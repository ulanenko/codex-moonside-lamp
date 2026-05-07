from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from .config import VALID_STATES, commands_for_state, load_config
from .logging_utils import setup_file_logger
from .moonside_ble import BleakUnavailable, MoonsideBLE, is_bluetooth_unavailable
from .state import read_state_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent BLE daemon for Codex Moonside lamp status.")
    parser.add_argument("--config", help="Config file path.")
    parser.add_argument("--scan", action="store_true", help="List nearby BLE devices and exit.")
    parser.add_argument("--test-state", choices=sorted(VALID_STATES), help="Send one configured state and exit.")
    parser.add_argument(
        "--raw-command",
        action="append",
        help="Send one raw Moonside command. May be repeated, e.g. --raw-command LEDON --raw-command COLOR255000000.",
    )
    parser.add_argument("--debug", action="store_true", help="Log debug details.")
    return parser


def make_controller(config: dict[str, Any], logger: Any) -> MoonsideBLE:
    return MoonsideBLE(
        ble_address=config.get("ble_address"),
        name_contains=config.get("lamp_name_contains"),
        command_delay_seconds=float(config.get("command_delay_seconds", 0.08)),
        command_suffix=str(config.get("command_suffix", "")),
        write_response=bool(config.get("write_response", True)),
        skip_redundant_commands=bool(config.get("skip_redundant_commands", True)),
        logger=logger,
    )


async def run_scan(config: dict[str, Any], logger: Any) -> int:
    controller = make_controller(config, logger)
    devices = await controller.scan(timeout=float(config.get("scan_timeout_seconds", 5)))
    if not devices:
        print("No BLE devices found.")
        return 0

    needle = (config.get("lamp_name_contains") or "").lower()
    for device in devices:
        marker = "*" if needle and needle in (device.name or "").lower() else " "
        rssi = "" if device.rssi is None else f" rssi={device.rssi}"
        print(f"{marker} {device.name or '<unnamed>'}  {device.address}{rssi}")
    return 0


async def run_test_state(config: dict[str, Any], logger: Any, state: str) -> int:
    controller = make_controller(config, logger)
    try:
        print(f"Connecting to {config.get('ble_address') or config.get('lamp_name_contains')}...", flush=True)
        await controller.connect(timeout=float(config.get("scan_timeout_seconds", 5)))
        print(f"Connected. Sending state: {state}", flush=True)
        await send_state(controller, config, state, logger)
        print("Done.", flush=True)
    finally:
        await controller.disconnect()
    return 0


async def run_raw_commands(config: dict[str, Any], logger: Any, commands: list[str]) -> int:
    controller = make_controller(config, logger)
    try:
        print(f"Connecting to {config.get('ble_address') or config.get('lamp_name_contains')}...", flush=True)
        await controller.connect(timeout=float(config.get("scan_timeout_seconds", 5)))
        print("Connected. Sending raw commands:", ", ".join(commands), flush=True)
        await controller.send_commands(commands, optimize=False)
        print("Done.", flush=True)
    finally:
        await controller.disconnect()
    return 0


async def send_state(controller: MoonsideBLE, config: dict[str, Any], state: str, logger: Any) -> None:
    commands = commands_for_state(config, state)
    if not commands:
        logger.warning("No commands configured for state=%s", state)
        return
    logger.info("Applying state=%s commands=%s", state, commands)
    try:
        await controller.send_commands(commands)
    except Exception as exc:
        if not is_recoverable_ble_send_error(exc):
            raise
        logger.warning("BLE send failed for state=%s; reconnecting and retrying: %s", state, exc)
        try:
            await controller.disconnect()
        except Exception as disconnect_exc:
            logger.warning("BLE disconnect during recovery failed: %s", disconnect_exc)
        await controller.connect(timeout=float(config.get("scan_timeout_seconds", 5)))
        await controller.send_commands(commands, optimize=False)


def is_recoverable_ble_send_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    if "not connected" in message or "disconnected" in message:
        return True
    return exc.__class__.__name__ in {"BleakError", "BleakDeviceNotFoundError"}


def state_file_fingerprint(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def return_target_for_transient(
    state_config: dict[str, Any],
    current_state: str | None,
    previous_state: str | None,
) -> str:
    configured_target = state_config.get("return_to_state")
    if isinstance(configured_target, str) and configured_target in VALID_STATES:
        return configured_target
    if current_state == "tool_running" and previous_state:
        return previous_state
    return current_state or previous_state or "idle"


def should_apply_ambient_timeout(
    *,
    current_state: str | None,
    ambient_applied: bool,
    last_activity_monotonic: float,
    now_monotonic: float,
    timeout_seconds: float,
) -> bool:
    if timeout_seconds <= 0 or ambient_applied:
        return False
    if current_state != "idle":
        return False
    return now_monotonic - last_activity_monotonic >= timeout_seconds


def configured_process_names(config: dict[str, Any]) -> list[str]:
    raw_names = config.get("codex_process_names", [])
    if isinstance(raw_names, str):
        raw_names = [raw_names]
    if not isinstance(raw_names, list):
        return []
    return [str(name).strip() for name in raw_names if str(name).strip()]


def process_name_matches(command: str, expected_name: str) -> bool:
    command = command.strip()
    expected_name = expected_name.strip()
    if not command or not expected_name:
        return False

    basename = Path(command).name
    if basename == expected_name:
        return True
    if basename.startswith(f"{expected_name} Helper"):
        return True
    return f"/{expected_name}.app/" in command


def is_configured_process_running(process_names: list[str], process_lines: list[str]) -> bool:
    if not process_names:
        return False
    return any(
        process_name_matches(line, process_name)
        for line in process_lines
        for process_name in process_names
    )


def read_process_commands() -> list[str]:
    result = subprocess.run(
        ["/bin/ps", "-axo", "comm="],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not list running processes")
    return result.stdout.splitlines()


def should_apply_codex_process_missing_state(
    *,
    tracking_enabled: bool,
    process_running: bool,
    missing_state_applied: bool,
    last_seen_monotonic: float,
    now_monotonic: float,
    missing_seconds: float,
) -> bool:
    if not tracking_enabled or process_running or missing_state_applied:
        return False
    if missing_seconds <= 0:
        return True
    return now_monotonic - last_seen_monotonic >= missing_seconds


async def watch_state_file(controller: MoonsideBLE, config: dict[str, Any], logger: Any) -> None:
    state_file = config["state_file"]
    poll_interval = float(config.get("poll_interval_seconds", 0.2))
    ambient_after_idle_seconds = float(config.get("ambient_after_idle_seconds", 0))
    ambient_state = str(config.get("ambient_state", "ambient"))
    process_tracking_enabled = bool(config.get("codex_process_tracking_enabled", False))
    process_names = configured_process_names(config)
    process_poll_interval = float(config.get("codex_process_poll_interval_seconds", 5))
    process_missing_seconds = float(config.get("codex_process_missing_seconds", 60))
    process_missing_state = str(config.get("codex_process_missing_state", "off"))
    process_return_state = str(config.get("codex_process_return_state", "idle"))
    last_fingerprint: str | None = None
    current_state: str | None = None
    previous_state: str | None = None
    last_activity_monotonic = time.monotonic()
    ambient_applied = False
    last_process_check_monotonic = 0.0
    last_process_seen_monotonic = time.monotonic()
    last_process_running = True
    process_missing_state_applied = False

    if process_tracking_enabled and not process_names:
        logger.warning("Codex process tracking is enabled, but codex_process_names is empty; disabling tracking")
        process_tracking_enabled = False
    if process_missing_state not in VALID_STATES:
        logger.warning("Unknown codex_process_missing_state=%s; using off", process_missing_state)
        process_missing_state = "off"
    if process_return_state not in VALID_STATES:
        logger.warning("Unknown codex_process_return_state=%s; using idle", process_return_state)
        process_return_state = "idle"

    async def apply_ambient_if_due() -> bool:
        nonlocal current_state, previous_state, ambient_applied
        if not should_apply_ambient_timeout(
            current_state=current_state,
            ambient_applied=ambient_applied,
            last_activity_monotonic=last_activity_monotonic,
            now_monotonic=time.monotonic(),
            timeout_seconds=ambient_after_idle_seconds,
        ):
            return False

        logger.info(
            "No Codex activity for %.1fs after idle; applying ambient state=%s",
            ambient_after_idle_seconds,
            ambient_state,
        )
        await send_state(controller, config, ambient_state, logger)
        previous_state = current_state
        current_state = ambient_state
        ambient_applied = True
        return True

    async def codex_process_allows_state_updates() -> bool:
        nonlocal current_state, previous_state, last_activity_monotonic, ambient_applied
        nonlocal last_process_check_monotonic, last_process_seen_monotonic, last_process_running
        nonlocal process_missing_state_applied

        if not process_tracking_enabled:
            return True

        now = time.monotonic()
        if now - last_process_check_monotonic >= process_poll_interval:
            last_process_check_monotonic = now
            try:
                last_process_running = is_configured_process_running(process_names, read_process_commands())
            except Exception as exc:
                logger.warning("Could not inspect Codex process state: %s", exc)
                return True

            if last_process_running:
                last_process_seen_monotonic = now
                if process_missing_state_applied:
                    logger.info("Codex process detected; applying return state=%s", process_return_state)
                    previous_state = current_state
                    current_state = process_return_state
                    last_activity_monotonic = now
                    ambient_applied = False
                    process_missing_state_applied = False
                    await send_state(controller, config, process_return_state, logger)
                return True

        if should_apply_codex_process_missing_state(
            tracking_enabled=process_tracking_enabled,
            process_running=last_process_running,
            missing_state_applied=process_missing_state_applied,
            last_seen_monotonic=last_process_seen_monotonic,
            now_monotonic=now,
            missing_seconds=process_missing_seconds,
        ):
            logger.info(
                "Codex process not seen for %.1fs; applying state=%s",
                process_missing_seconds,
                process_missing_state,
            )
            await send_state(controller, config, process_missing_state, logger)
            previous_state = current_state
            current_state = process_missing_state
            ambient_applied = False
            process_missing_state_applied = True
            return False

        return not process_missing_state_applied

    logger.info("Watching state file: %s", state_file)
    while True:
        if not await codex_process_allows_state_updates():
            await asyncio.sleep(poll_interval)
            continue

        payload = read_state_file(state_file)
        if payload is None:
            await apply_ambient_if_due()
            await asyncio.sleep(poll_interval)
            continue

        fingerprint = state_file_fingerprint(payload)
        if fingerprint == last_fingerprint:
            await apply_ambient_if_due()
            await asyncio.sleep(poll_interval)
            continue
        last_fingerprint = fingerprint
        last_activity_monotonic = time.monotonic()
        ambient_applied = False

        state = str(payload["state"])
        state_config = config.get("states", {}).get(state, {})
        logger.info("Observed state=%s event=%s tool=%s", state, payload.get("event"), payload.get("tool"))

        if state_config.get("return_to_previous"):
            await send_state(controller, config, state, logger)
            duration = float(state_config.get("duration_seconds", 0.4))
            if duration > 0:
                await asyncio.sleep(duration)
            target = return_target_for_transient(state_config, current_state, previous_state)
            if target != state:
                await send_state(controller, config, target, logger)
                current_state = target
            await asyncio.sleep(poll_interval)
            continue

        if state == current_state:
            await asyncio.sleep(poll_interval)
            continue

        previous_state = current_state
        current_state = state
        await send_state(controller, config, state, logger)
        await asyncio.sleep(poll_interval)


async def run_daemon(config: dict[str, Any], logger: Any) -> int:
    reconnect_interval = float(config.get("reconnect_interval_seconds", 3))
    while True:
        controller = make_controller(config, logger)
        try:
            await controller.connect(timeout=float(config.get("scan_timeout_seconds", 5)))
            await watch_state_file(controller, config, logger)
        except asyncio.CancelledError:
            raise
        except BleakUnavailable:
            raise
        except Exception as exc:
            if is_bluetooth_unavailable(exc):
                raise
            logger.exception("Daemon connection loop failed; reconnecting in %.1fs: %s", reconnect_interval, exc)
            await asyncio.sleep(reconnect_interval)
        finally:
            await controller.disconnect()


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    logger = setup_file_logger("codex_moonside.daemon", config.get("log_file"), debug=args.debug, console=True)

    try:
        if args.scan:
            return await run_scan(config, logger)
        if args.test_state:
            return await run_test_state(config, logger, args.test_state)
        if args.raw_command:
            return await run_raw_commands(config, logger, args.raw_command)
        return await run_daemon(config, logger)
    except BleakUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        if is_bluetooth_unavailable(exc):
            print(f"Bluetooth is not available to Bleak: {exc}", file=sys.stderr)
            return 1
        raise


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
