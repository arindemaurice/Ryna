"""Tests for 3-tier permissions, emergency stop, task execution, crash recovery, and offline queueing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.python.config import RynaConfig
from core.python.database import RynaDatabase
from services.agents.permissions import PermissionEngine
from services.agents.task_engine import TaskEngine
from services.tools.local_tools import LocalToolRegistry


class TestPermissionsAndTasks(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = RynaConfig.load(overrides={"db_path": Path(self._tmp.name) / "tasks.db"})
        self.db = RynaDatabase(self.cfg.db_path, self.cfg.default_settings)
        self.perms = PermissionEngine(self.db)
        self.tools = LocalToolRegistry(self.cfg, self.db)
        self.online_flag = False
        self.tasks = TaskEngine(self.db, self.perms, self.tools, connectivity_checker=lambda: self.online_flag)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_level1_level2_level3_permissions(self) -> None:
        d1 = self.perms.evaluate("calculate", "2+2")
        self.assertTrue(d1.allowed)

        d2 = self.perms.evaluate("send_email", "alice@example.com", explicitly_approved=False)
        self.assertFalse(d2.allowed)
        self.assertTrue(d2.requires_confirmation)

        d2_ok = self.perms.evaluate("send_email", "alice@example.com", explicitly_approved=True)
        self.assertTrue(d2_ok.allowed)

        d3 = self.perms.evaluate("delete_permanent", "C:\\Windows", explicitly_approved=True)
        self.assertFalse(d3.allowed)
        self.assertTrue(d3.blocked)

    def test_level2_task_waits_for_approval_then_completes(self) -> None:
        task = self.tasks.create_task(
            title="Approved Math",
            goal="calc: 12 * 12",
            permission_level=2,
            steps=[
                {
                    "description": "Compute 12 * 12 with confirmation",
                    "tool_name": "calculate",
                    "tool_args": {"expression": "12 * 12"},
                    "permission_level": 2,
                    "requires_internet": False,
                }
            ],
        )
        res1 = self.tasks.run_task(task["id"], approved=False)
        self.assertEqual(res1["status"], "waiting_for_approval")

        res2 = self.tasks.run_task(task["id"], approved=True)
        self.assertEqual(res2["status"], "completed")
        self.assertEqual(res2["outcome_status"], "succeeded")
        self.assertIn("144", res2["result_summary"])

    def test_offline_queue_and_online_resume(self) -> None:
        self.online_flag = False
        task = self.tasks.create_task(
            title="Hybrid Offline-Online Task",
            goal="Calculate offline then run online step",
            steps=[
                {
                    "description": "Offline calculation",
                    "tool_name": "calculate",
                    "tool_args": {"expression": "10 + 5"},
                    "permission_level": 1,
                    "requires_internet": False,
                },
                {
                    "description": "Online step queued while offline",
                    "tool_name": "create_note",
                    "tool_args": {"title": "Synced", "content": "Completed online"},
                    "permission_level": 1,
                    "requires_internet": True,
                },
            ],
        )
        paused = self.tasks.run_task(task["id"])
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["outcome_status"], "partially_completed")
        self.assertEqual(paused["steps"][0]["status"], "completed")
        self.assertEqual(paused["steps"][1]["status"], "pending")

        # Connectivity returns -> resumes safely without re-running step 1
        self.online_flag = True
        finished = self.tasks.run_task(task["id"])
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["outcome_status"], "succeeded")

    def test_crash_recovery_marks_running_task_uncertain(self) -> None:
        task = self.tasks.create_task(title="Interrupted Task", goal="calc: 3 * 9")
        # Simulate crash mid-execution
        self.tasks._set_task_state(task["id"], "running", "pending")
        self.tasks._set_step_state(task["steps"][0]["id"], "running", "")

        recovered = self.tasks.recover_after_restart()
        self.assertEqual(len(recovered), 1)

        after = self.tasks.get_task(task["id"])
        self.assertEqual(after["status"], "paused")
        self.assertEqual(after["outcome_status"], "uncertain")
        self.assertEqual(after["steps"][0]["status"], "uncertain")

        # Blind retry without approval is prevented
        gated = self.tasks.run_task(task["id"], approved=False)
        self.assertEqual(gated["status"], "waiting_for_approval")

    def test_emergency_stop_blocks_tasks(self) -> None:
        self.perms.trigger_emergency_stop("Test halt")
        task = self.tasks.create_task(title="Blocked Task", goal="calc: 1 + 1")
        res = self.tasks.run_task(task["id"])
        self.assertEqual(res["status"], "paused")
        self.assertIn("Emergency stop", res["error_message"])


if __name__ == "__main__":
    unittest.main()
