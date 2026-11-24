"""Tests for SQLite migrations, hot backup/restore, settings, conversations, and 9-category memory."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.store import RynaDatabase
from memory.engine import MemoryEngine
from security.privacy import SecretRejectedError


class TestDatabaseAndMemory(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = RynaDatabase(
            self.root / "ryna.db",
            default_settings={"memory.enabled": True, "privacy.redact_secrets": True},
        )
        self.mem = MemoryEngine(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_hot_backup_and_restore(self) -> None:
        self.db.set_setting("voice.wake_word", "Hey Ryna Custom")
        conv = self.db.create_conversation()
        self.db.add_message(conv["id"], "user", "Important project decision")
        backup = self.db.backup_to(self.root / "backup.db")
        self.assertTrue(backup.is_file())

        # Mutate live DB then restore
        self.db.set_setting("voice.wake_word", "Changed")
        self.db.restore_from(backup)
        self.assertEqual(self.db.get_setting("voice.wake_word"), "Hey Ryna Custom")

    def test_memory_dedup_conflict_resolution_and_secret_guard(self) -> None:
        m1 = self.mem.add("preferences", "Primary editor is VS Code", "user_preference", subject_key="editor", tags=["dev"])
        m2 = self.mem.add("preferences", "  primary editor is vs code ", "user_preference", subject_key="editor", tags=["ide"])
        self.assertEqual(m1["id"], m2["id"])
        self.assertEqual(sorted(m2["tags"]), ["dev", "ide"])

        # Conflict update marks prior subject_key entry outdated
        m3 = self.mem.add("preferences", "Primary editor is Neovim", "user_preference", subject_key="editor")
        all_prefs = self.mem.list(category="preferences")
        old = next(x for x in all_prefs if x["id"] == m1["id"])
        self.assertEqual(old["memory_type"], "outdated")
        self.assertEqual(m3["memory_type"], "user_preference")

        # Retrieval skips outdated
        rel = self.mem.retrieve_relevant("What is my primary editor?")
        self.assertEqual(len(rel), 1)
        self.assertIn("Neovim", rel[0]["content"])

        # Secret rejection
        with self.assertRaises(SecretRejectedError):
            self.mem.add("personal", "password=MySuperSecretPassword123")
        with self.assertRaises(SecretRejectedError):
            self.mem.add("project", "token sk-1234567890abcdefghijklmnop")


if __name__ == "__main__":
    unittest.main()
