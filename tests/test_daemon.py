from __future__ import annotations

import asyncio
import unittest

from codex_moonside.daemon import (
    configured_process_names,
    is_configured_process_running,
    is_recoverable_ble_send_error,
    process_name_matches,
    send_state,
    should_apply_codex_process_missing_state,
    should_apply_ambient_timeout,
)


class NullLogger:
    def info(self, *_args: object) -> None:
        pass

    def warning(self, *_args: object) -> None:
        pass


class RecoveringController:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bool]] = []
        self.connected_with: list[float] = []
        self.disconnects = 0

    async def send_commands(self, commands: list[str], *, optimize: bool = True) -> None:
        self.calls.append((commands, optimize))
        if len(self.calls) == 1:
            raise RuntimeError("BLE client is not connected")

    async def disconnect(self) -> None:
        self.disconnects += 1

    async def connect(self, timeout: float = 5.0) -> None:
        self.connected_with.append(timeout)


class FailingController:
    async def send_commands(self, commands: list[str], *, optimize: bool = True) -> None:
        raise ValueError("bad command")


class DaemonSendStateTests(unittest.TestCase):
    def test_send_state_reconnects_and_retries_on_stale_ble_connection(self) -> None:
        controller = RecoveringController()
        config = {
            "scan_timeout_seconds": 7,
            "states": {"working": {"commands": ["LEDON", "BRIGH060", "THEME.BEAT2.255,255,255"]}},
        }

        asyncio.run(send_state(controller, config, "working", NullLogger()))  # type: ignore[arg-type]

        self.assertEqual(controller.disconnects, 1)
        self.assertEqual(controller.connected_with, [7.0])
        self.assertEqual(
            controller.calls,
            [
                (["LEDON", "BRIGH060", "THEME.BEAT2.255,255,255"], True),
                (["LEDON", "BRIGH060", "THEME.BEAT2.255,255,255"], False),
            ],
        )

    def test_send_state_does_not_retry_unrelated_errors(self) -> None:
        config = {"states": {"working": {"commands": ["BAD"]}}}

        with self.assertRaises(ValueError):
            asyncio.run(send_state(FailingController(), config, "working", NullLogger()))  # type: ignore[arg-type]

    def test_recoverable_ble_send_error_detection(self) -> None:
        self.assertTrue(is_recoverable_ble_send_error(RuntimeError("BLE client is not connected")))
        self.assertTrue(is_recoverable_ble_send_error(RuntimeError("Peripheral disconnected")))
        self.assertFalse(is_recoverable_ble_send_error(ValueError("bad command")))

    def test_ambient_timeout_applies_only_after_idle_quiet_period(self) -> None:
        self.assertTrue(
            should_apply_ambient_timeout(
                current_state="idle",
                ambient_applied=False,
                last_activity_monotonic=100,
                now_monotonic=1900,
                timeout_seconds=1800,
            )
        )

    def test_configured_process_names_accepts_string_or_list(self) -> None:
        self.assertEqual(configured_process_names({"codex_process_names": "Codex"}), ["Codex"])
        self.assertEqual(configured_process_names({"codex_process_names": ["Codex", " "]}), ["Codex"])
        self.assertEqual(configured_process_names({"codex_process_names": 1}), [])

    def test_process_name_matching_avoids_codex_moonside_helper(self) -> None:
        self.assertTrue(process_name_matches("/Applications/Codex.app/Contents/MacOS/Codex", "Codex"))
        self.assertTrue(process_name_matches("/Applications/Codex.app/Contents/Frameworks/Codex Helper", "Codex"))
        self.assertFalse(process_name_matches("/tmp/CodexMoonsideMenuBar", "Codex"))
        self.assertFalse(process_name_matches("/usr/local/bin/codex-moonside-daemon", "Codex"))

    def test_configured_process_running_checks_all_process_lines(self) -> None:
        self.assertTrue(
            is_configured_process_running(
                ["Codex"],
                ["/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder", "/Applications/Codex.app/Contents/MacOS/Codex"],
            )
        )
        self.assertFalse(is_configured_process_running(["Codex"], ["/tmp/CodexMoonsideMenuBar"]))

    def test_process_missing_state_waits_for_grace_period(self) -> None:
        self.assertTrue(
            should_apply_codex_process_missing_state(
                tracking_enabled=True,
                process_running=False,
                missing_state_applied=False,
                last_seen_monotonic=100,
                now_monotonic=160,
                missing_seconds=60,
            )
        )
        self.assertFalse(
            should_apply_codex_process_missing_state(
                tracking_enabled=True,
                process_running=False,
                missing_state_applied=False,
                last_seen_monotonic=100,
                now_monotonic=159,
                missing_seconds=60,
            )
        )
        self.assertFalse(
            should_apply_codex_process_missing_state(
                tracking_enabled=True,
                process_running=True,
                missing_state_applied=False,
                last_seen_monotonic=100,
                now_monotonic=200,
                missing_seconds=60,
            )
        )
        self.assertFalse(
            should_apply_ambient_timeout(
                current_state="working",
                ambient_applied=False,
                last_activity_monotonic=100,
                now_monotonic=1900,
                timeout_seconds=1800,
            )
        )
        self.assertFalse(
            should_apply_ambient_timeout(
                current_state="idle",
                ambient_applied=True,
                last_activity_monotonic=100,
                now_monotonic=1900,
                timeout_seconds=1800,
            )
        )


if __name__ == "__main__":
    unittest.main()
