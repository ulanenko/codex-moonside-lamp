from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codex_moonside.state import (
    atomic_write_state,
    build_session_lock,
    build_permission_marker,
    event_to_state,
    permission_marker_matches,
    payload_from_hook_input,
    read_state_file,
    session_lock_matches,
)


class StateTests(unittest.TestCase):
    def test_event_mapping(self) -> None:
        self.assertEqual(event_to_state("SessionStart"), "idle")
        self.assertEqual(event_to_state("UserPromptSubmit"), "working")
        self.assertEqual(event_to_state("PreToolUse"), "tool_running")
        self.assertEqual(event_to_state("PermissionRequest"), "attention")
        self.assertEqual(event_to_state("PostToolUse"), "tool_done")
        self.assertEqual(event_to_state("Stop"), "idle")
        self.assertIsNone(event_to_state("Unexpected"))

    def test_payload_from_codex_style_input(self) -> None:
        payload = payload_from_hook_input(
            json.dumps(
                {
                    "hookEventName": "PreToolUse",
                    "toolName": "Bash",
                    "sessionId": "s1",
                    "cwd": "/tmp/project",
                    "toolInput": {"command": "do not store this"},
                }
            )
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["state"], "tool_running")
        self.assertEqual(payload["event"], "PreToolUse")
        self.assertEqual(payload["tool"], "Bash")
        self.assertEqual(payload["session_id"], "s1")
        self.assertEqual(payload["cwd"], "/tmp/project")
        self.assertNotIn("toolInput", payload)

    def test_explicit_state_without_stdin(self) -> None:
        payload = payload_from_hook_input("", explicit_state="attention", cwd="/tmp/cwd")
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["state"], "attention")
        self.assertEqual(payload["cwd"], "/tmp/cwd")

    def test_atomic_write_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            atomic_write_state({"state": "working", "timestamp": "now"}, str(path))
            self.assertEqual(read_state_file(str(path))["state"], "working")  # type: ignore[index]

    def test_invalid_json_is_ignored_on_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text("{", encoding="utf-8")
            self.assertIsNone(read_state_file(str(path)))

    def test_permission_marker_matches_post_tool_use(self) -> None:
        marker = build_permission_marker(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": "Bash",
                "session_id": "s1",
                "turn_id": "t1",
                "cwd": "/tmp/project",
                "tool_input": {"command": "touch /tmp/ok", "description": "Need approval"},
            }
        )
        self.assertTrue(
            permission_marker_matches(
                marker,
                {
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Bash",
                    "session_id": "s1",
                    "turn_id": "t1",
                    "cwd": "/tmp/project",
                    "tool_use_id": "u1",
                    "tool_input": {"command": "touch /tmp/ok"},
                },
            )
        )
        self.assertFalse(
            permission_marker_matches(
                marker,
                {
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Bash",
                    "session_id": "s1",
                    "turn_id": "other",
                    "cwd": "/tmp/project",
                    "tool_input": {"command": "touch /tmp/ok"},
                },
            )
        )
        self.assertFalse(
            permission_marker_matches(
                marker,
                {
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Bash",
                    "session_id": "s1",
                    "turn_id": "t1",
                    "cwd": "/tmp/project",
                    "tool_input": {"command": "touch /tmp/different"},
                },
            )
        )

    def test_permission_marker_requires_some_identity_match(self) -> None:
        self.assertFalse(permission_marker_matches({"tool": "Bash"}, {"tool_name": "Bash"}))
        self.assertTrue(
            permission_marker_matches(
                {"tool": "Bash", "cwd": "/tmp/a"},
                {"tool_name": "Bash", "cwd": "/tmp/a"},
            )
        )

    def test_session_lock_matches_session_id_first(self) -> None:
        lock = build_session_lock({"session_id": "s1", "turn_id": "t1", "cwd": "/tmp/a"})
        self.assertTrue(session_lock_matches(lock, {"session_id": "s1", "turn_id": "other", "cwd": "/tmp/b"}))
        self.assertFalse(session_lock_matches(lock, {"session_id": "s2", "turn_id": "t1", "cwd": "/tmp/a"}))

    def test_session_lock_falls_back_to_turn_or_cwd(self) -> None:
        self.assertTrue(session_lock_matches({"turn_id": "t1"}, {"turn_id": "t1"}))
        self.assertFalse(session_lock_matches({"turn_id": "t1"}, {"turn_id": "t2"}))
        self.assertTrue(session_lock_matches({"cwd": "/tmp/a"}, {"cwd": "/tmp/a"}))
        self.assertFalse(session_lock_matches({"cwd": "/tmp/a"}, {"cwd": "/tmp/b"}))


if __name__ == "__main__":
    unittest.main()
