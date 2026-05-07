from __future__ import annotations

import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

from codex_moonside.doctor import (
    check_hook_trust,
    executable_for_command,
    run_direct_hook_smoke_test,
    toml_bool_in_section,
)
from codex_moonside.hooks_config import trusted_hook_state_entries


class DoctorTests(unittest.TestCase):
    def test_toml_bool_in_section_reads_feature_flags(self) -> None:
        text = """
[features]
hooks = true
codex_hooks = false # deprecated

[other]
hooks = false
"""
        self.assertTrue(toml_bool_in_section(text, "features", "hooks"))
        self.assertFalse(toml_bool_in_section(text, "features", "codex_hooks"))
        self.assertIsNone(toml_bool_in_section(text, "features", "missing"))

    def test_executable_for_command_handles_quoted_absolute_path(self) -> None:
        self.assertEqual(executable_for_command("'/bin/echo' hello"), "/bin/echo")
        self.assertIsNone(executable_for_command("'/definitely/missing' hello"))

    def test_check_hook_trust_detects_and_fixes_missing_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            hooks_path = tmp / "hooks.json"
            config_path = tmp / "config.toml"
            hooks = {
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [{"type": "command", "command": "/bin/echo ok"}]},
                    ],
                },
            }
            hooks_path.write_text(json.dumps(hooks), encoding="utf-8")
            config_path.write_text("[features]\nhooks = true\n", encoding="utf-8")

            failed = check_hook_trust(hooks, hooks_path, config_path, config_path.read_text(), fix=False)
            self.assertEqual(failed.status, "fail")

            fixed = check_hook_trust(hooks, hooks_path, config_path, config_path.read_text(), fix=True)
            self.assertEqual(fixed.status, "ok")
            self.assertEqual(len(trusted_hook_state_entries(hooks, hooks_path)), 1)

    def test_run_direct_hook_smoke_test_writes_temp_state(self) -> None:
        command = f"{shlex.quote(sys.executable)} -m codex_moonside.hook"
        check = run_direct_hook_smoke_test({"hooks": {}}, command)
        self.assertEqual(check.status, "ok")


if __name__ == "__main__":
    unittest.main()
