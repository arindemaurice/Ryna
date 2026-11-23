"""Persistent adaptive memory store for RYNA.

Supports Personal, Project, Knowledge, and Activity memories,
epistemic distinction (fact, preference, inference, temporary, outdated),
deduplication, expiration, relevance retrieval, export, and secret rejection.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from core.python.database import RynaDatabase, new_id, utc_now

MEMORY_CATEGORIES = ("personal", "project", "knowledge", "activity")
MEMORY_TYPES = ("confirmed_fact", "user_preference", "inference", "temporary", "outdated")

SECRET_PATTERNS = [
    re.compile(r"\b(password|passwd|pwd|secret|token|api[_\s-]?key|private[_\s-]?key)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z\-_]{20,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
]


class SecretRejectedError(ValueError):
    """Raised when an attempt is made to store a secret/credential in ordinary memory."""


def contains_secret(content: str) -> bool:
    return any(p.search(content) for p in SECRET_PATTERNS)


def _normalize_hash(category: str, content: str) -> str:
    norm = re.sub(r"\s+", " ", content.strip().lower())
    return hashlib.sha256(f"{category}:{norm}".encode("utf-8")).hexdigest()


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{2,}", text.lower())}


class MemoryStore:
    def __init__(self, db: RynaDatabase) -> None:
        self.db = db

    def is_enabled(self) -> bool:
        return bool(self.db.get_setting("memory.enabled", True))

    def is_learning_paused(self) -> bool:
        return bool(self.db.get_setting("memory.learning_paused", False))

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        item = dict(row)
        item["tags"] = json.loads(item.pop("tags_json") or "[]")
        item.pop("normalized_hash", None)
        return item

    def purge_expired(self) -> int:
        now = utc_now()
        with self.db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            )
            return cur.rowcount

    def add(
        self,
        category: str,
        content: str,
        memory_type: str = "confirmed_fact",
        confidence: float = 0.85,
        source: str = "user",
        tags: list[str] | None = None,
        expires_in_days: int | None = None,
        bypass_pause: bool = True,
    ) -> dict[str, Any]:
        if not self.is_enabled():
            raise RuntimeError("Memory is currently disabled in Settings.")
        if not bypass_pause and self.is_learning_paused():
            raise RuntimeError("Memory learning is currently paused.")

        cat = category.strip().lower()
        if cat not in MEMORY_CATEGORIES:
            raise ValueError(f"Invalid memory category '{category}'. Expected one of {MEMORY_CATEGORIES}.")
        mtype = memory_type.strip().lower()
        if mtype not in MEMORY_TYPES:
            raise ValueError(f"Invalid memory type '{memory_type}'. Expected one of {MEMORY_TYPES}.")

        cleaned = content.strip()
        if not cleaned:
            raise ValueError("Memory content cannot be empty.")

        if bool(self.db.get_setting("privacy.redact_secrets", True)) and contains_secret(cleaned):
            raise SecretRejectedError(
                "Refused to store potential password, token, or API key in ordinary memory."
            )

        confidence = max(0.0, min(1.0, float(confidence)))
        # Never silently elevate inferences into confirmed facts
        if mtype == "inference" and confidence > 0.75:
            confidence = 0.75

        norm_hash = _normalize_hash(cat, cleaned)
        now = utc_now()

        if expires_in_days is None and mtype == "temporary":
            expires_in_days = 7
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(days=int(expires_in_days))).isoformat()
            if expires_in_days is not None and expires_in_days > 0
            else None
        )

        # Deduplicate exact normalized matches within same category
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM memories WHERE normalized_hash = ?", (norm_hash,)
            ).fetchone()
            if existing:
                merged_tags = sorted(set(json.loads(existing["tags_json"] or "[]")) | set(tags or []))
                conn.execute(
                    """
                    UPDATE memories
                    SET confidence = ?, source = ?, tags_json = ?, updated_at = ?, expires_at = ?
                    WHERE id = ?
                    """,
                    (
                        max(float(existing["confidence"]), confidence),
                        source,
                        json.dumps(merged_tags),
                        now,
                        expires_at or existing["expires_at"],
                        existing["id"],
                    ),
                )
                row = conn.execute("SELECT * FROM memories WHERE id = ?", (existing["id"],)).fetchone()
                return self._row_to_dict(row)

            mem_id = new_id("mem")
            conn.execute(
                """
                INSERT INTO memories(
                    id, category, memory_type, content, normalized_hash,
                    confidence, source, tags_json, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mem_id,
                    cat,
                    mtype,
                    cleaned,
                    norm_hash,
                    confidence,
                    source,
                    json.dumps(sorted(set(tags or []))),
                    expires_at,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
            return self._row_to_dict(row)

    def list(
        self,
        category: str | None = None,
        query: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        self.purge_expired()
        clauses = []
        params: list[Any] = []
        if category and category.lower() in MEMORY_CATEGORIES:
            clauses.append("category = ?")
            params.append(category.lower())
        if query and query.strip():
            clauses.append("(LOWER(content) LIKE ? OR LOWER(tags_json) LIKE ?)")
            q = f"%{query.strip().lower()}%"
            params.extend([q, q])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM memories {where} ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self.db._lock:
            rows = self.db._conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def retrieve_relevant(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        """Retrieve relevant non-outdated memories scored by token overlap and confidence."""
        if not self.is_enabled():
            return []
        self.purge_expired()
        q_tokens = _tokenize(query)
        all_items = self.list(limit=300)
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in all_items:
            if item["memory_type"] == "outdated":
                continue
            item_tokens = _tokenize(item["content"] + " " + " ".join(item.get("tags", [])))
            overlap = len(q_tokens & item_tokens)
            if not q_tokens:
                score = float(item["confidence"]) * 0.5
            elif overlap == 0:
                # Still include high-confidence personal preferences at lower weight
                if item["category"] == "personal" and item["memory_type"] == "user_preference":
                    score = 0.25 * float(item["confidence"])
                else:
                    continue
            else:
                jaccard = overlap / max(1, len(q_tokens | item_tokens))
                type_boost = 1.15 if item["memory_type"] in ("confirmed_fact", "user_preference") else 0.9
                score = (overlap * 0.6 + jaccard) * float(item["confidence"]) * type_boost
            enriched = dict(item)
            enriched["relevance_score"] = round(score, 4)
            scored.append((score, enriched))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

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
            new_conf = float(confidence) if confidence is not None else float(row["confidence"])
            new_conf = max(0.0, min(1.0, new_conf))
            new_tags = json.dumps(sorted(set(tags))) if tags is not None else row["tags_json"]
            new_hash = _normalize_hash(new_cat, new_content)

            conn.execute(
                """
                UPDATE memories
                SET category = ?, memory_type = ?, content = ?, normalized_hash = ?,
                    confidence = ?, tags_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_cat, new_type, new_content, new_hash, new_conf, new_tags, utc_now(), memory_id),
            )
            updated = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            return self._row_to_dict(updated)

    def delete(self, memory_id: str) -> bool:
        with self.db.transaction() as conn:
            cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            return cur.rowcount > 0

    def delete_category(self, category: str) -> int:
        cat = category.strip().lower()
        if cat not in MEMORY_CATEGORIES:
            raise ValueError(f"Invalid category '{category}'.")
        with self.db.transaction() as conn:
            cur = conn.execute("DELETE FROM memories WHERE category = ?", (cat,))
            return cur.rowcount

    def clear_all(self) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute("DELETE FROM memories")
            return cur.rowcount

    def export_all(self) -> dict[str, Any]:
        items = self.list(limit=10000)
        return {
            "exported_at": utc_now(),
            "count": len(items),
            "memories": items,
        }
