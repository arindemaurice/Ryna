"""Thread-safe SQLite database with versioned migrations, WAL durability, backup, and restore."""

from __future__ import annotations

import json
import shutil
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


MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        PRAGMA journal_mode = WAL;
        PRAGMA foreign_keys = ON;
        PRAGMA busy_timeout = 5000;

        CREATE TABLE IF NOT EXISTS schema_meta (
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
            updated_at TEXT NOT NULL
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
            subject_key TEXT NOT NULL DEFAULT '',
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
        CREATE INDEX IF NOT EXISTS idx_memories_subject ON memories(subject_key);

        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            goal TEXT NOT NULL,
            status TEXT NOT NULL,
            outcome_status TEXT NOT NULL DEFAULT 'pending',
            requires_internet INTEGER NOT NULL DEFAULT 0,
            permission_level INTEGER NOT NULL DEFAULT 1,
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
            status TEXT NOT NULL DEFAULT 'planned',
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
        CREATE INDEX IF NOT EXISTS idx_audit_created ON action_audit(created_at DESC);

        CREATE TABLE IF NOT EXISTS activity_events (
            id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            event_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'info',
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_events(created_at DESC);

        CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            remind_at TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    )
]


class RynaDatabase:
    def __init__(self, db_path: Path, default_settings: dict[str, Any] | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._apply_migrations(default_settings or {})

    def _apply_migrations(self, default_settings: dict[str, Any]) -> None:
        with self._lock:
            for version, sql in MIGRATIONS:
                self._conn.executescript(sql)
                self._conn.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value, updated_at) VALUES (?, ?, ?)",
                    ("schema_version", str(version), utc_now()),
                )
            now = utc_now()
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

    def backup_to(self, destination: Path) -> Path:
        """Create a consistent SQLite hot backup using sqlite3.backup."""
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            dst_conn = sqlite3.connect(str(dest))
            try:
                self._conn.backup(dst_conn)
            finally:
                dst_conn.close()
        return dest

    def restore_from(self, source: Path) -> None:
        """Restore database state from a valid SQLite backup file."""
        src = Path(source)
        if not src.exists():
            raise FileNotFoundError(f"Backup file not found: {src}")
        with self._lock:
            src_conn = sqlite3.connect(str(src))
            try:
                src_conn.backup(self._conn)
            finally:
                src_conn.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- Settings ---

    def get_all_settings(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value_json FROM settings ORDER BY key").fetchall()
            return {r["key"]: json.loads(r["value_json"]) for r in rows}

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
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
        cid = new_id("conv")
        now = utc_now()
        clean_title = title.strip() or "New Conversation"
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (cid, clean_title, now, now),
            )
        return {"id": cid, "title": clean_title, "created_at": now, "updated_at": now, "message_count": 0}

    def list_conversations(self, limit: int = 60) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at, COUNT(m.id) AS message_count
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
        mid = new_id("msg")
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO messages(id, conversation_id, role, content, provider, model, mode, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (mid, conversation_id, role, content, provider, model, mode, json.dumps(metadata or {}), now),
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
            if role == "user":
                conv = conn.execute("SELECT title FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
                if conv and conv["title"] == "New Conversation":
                    snippet = content.strip().splitlines()[0][:52]
                    if snippet:
                        conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (snippet, conversation_id))
        return {
            "id": mid,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "provider": provider,
            "model": model,
            "mode": mode,
            "metadata": metadata or {},
            "created_at": now,
        }

    def list_messages(self, conversation_id: str, limit: int = 250) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, conversation_id, role, content, provider, model, mode, metadata_json, created_at
                FROM messages WHERE conversation_id = ? ORDER BY created_at ASC LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
                out.append(d)
            return out

    def delete_message(self, message_id: str) -> bool:
        with self.transaction() as conn:
            cur = conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            return cur.rowcount > 0

    # --- Notes, Audit & Activity ---

    def create_note(self, title: str, content: str, remind_at: str | None = None) -> dict[str, Any]:
        nid = new_id("note")
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO notes(id, title, content, remind_at, completed, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
                (nid, title.strip() or "Note", content, remind_at, now, now),
            )
        return {"id": nid, "title": title.strip() or "Note", "content": content, "remind_at": remind_at, "completed": False, "created_at": now, "updated_at": now}

    def list_notes(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM notes ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
            return [{**dict(r), "completed": bool(r["completed"])} for r in rows]

    def complete_note(self, note_id: str) -> bool:
        with self.transaction() as conn:
            cur = conn.execute("UPDATE notes SET completed = 1, updated_at = ? WHERE id = ?", (utc_now(), note_id))
            return cur.rowcount > 0

    def log_action(
        self,
        action_type: str,
        permission_level: int,
        status: str,
        target: str = "",
        details: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        aid = new_id("act")
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO action_audit(id, action_type, permission_level, status, target, details_json, task_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (aid, action_type, permission_level, status, target, json.dumps(details or {}), task_id, now),
            )
        return {"id": aid, "action_type": action_type, "permission_level": permission_level, "status": status, "target": target, "details": details or {}, "task_id": task_id, "created_at": now}

    def list_audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM action_audit ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["details"] = json.loads(d.pop("details_json") or "{}")
                out.append(d)
            return out

    def record_activity(
        self,
        category: str,
        event_type: str,
        summary: str,
        status: str = "info",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        eid = new_id("evt")
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO activity_events(id, category, event_type, summary, status, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (eid, category, event_type, summary, status, json.dumps(details or {}), now),
            )
        return {"id": eid, "category": category, "event_type": event_type, "summary": summary, "status": status, "details": details or {}, "created_at": now}

    def list_activity(self, category: str | None = None, limit: int = 150) -> list[dict[str, Any]]:
        with self._lock:
            if category and category.strip():
                rows = self._conn.execute(
                    "SELECT * FROM activity_events WHERE category = ? ORDER BY created_at DESC LIMIT ?",
                    (category.strip().lower(), limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM activity_events ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["details"] = json.loads(d.pop("details_json") or "{}")
                out.append(d)
            return out

    def clear_activity(self) -> int:
        with self.transaction() as conn:
            return conn.execute("DELETE FROM activity_events").rowcount

    def stats(self) -> dict[str, int]:
        with self._lock:
            def _c(tbl: str) -> int:
                row = self._conn.execute(f"SELECT COUNT(*) AS c FROM {tbl}").fetchone()
                return int(row["c"]) if row else 0

            return {
                "schema_version": SCHEMA_VERSION,
                "conversations": _c("conversations"),
                "messages": _c("messages"),
                "memories": _c("memories"),
                "tasks": _c("tasks"),
                "notes": _c("notes"),
                "audit_entries": _c("action_audit"),
                "activity_events": _c("activity_events"),
                "db_size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            }
