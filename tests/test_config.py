from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codex_moonside.config import commands_for_state, deep_merge, load_config


class ConfigTests(unittest.TestCase):
    def test_deep_merge_preserves_default_state_commands(self) -> None:
        merged = deep_merge(
            {"states": {"idle": {"commands": ["LEDON"], "duration_seconds": 1}}, "a": 1},
            {"states": {"idle": {"commands": ["LEDOFF"]}}},
        )
        self.assertEqual(merged["states"]["idle"]["commands"], ["LEDOFF"])
        self.assertEqual(merged["states"]["idle"]["duration_seconds"], 1)
        self.assertEqual(merged["a"], 1)

    def test_load_config_with_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "state_file": str(Path(tmpdir) / "state.json"),
                        "states": {"attention": {"commands": ["COLOR001002003"]}},
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(str(config_path))
            self.assertEqual(config["state_file"], str(Path(tmpdir) / "state.json"))
            self.assertEqual(commands_for_state(config, "attention"), ["COLOR001002003"])
            self.assertEqual(commands_for_state(config, "idle"), ["LEDON", "BRIGH060", "COLOR255120040"])


if __name__ == "__main__":
    unittest.main()
