from __future__ import annotations

import unittest

from codex_moonside.moonside_ble import MoonsideBLE


class BLECommandOptimizationTests(unittest.TestCase):
    def test_redundant_power_and_brightness_detection(self) -> None:
        controller = MoonsideBLE()

        self.assertFalse(controller._is_redundant_command("LEDON"))
        controller._remember_command("LEDON")
        self.assertTrue(controller._is_redundant_command("LEDON"))
        self.assertFalse(controller._is_redundant_command("LEDOFF"))

        self.assertFalse(controller._is_redundant_command("BRIGH060"))
        controller._remember_command("BRIGH060")
        self.assertTrue(controller._is_redundant_command("BRIGH060"))
        self.assertFalse(controller._is_redundant_command("BRIGH070"))
        self.assertFalse(controller._is_redundant_command("COLOR255120040"))


if __name__ == "__main__":
    unittest.main()
