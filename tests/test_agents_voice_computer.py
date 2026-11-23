"""Tests for 8-state tasks, crash recovery, 3-tier permissions, 13 robot states, tools, computer control, and Win32 mutex."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.engine import AgentTaskEngine
from agents.tools import ToolRegistry, safe_calculate
from ai.config import RynaConfig
from app.desktop.single_instance import SingleInstanceGuard
from computer.controller import ComputerController
from database.store import RynaDatabase
from security.permissions import PermissionEngine
from voice.manager import ROBOT_STATES, VoiceManager


class TestAgentsVoiceAndComputer(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = RynaConfig.load(overrides={"db_path": Path(self._tmp.name) / "agt.db"})
        self.db = RynaDatabase(self.cfg.db_path, self.cfg.default_settings)
        self.perms = PermissionEngine(self.db)
        self.comp = ComputerController(self.db)
        self.tools = ToolRegistry(self.cfg, self.db, self.comp)
        self.online = False
        self.tasks = AgentTaskEngine(self.db, self.perms, self.tools, connectivity_checker=lambda: self.online)
        self.voice = VoiceManager(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_8_state_task_approval_offline_queue_and_crash_recovery(self) -> None:
        # Level 2 approval required
        t = self.tasks.create_task("Confirm Math", "calc: 9 * 9", permission_level=2)
        self.assertEqual(t["status"], "planned")
        w = self.tasks.run_task(t["id"], approved=False)
        self.assertEqual(w["status"], "waiting")
        ok = self.tasks.run_task(t["id"], approved=True)
        self.assertEqual(ok["status"], "completed")
        self.assertIn("81", ok["result_summary"])

        # Offline queue & online resume
        t2 = self.tasks.create_task(
            "Hybrid",
            "Offline then online",
            steps=[
                {"description": "Off", "tool_name": "calculate", "tool_args": {"expression": "2+2"}, "permission_level": 1, "requires_internet": False},
                {"description": "On", "tool_name": "create_note", "tool_args": {"title": "Online", "content": "Done"}, "permission_level": 1, "requires_internet": True},
            ],
        )
        p = self.tasks.run_task(t2["id"])
        self.assertEqual(p["status"], "paused")
        self.online = True
        fin = self.tasks.run_task(t2["id"])
        self.assertEqual(fin["status"], "completed")

        # Crash recovery prevents blind retry
        t3 = self.tasks.create_task("Crashed", "calc: 5+5")
        self.tasks._set_task(t3["id"], "running", "pending")
        self.tasks._set_step(t3["steps"][0]["id"], "running", "")
        rec = self.tasks.recover_after_restart()
        self.assertEqual(len(rec), 1)
        after = self.tasks.get_task(t3["id"])
        self.assertEqual(after["status"], "paused")
        self.assertEqual(after["outcome_status"], "uncertain")
        self.assertEqual(self.tasks.run_task(t3["id"], approved=False)["status"], "waiting")

    def test_13_robot_states_tools_computer_and_mutex(self) -> None:
        self.assertEqual(len(ROBOT_STATES), 13)
        for st in ROBOT_STATES:
            self.assertEqual(self.voice.set_state(st), st)
        res = self.voice.interrupt("Barge-in")
        self.assertEqual(res["new_state"], "interrupted")

        self.assertEqual(safe_calculate("14 * 3"), 42)
        with self.assertRaises(ValueError):
            safe_calculate("__import__('os').system('dir')")
        with self.assertRaises(PermissionError):
            self.tools.execute("file_read", {"path": "..\\..\\Windows\\win.ini"})
        with self.assertRaises(PermissionError):
            self.comp.launch_application("notepad")  # disabled by default

        g1, g2 = SingleInstanceGuard("RYNA_CLEAN_TEST_MUTEX"), SingleInstanceGuard("RYNA_CLEAN_TEST_MUTEX")
        self.assertTrue(g1.acquire())
        try:
            self.assertFalse(g2.acquire())
        finally:
            g1.release()


if __name__ == "__main__":
    unittest.main()
