from __future__ import annotations

import argparse
import asyncio
import json
import sys
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
    await controller.send_commands(commands)


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


async def watch_state_file(controller: MoonsideBLE, config: dict[str, Any], logger: Any) -> None:
    state_file = config["state_file"]
    poll_interval = float(config.get("poll_interval_seconds", 0.2))
    last_fingerprint: str | None = None
    current_state: str | None = None
    previous_state: str | None = None

    logger.info("Watching state file: %s", state_file)
    while True:
        payload = read_state_file(state_file)
        if payload is None:
            await asyncio.sleep(poll_interval)
            continue

        fingerprint = state_file_fingerprint(payload)
        if fingerprint == last_fingerprint:
            await asyncio.sleep(poll_interval)
            continue
        last_fingerprint = fingerprint

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
