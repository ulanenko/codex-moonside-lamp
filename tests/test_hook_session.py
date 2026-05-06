from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_moonside.hook import session_allowed
from codex_moonside.state import write_session_lock


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


if __name__ == "__main__":
    unittest.main()
