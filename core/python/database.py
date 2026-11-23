"""SQLite local database layer for RYNA.

Stores conversations, messages, memories, settings, tasks, task steps,
notes/reminders, and permission/action audit logs with WAL durability.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}_{raw}" if prefix else raw


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    mode TEXT NOT NULL DEFAULT 'offline',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    normalized_hash TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.8,
    source TEXT NOT NULL DEFAULT 'user',
    tags_json TEXT NOT NULL DEFAULT '[]',
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
CREATE INDEX IF NOT EXISTS idx_memories_hash ON memories(normalized_hash);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    outcome_status TEXT NOT NULL DEFAULT 'pending',
    requires_internet INTEGER NOT NULL DEFAULT 0,
    permission_level INTEGER NOT NULL DEFAULT 1,
    plan_json TEXT NOT NULL DEFAULT '[]',
    result_summary TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    conversation_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS task_steps (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    description TEXT NOT NULL,
    tool_name TEXT,
    tool_args_json TEXT NOT NULL DEFAULT '{}',
    permission_level INTEGER NOT NULL DEFAULT 1,
    requires_internet INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    output TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_task_steps_task ON task_steps(task_id, step_index);

CREATE TABLE IF NOT EXISTS action_audit (
    id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    permission_level INTEGER NOT NULL,
    status TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    task_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_action_audit_created ON action_audit(created_at DESC);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    remind_at TEXT,
    completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class RynaDatabase:
    """Thread-safe SQLite database wrapper for RYNA."""

    def __init__(self, db_path: Path, default_settings: dict[str, Any] | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize(default_settings or {})

    def _initialize(self, default_settings: dict[str, Any]) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)
            now = utc_now()
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value, updated_at) VALUES (?, ?, ?)",
                ("schema_version", str(SCHEMA_VERSION), now),
            )
            for key, value in default_settings.items():
                self._conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)",
                    (key, json.dumps(value), now),
                )
            self._conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- Settings ---

    def get_all_settings(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value_json FROM settings ORDER BY key").fetchall()
            return {row["key"]: json.loads(row["value_json"]) for row in rows}

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute(
                "SELECT value_json FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return json.loads(row["value_json"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), utc_now()),
            )

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.transaction() as conn:
            for key, value in updates.items():
                conn.execute(
                    "INSERT OR REPLACE INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)",
                    (key, json.dumps(value), now),
                )
        return self.get_all_settings()

    # --- Conversations & Messages ---

    def create_conversation(self, title: str = "New Conversation") -> dict[str, Any]:
        conv_id = new_id("conv")
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO conversations(id, title, created_at, updated_at, metadata_json) VALUES (?, ?, ?, ?, ?)",
                (conv_id, title.strip() or "New Conversation", now, now, "{}"),
            )
        return {
            "id": conv_id,
            "title": title.strip() or "New Conversation",
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
        }

    def list_conversations(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at,
                       COUNT(m.id) AS message_count
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                GROUP BY c.id
                ORDER BY c.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            return dict(row) if row else None

    def delete_conversation(self, conversation_id: str) -> bool:
        with self.transaction() as conn:
            cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            return cur.rowcount > 0

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        provider: str | None = None,
        model: str | None = None,
        mode: str = "offline",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        msg_id = new_id("msg")
        now = utc_now()
        meta_json = json.dumps(metadata or {})
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO messages(id, conversation_id, role, content, provider, model, mode, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (msg_id, conversation_id, role, content, provider, model, mode, meta_json, now),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            # Auto-title new conversations from first user message
            if role == "user":
                conv = conn.execute(
                    "SELECT title FROM conversations WHERE id = ?", (conversation_id,)
                ).fetchone()
                if conv and conv["title"] == "New Conversation":
                    snippet = content.strip().splitlines()[0][:48]
                    if snippet:
                        conn.execute(
                            "UPDATE conversations SET title = ? WHERE id = ?",
                            (snippet, conversation_id),
                        )
        return {
            "id": msg_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "provider": provider,
            "model": model,
            "mode": mode,
            "metadata": metadata or {},
            "created_at": now,
        }

    def list_messages(self, conversation_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, conversation_id, role, content, provider, model, mode, metadata_json, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
            result = []
            for r in rows:
                item = dict(r)
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
                result.append(item)
            return result

    # --- Notes & Reminders ---

    def create_note(self, title: str, content: str, remind_at: str | None = None) -> dict[str, Any]:
        note_id = new_id("note")
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO notes(id, title, content, remind_at, completed, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
                (note_id, title.strip() or "Untitled Note", content, remind_at, now, now),
            )
        return {
            "id": note_id,
            "title": title.strip() or "Untitled Note",
            "content": content,
            "remind_at": remind_at,
            "completed": False,
            "created_at": now,
            "updated_at": now,
        }

    def list_notes(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, content, remind_at, completed, created_at, updated_at FROM notes ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["completed"] = bool(d["completed"])
                out.append(d)
            return out

    # --- Audit Log ---

    def log_action(
        self,
        action_type: str,
        permission_level: int,
        status: str,
        target: str = "",
        details: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        audit_id = new_id("act")
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO action_audit(id, action_type, permission_level, status, target, details_json, task_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (audit_id, action_type, permission_level, status, target, json.dumps(details or {}), task_id, now),
            )
        return {
            "id": audit_id,
            "action_type": action_type,
            "permission_level": permission_level,
            "status": status,
            "target": target,
            "details": details or {},
            "task_id": task_id,
            "created_at": now,
        }

    def list_audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, action_type, permission_level, status, target, details_json, task_id, created_at FROM action_audit ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            result = []
            for r in rows:
                item = dict(r)
                item["details"] = json.loads(item.pop("details_json") or "{}")
                result.append(item)
            return result

    def stats(self) -> dict[str, int]:
        with self._lock:
            def count(table: str) -> int:
                row = self._conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
                return int(row["c"]) if row else 0

            return {
                "conversations": count("conversations"),
                "messages": count("messages"),
                "memories": count("memories"),
                "tasks": count("tasks"),
                "notes": count("notes"),
                "audit_entries": count("action_audit"),
                "db_size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            }
