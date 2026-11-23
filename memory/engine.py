"""Persistent adaptive memory engine with conflict resolution, deduplication, expiration, and privacy controls."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from database.store import RynaDatabase, new_id, utc_now
from security.privacy import SecretRejectedError, contains_secret

MEMORY_CATEGORIES = (
    "preferences",
    "personal",
    "project",
    "work",
    "decisions",
    "tasks",
    "knowledge",
    "events",
    "activity",
)
MEMORY_TYPES = ("confirmed_fact", "user_preference", "inference", "temporary", "outdated")


def _norm_hash(category: str, content: str) -> str:
    norm = re.sub(r"\s+", " ", content.strip().lower())
    return hashlib.sha256(f"{category}:{norm}".encode("utf-8")).hexdigest()


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{2,}", text.lower()))


class MemoryEngine:
    def __init__(self, db: RynaDatabase) -> None:
        self.db = db

    def is_enabled(self) -> bool:
        return bool(self.db.get_setting("memory.enabled", True))

    def is_learning_paused(self) -> bool:
        return bool(self.db.get_setting("memory.learning_paused", False))

    def _to_dict(self, row: Any) -> dict[str, Any]:
        d = dict(row)
        d["tags"] = json.loads(d.pop("tags_json") or "[]")
        d.pop("normalized_hash", None)
        return d

    def purge_expired(self) -> int:
        with self.db.transaction() as conn:
            return conn.execute(
                "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= ?", (utc_now(),)
            ).rowcount

    def add(
        self,
        category: str,
        content: str,
        memory_type: str = "confirmed_fact",
        confidence: float = 0.85,
        source: str = "user",
        tags: list[str] | None = None,
        subject_key: str = "",
        expires_in_days: int | None = None,
        bypass_pause: bool = True,
    ) -> dict[str, Any]:
        if not self.is_enabled():
            raise RuntimeError("Memory is currently disabled in Settings.")
        if not bypass_pause and self.is_learning_paused():
            raise RuntimeError("Automatic memory learning is paused.")

        cat = category.strip().lower()
        if cat not in MEMORY_CATEGORIES:
            raise ValueError(f"Invalid category '{category}'. Expected one of {MEMORY_CATEGORIES}.")
        mtype = memory_type.strip().lower()
        if mtype not in MEMORY_TYPES:
            raise ValueError(f"Invalid memory type '{memory_type}'. Expected one of {MEMORY_TYPES}.")

        cleaned = content.strip()
        if not cleaned:
            raise ValueError("Memory content cannot be empty.")

        if bool(self.db.get_setting("privacy.redact_secrets", True)) and contains_secret(cleaned):
            raise SecretRejectedError("Refused to store password, API key, token, or private key in ordinary memory.")

        conf = max(0.0, min(1.0, float(confidence)))
        if mtype == "inference" and conf > 0.75:
            conf = 0.75

        if expires_in_days is None and mtype == "temporary":
            expires_in_days = 7
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(days=int(expires_in_days))).isoformat()
            if expires_in_days is not None and expires_in_days > 0
            else None
        )

        nhash = _norm_hash(cat, cleaned)
        subj = subject_key.strip().lower()
        now = utc_now()

        with self.db.transaction() as conn:
            # Exact deduplication
            existing = conn.execute("SELECT * FROM memories WHERE normalized_hash = ?", (nhash,)).fetchone()
            if existing:
                merged_tags = sorted(set(json.loads(existing["tags_json"] or "[]")) | set(tags or []))
                conn.execute(
                    "UPDATE memories SET confidence = ?, source = ?, tags_json = ?, updated_at = ?, expires_at = ? WHERE id = ?",
                    (max(float(existing["confidence"]), conf), source, json.dumps(merged_tags), now, expires_at or existing["expires_at"], existing["id"]),
                )
                return self._to_dict(conn.execute("SELECT * FROM memories WHERE id = ?", (existing["id"],)).fetchone())

            # Conflict resolution: if same subject_key gets a newer confirmed fact/preference, mark prior active entry outdated
            if subj and mtype in ("confirmed_fact", "user_preference"):
                conn.execute(
                    "UPDATE memories SET memory_type = 'outdated', updated_at = ? WHERE subject_key = ? AND category = ? AND memory_type != 'outdated'",
                    (now, subj, cat),
                )

            mid = new_id("mem")
            conn.execute(
                """
                INSERT INTO memories(
                    id, category, memory_type, subject_key, content, normalized_hash,
                    confidence, source, tags_json, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (mid, cat, mtype, subj, cleaned, nhash, conf, source, json.dumps(sorted(set(tags or []))), expires_at, now, now),
            )
            return self._to_dict(conn.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone())

    def list(self, category: str | None = None, query: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        self.purge_expired()
        clauses, params = [], []
        if category and category.lower() in MEMORY_CATEGORIES:
            clauses.append("category = ?")
            params.append(category.lower())
        if query and query.strip():
            clauses.append("(LOWER(content) LIKE ? OR LOWER(tags_json) LIKE ? OR LOWER(subject_key) LIKE ?)")
            q = f"%{query.strip().lower()}%"
            params.extend([q, q, q])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.db._lock:
            rows = self.db._conn.execute(f"SELECT * FROM memories {where} ORDER BY updated_at DESC LIMIT ?", params).fetchall()
            return [self._to_dict(r) for r in rows]

    def retrieve_relevant(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        if not self.is_enabled():
            return []
        self.purge_expired()
        q_toks = _tokens(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in self.list(limit=300):
            if item["memory_type"] == "outdated":
                continue
            item_toks = _tokens(item["content"] + " " + " ".join(item.get("tags", [])) + " " + item.get("subject_key", ""))
            overlap = len(q_toks & item_toks)
            if not q_toks:
                score = float(item["confidence"]) * 0.4
            elif overlap == 0:
                if item["category"] in ("preferences", "personal") and item["memory_type"] == "user_preference":
                    score = 0.25 * float(item["confidence"])
                else:
                    continue
            else:
                jaccard = overlap / max(1, len(q_toks | item_toks))
                boost = 1.15 if item["memory_type"] in ("confirmed_fact", "user_preference") else 0.85
                score = (overlap * 0.6 + jaccard) * float(item["confidence"]) * boost
            scored.append((score, {**item, "relevance_score": round(score, 4)}))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [x[1] for x in scored[:limit]]

    def update(
        self,
        memory_id: str,
        content: str | None = None,
        category: str | None = None,
        memory_type: str | None = None,
        confidence: float | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if not row:
                return None
            new_content = content.strip() if content is not None else row["content"]
            if not new_content:
                raise ValueError("Memory content cannot be empty.")
            if bool(self.db.get_setting("privacy.redact_secrets", True)) and contains_secret(new_content):
                raise SecretRejectedError("Refused to store potential secret in memory.")
            new_cat = category.strip().lower() if category else row["category"]
            if new_cat not in MEMORY_CATEGORIES:
                raise ValueError(f"Invalid category '{new_cat}'.")
            new_type = memory_type.strip().lower() if memory_type else row["memory_type"]
            if new_type not in MEMORY_TYPES:
                raise ValueError(f"Invalid memory type '{new_type}'.")
            new_conf = max(0.0, min(1.0, float(confidence))) if confidence is not None else float(row["confidence"])
            new_tags = json.dumps(sorted(set(tags))) if tags is not None else row["tags_json"]
            conn.execute(
                "UPDATE memories SET category=?, memory_type=?, content=?, normalized_hash=?, confidence=?, tags_json=?, updated_at=? WHERE id=?",
                (new_cat, new_type, new_content, _norm_hash(new_cat, new_content), new_conf, new_tags, utc_now(), memory_id),
            )
            return self._to_dict(conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone())

    def delete(self, memory_id: str) -> bool:
        with self.db.transaction() as conn:
            return conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0

    def clear_all(self, category: str | None = None) -> int:
        with self.db.transaction() as conn:
            if category:
                cat = category.strip().lower()
                if cat not in MEMORY_CATEGORIES:
                    raise ValueError(f"Invalid category '{category}'.")
                return conn.execute("DELETE FROM memories WHERE category = ?", (cat,)).rowcount
            return conn.execute("DELETE FROM memories").rowcount

    def export_all(self) -> dict[str, Any]:
        items = self.list(limit=10000)
        return {"exported_at": utc_now(), "count": len(items), "memories": items}

    def import_backup(self, payload: dict[str, Any]) -> int:
        imported = 0
        for item in payload.get("memories", []):
            try:
                self.add(
                    category=str(item.get("category", "knowledge")),
                    content=str(item.get("content", "")),
                    memory_type=str(item.get("memory_type", "confirmed_fact")),
                    confidence=float(item.get("confidence", 0.8)),
                    source=str(item.get("source", "backup")),
                    tags=list(item.get("tags", [])),
                    subject_key=str(item.get("subject_key", "")),
                )
                imported += 1
            except Exception:
                continue
        return imported
