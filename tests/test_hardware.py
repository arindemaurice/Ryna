"""Tests for hardware detection and hardware-aware model tier selection."""

from __future__ import annotations

import unittest

from core.python.hardware import detect_hardware, select_hardware_tier


class TestHardwareMonitor(unittest.TestCase):
    def test_detect_hardware_returns_real_metrics(self) -> None:
        hw = detect_hardware()
        self.assertGreaterEqual(hw.cpu_logical_cores, 1)
        self.assertGreater(hw.disk_total_gb, 0)
        self.assertGreater(hw.ram_total_gb, 0)
        self.assertIn(hw.recommended_tier, ("low", "medium", "high", "workstation"))
        self.assertTrue(len(hw.recommended_local_models) >= 1)

    def test_tier_selection_scales_with_ram(self) -> None:
        low = select_hardware_tier(4.0)
        med = select_hardware_tier(16.0)
        high = select_hardware_tier(32.0)
        ws = select_hardware_tier(128.0)

        self.assertEqual(low["tier"], "low")
        self.assertTrue(low["reduced_animations"])
        self.assertEqual(med["tier"], "medium")
        self.assertEqual(high["tier"], "high")
        self.assertEqual(ws["tier"], "workstation")


if __name__ == "__main__":
    unittest.main()
