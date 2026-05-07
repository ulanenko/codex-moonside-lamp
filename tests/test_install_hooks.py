from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from install_hooks import (
    load_hooks_template,
    trusted_hook_state_entries,
    upsert_hook_trust_entries,
    write_hook_trust,
)


class InstallHooksTests(unittest.TestCase):
    def test_trusted_hook_state_entries_match_generated_hooks(self) -> None:
        hooks = load_hooks_template("/tmp/codex-moonside-hook")
        entries = trusted_hook_state_entries(hooks, Path("/tmp/hooks.json"))

        self.assertEqual(len(entries), 5)
        self.assertEqual(entries[0][0], "/tmp/hooks.json:session_start:0:0")
        self.assertTrue(entries[0][1].startswith("sha256:"))

    def test_upsert_hook_trust_entries_adds_and_replaces_hashes(self) -> None:
        entries = [("/tmp/hooks.json:user_prompt_submit:0:0", "sha256:new")]
        config = '[features]\nhooks = true\n\n[hooks.state."/tmp/hooks.json:user_prompt_submit:0:0"]\ntrusted_hash = "sha256:old"\n'

        updated = upsert_hook_trust_entries(config, entries)

        self.assertIn('trusted_hash = "sha256:new"', updated)
        self.assertNotIn("sha256:old", updated)

    def test_write_hook_trust_uses_installed_hooks_source_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            hooks_path = tmp / "hooks.json"
            config_path = tmp / "config.toml"
            hooks = load_hooks_template("/tmp/codex-moonside-hook")
            hooks_path.write_text(json.dumps(hooks), encoding="utf-8")
            config_path.write_text("[features]\nhooks = true\n", encoding="utf-8")

            count = write_hook_trust(config_path, hooks_path, hooks)

            self.assertEqual(count, 5)
            self.assertIn(f'[hooks.state."{hooks_path}:stop:0:0"]', config_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
