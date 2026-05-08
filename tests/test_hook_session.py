from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_moonside.hook import main as hook_main
from codex_moonside.hook import session_allowed, wait_for_minimum_attention
from codex_moonside.state import read_state_file, write_session_lock


class HookSessionTests(unittest.TestCase):
    def test_permission_request_allowed_for_same_cwd_sibling_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = str(Path(tmpdir) / "lock.json")
            config = {
                "session_lock_enabled": True,
                "session_lock_file": lock_path,
                "permission_file": str(Path(tmpdir) / "permission.json"),
            }
            write_session_lock({"session_id": "s1", "cwd": "/tmp/project"}, lock_path)
            self.assertTrue(
                session_allowed(
                    config,
                    {"session_id": "s2", "cwd": "/tmp/project"},
                    "PermissionRequest",
                )
            )

    def test_stop_still_rejected_for_sibling_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = str(Path(tmpdir) / "lock.json")
            config = {
                "session_lock_enabled": True,
                "session_lock_file": lock_path,
                "permission_file": str(Path(tmpdir) / "permission.json"),
            }
            write_session_lock({"session_id": "s1", "cwd": "/tmp/project"}, lock_path)
            self.assertFalse(
                session_allowed(
                    config,
                    {"session_id": "s2", "cwd": "/tmp/project"},
                    "Stop",
                )
            )

    def test_wait_for_minimum_attention_sleeps_remaining_time(self) -> None:
        started = time.time()
        waited = wait_for_minimum_attention({"timestamp_epoch": started}, 0.02)

        self.assertGreater(waited, 0)
        self.assertGreaterEqual(time.time() - started, 0.02)

    def test_wait_for_minimum_attention_ignores_old_marker(self) -> None:
        waited = wait_for_minimum_attention({"timestamp_epoch": time.time() - 5}, 1.0)

        self.assertEqual(waited, 0.0)

    def test_post_tool_use_without_permission_marker_keeps_lamp_working(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state_path = tmp / "state.json"
            config_path = tmp / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "state_file": str(state_path),
                        "permission_file": str(tmp / "permission.json"),
                        "session_lock_file": str(tmp / "lock.json"),
                        "hook_log_file": str(tmp / "hook.log"),
                    }
                ),
                encoding="utf-8",
            )
            event = {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "session_id": "s1",
                "turn_id": "t1",
                "cwd": "/tmp/project",
                "tool_input": {"command": "echo ok"},
            }

            with patch("sys.stdin", io.StringIO(json.dumps(event))):
                status = hook_main(
                    [
                        "--config",
                        str(config_path),
                        "--permission-resolved-state",
                        "working",
                    ]
                )

            self.assertEqual(status, 0)
            payload = read_state_file(str(state_path))
            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertEqual(payload["state"], "working")
            self.assertEqual(payload["event"], "PostToolUse")


if __name__ == "__main__":
    unittest.main()
