"""Tests for persistent adaptive memory, deduplication, relevance retrieval, and secret rejection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.python.database import RynaDatabase
from services.memory.store import MemoryStore, SecretRejectedError


class TestMemoryStore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = RynaDatabase(
            Path(self._tmp.name) / "mem.db",
            default_settings={
                "memory.enabled": True,
                "memory.learning_paused": False,
                "privacy.redact_secrets": True,
            },
        )
        self.store = MemoryStore(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_categories_deduplication_and_inference_cap(self) -> None:
        m1 = self.store.add("personal", "User prefers dark navy interfaces", "user_preference", confidence=0.95, tags=["ui"])
        m2 = self.store.add("personal", "  user prefers   dark navy interfaces ", "user_preference", confidence=0.9, tags=["theme"])
        self.assertEqual(m1["id"], m2["id"])
        self.assertEqual(sorted(m2["tags"]), ["theme", "ui"])

        inf = self.store.add("project", "User might build Android next", "inference", confidence=0.99)
        self.assertLessEqual(inf["confidence"], 0.75)

    def test_secret_rejection(self) -> None:
        with self.assertRaises(SecretRejectedError):
            self.store.add("personal", "password = SuperSecret123!")
        with self.assertRaises(SecretRejectedError):
            self.store.add("project", "OpenAI key sk-1234567890abcdefghijklmnop")
        with self.assertRaises(SecretRejectedError):
            self.store.add("knowledge", "-----BEGIN RSA PRIVATE KEY-----\nabc")

    def test_relevance_retrieval_skips_outdated(self) -> None:
        self.store.add("project", "RYNA uses SQLite WAL database for local storage", "confirmed_fact", tags=["sqlite", "ryna"])
        self.store.add("project", "RYNA uses MongoDB", "outdated", tags=["ryna"])

        results = self.store.retrieve_relevant("What database does RYNA use?")
        self.assertEqual(len(results), 1)
        self.assertIn("SQLite", results[0]["content"])

    def test_edit_delete_export_and_disable(self) -> None:
        m = self.store.add("knowledge", "Python 3.13 includes sqlite3", "confirmed_fact")
        updated = self.store.update(m["id"], content="Python 3.13 includes SQLite 3.50+")
        self.assertIsNotNone(updated)
        self.assertIn("3.50+", updated["content"])

        exported = self.store.export_all()
        self.assertEqual(exported["count"], 1)

        self.assertTrue(self.store.delete(m["id"]))
        self.assertEqual(len(self.store.list()), 0)

        self.db.set_setting("memory.enabled", False)
        with self.assertRaises(RuntimeError):
            self.store.add("personal", "Should fail while disabled")


if __name__ == "__main__":
    unittest.main()
