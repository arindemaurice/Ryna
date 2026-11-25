"""Unit and integration tests for complete Windows device access and Ollama agent tool execution."""

from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from ai.config import RynaConfig
from ai.orchestration.core import RynaCore
from computer.controller import ComputerController
from database.store import RynaDatabase


class TestDeviceControl(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.cfg = RynaConfig.load(overrides={"db_path": self.tmp_path / "ryna.db", "data_dir": self.tmp_path})
        self.core = RynaCore(self.cfg, connectivity_checker=lambda: False)

    def tearDown(self) -> None:
        self.core.close()
        self.tmp.cleanup()

    def test_powershell_execution(self) -> None:
        """Verify real PowerShell command execution returns output and returncode."""
        res = self.core.computer.execute_command("Write-Output 'RYNA_DEVICE_OK'")
        self.assertTrue(res["success"])
        self.assertEqual(res["returncode"], 0)
        self.assertIn("RYNA_DEVICE_OK", res["output"])

    def test_mouse_and_keyboard_controls(self) -> None:
        """Verify mouse position and keypress methods don't crash and report status."""
        pos = self.core.computer.get_mouse_position()
        self.assertIn("x", pos)
        self.assertIn("y", pos)

        # Move mouse to its current position (safe no-op movement)
        m_res = self.core.computer.mouse_move(pos["x"], pos["y"])
        self.assertTrue(m_res["moved"])

        # Test hotkey logic for standard key combinations
        hk_res = self.core.computer.hotkey("ctrl+c")
        self.assertTrue(hk_res["success"])

        # Test single keypress
        pk_res = self.core.computer.press_key("space")
        self.assertTrue(pk_res["pressed"])

    def test_unrestricted_filesystem_access(self) -> None:
        """Verify files can be created, written, read, and deleted across custom paths."""
        test_file = self.tmp_path / "device_test.txt"
        write_res = self.core.tools.execute("file_write", {"path": str(test_file), "content": "RYNA Full Access"})
        self.assertTrue(write_res["written"])
        self.assertTrue(test_file.exists())

        read_res = self.core.tools.execute("file_read", {"path": str(test_file)})
        self.assertEqual(read_res["content"], "RYNA Full Access")

        del_res = self.core.tools.execute("delete_file", {"path": str(test_file)})
        self.assertTrue(del_res["deleted"])
        self.assertFalse(test_file.exists())

    def test_natural_action_device_commands(self) -> None:
        """Verify natural direct commands execute immediately without LLM latency."""
        conv_id = self.core.db.create_conversation()["id"]

        # Natural PowerShell execution
        msg = self.core._try_natural_action("run command Write-Output 'NaturalPS'", conv_id, online=False)
        self.assertIsNotNone(msg)
        self.assertIn("NaturalPS", msg["content"])

        # Natural file creation
        target_f = self.tmp_path / "natural_test.txt"
        msg2 = self.core._try_natural_action(f"create file {target_f} with content HelloFromNatural", conv_id, online=False)
        self.assertIsNotNone(msg2)
        self.assertTrue(target_f.exists())
        self.assertEqual(target_f.read_text(encoding="utf-8").strip(), "HelloFromNatural")

        # Natural process list
        msg3 = self.core._try_natural_action("list running processes", conv_id, online=False)
        self.assertIsNotNone(msg3)
        self.assertIn("Running processes", msg3["content"])

    def test_ollama_action_detection_and_execution(self) -> None:
        """Verify that when Ollama outputs an [ACTION: ...] tag, RYNA executes it on Windows."""
        conv_id = self.core.db.create_conversation()["id"]
        test_f = self.tmp_path / "ollama_action_test.txt"

        ollama_output = (
            f"I will create that file for you now.\n"
            f"[ACTION: file_write({{\"path\": \"{str(test_f).replace('\\', '/')}\", \"content\": \"Created by Ollama agent\"}})]\n"
            f"The file is now ready on your device."
        )

        processed_text, executed = self.core._execute_ai_device_actions(ollama_output, conv_id, mode="offline")
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0]["tool"], "file_write")
        self.assertTrue(executed[0]["success"])
        self.assertTrue(test_f.exists())
        self.assertEqual(test_f.read_text(encoding="utf-8").strip(), "Created by Ollama agent")
        self.assertNotIn("[ACTION:", processed_text)
        self.assertIn("File created:", processed_text)

    def test_ollama_powershell_action_execution(self) -> None:
        """Verify Ollama can run PowerShell commands via agent action tags."""
        conv_id = self.core.db.create_conversation()["id"]
        ollama_output = "Let me check that for you.\n[ACTION: execute_command({\"command\": \"Write-Output 'AgenticPowerShellOK'\"})]"

        processed_text, executed = self.core._execute_ai_device_actions(ollama_output, conv_id, mode="offline")
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0]["tool"], "execute_command")
        self.assertTrue(executed[0]["success"])
        self.assertIn("AgenticPowerShellOK", processed_text)


if __name__ == "__main__":
    unittest.main()
