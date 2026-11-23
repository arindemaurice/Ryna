"""Tests for sandboxed local tools, voice interruption state machine, and single-instance mutex."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from apps.windows_companion.single_instance import SingleInstanceGuard
from core.python.config import RynaConfig
from core.python.database import RynaDatabase
from services.tools.local_tools import LocalToolRegistry, safe_calculate
from services.voice.pipeline import VoicePipeline


class TestToolsVoiceAndSingleInstance(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = RynaConfig.load(overrides={"db_path": Path(self._tmp.name) / "tools.db"})
        self.db = RynaDatabase(self.cfg.db_path, self.cfg.default_settings)
        self.tools = LocalToolRegistry(self.cfg, self.db)
        self.voice = VoicePipeline(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_safe_calculator_and_injection_rejection(self) -> None:
        self.assertEqual(safe_calculate("(12 + 8) * 3 / 2"), 30)
        with self.assertRaises(ValueError):
            safe_calculate("__import__('os').system('echo pwned')")

    def test_file_tools_enforce_root_sandbox(self) -> None:
        found = self.tools.execute("file_search", {"query": "readme"})
        self.assertGreaterEqual(found["count"], 1)

        with self.assertRaises(PermissionError):
            self.tools.execute("file_read", {"path": "..\\..\\Windows\\System32\\drivers\\etc\\hosts"})

    def test_voice_pipeline_barge_in_and_backend_probe(self) -> None:
        st = self.voice.status()
        self.assertEqual(st["mode"], "push_to_talk")
        self.assertTrue(len(st["backends"]) >= 5)

        self.voice.begin_generation("gen_1")
        self.assertEqual(self.voice.get_state(), "processing")
        self.voice.set_state("speaking")

        self.db.set_setting("voice.enabled", True)
        res = self.voice.interrupt("User started talking")
        self.assertTrue(res["interrupted"])
        self.assertEqual(res["previous_state"], "speaking")
        self.assertEqual(res["new_state"], "listening")
        self.assertTrue(self.voice.is_interrupted())

    def test_single_instance_guard_blocks_duplicate(self) -> None:
        g1 = SingleInstanceGuard("RYNA_UNIT_TEST_MUTEX_010")
        g2 = SingleInstanceGuard("RYNA_UNIT_TEST_MUTEX_010")
        self.assertTrue(g1.acquire())
        try:
            self.assertFalse(g2.acquire())
        finally:
            g1.release()
        self.assertTrue(g2.acquire())
        g2.release()


if __name__ == "__main__":
    unittest.main()
