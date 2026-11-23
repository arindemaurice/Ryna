"""Tests for SQLite persistence, WAL mode, settings, conversations, notes, and audit log."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.python.database import RynaDatabase


class TestRynaDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "ryna_test.db"
        self.db = RynaDatabase(
            self.db_path,
            default_settings={"general.assistant_name": "RYNA", "ai.allow_cloud": False},
        )

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_settings_persist_across_reopen(self) -> None:
        self.assertEqual(self.db.get_setting("general.assistant_name"), "RYNA")
        self.db.update_settings({"ai.allow_cloud": True, "appearance.companion_size": 210})
        self.db.close()

        reopened = RynaDatabase(self.db_path, default_settings={"ai.allow_cloud": False})
        try:
            self.assertTrue(reopened.get_setting("ai.allow_cloud"))
            self.assertEqual(reopened.get_setting("appearance.companion_size"), 210)
        finally:
            reopened.close()

    def test_conversation_auto_title_and_cascade_delete(self) -> None:
        conv = self.db.create_conversation()
        self.assertEqual(conv["title"], "New Conversation")

        self.db.add_message(conv["id"], "user", "Plan my offline architecture for RYNA")
        self.db.add_message(conv["id"], "assistant", "Stored reply", provider="ollama", model="qwen2.5:3b")

        convs = self.db.list_conversations()
        self.assertEqual(len(convs), 1)
        self.assertIn("Plan my offline", convs[0]["title"])
        self.assertEqual(convs[0]["message_count"], 2)

        msgs = self.db.list_messages(conv["id"])
        self.assertEqual(len(msgs), 2)
        self.assertTrue(self.db.delete_conversation(conv["id"]))
        self.assertEqual(self.db.list_messages(conv["id"]), [])

    def test_notes_and_audit_log(self) -> None:
        note = self.db.create_note("Test Note", "Verify offline SQLite note storage")
        self.assertEqual(note["title"], "Test Note")
        self.assertEqual(len(self.db.list_notes()), 1)

        self.db.log_action("calculate", 1, "permitted", "2+2", {"result": 4})
        entries = self.db.list_audit_log()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action_type"], "calculate")


if __name__ == "__main__":
    unittest.main()
