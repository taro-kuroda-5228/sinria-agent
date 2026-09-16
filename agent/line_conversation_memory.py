"""Private, sanitized, searchable memory for allowlisted LINE conversations."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PATIENT_ID_RE = re.compile(
    r"(?:患者|patient|mrn|カルテ|診察券)\s*(?:id|番号|no\.?|#)?\s*[:：=]?\s*[A-Z0-9-]{3,}",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?81[- ]?)?0\d{1,4}[- ]?\d{1,4}[- ]?\d{3,4}(?!\d)")
_CREDENTIAL_RE = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|credential|secret)\s*[:：=]",
    re.IGNORECASE,
)
_CLINICAL_SOURCE_RE = re.compile(
    r"(?:患者|カルテ|診療内容|症例|検査値|処方|服薬|既往歴|病歴|patient\b|medical record|clinical details)",
    re.IGNORECASE,
)
_ALLOWED_KINDS = {"discussion", "proposal", "task", "decision", "fact", "status", "deadline", "correction"}
_ALLOWED_STATUSES = {"active", "superseded", "deleted"}
_MAX_SUMMARY = 500
_MAX_TOPIC = 120
_DAY_MS = 86_400_000


@dataclass(frozen=True)
class LineMemoryReceipt:
    memory_id: str
    source_citation: str
    created: bool


def default_line_conversation_memory_db() -> Path:
    override = os.getenv("SINRIA_LINE_CONVERSATION_MEMORY_DB", "").strip()
    if override:
        return Path(override).expanduser()
    from sinria_constants import get_sinria_home

    return get_sinria_home() / "private" / "line" / "conversation-memory.sqlite3"


def _normalized_text(value: Any, *, field: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or len(text) > limit:
        raise ValueError(f"{field} must contain 1-{limit} characters")
    if (
        _EMAIL_RE.search(text)
        or _PATIENT_ID_RE.search(text)
        or _PHONE_RE.search(text)
        or _CREDENTIAL_RE.search(text)
        or _CLINICAL_SOURCE_RE.search(text)
    ):
        raise ValueError(f"{field} contains sensitive data")
    return text


def is_sensitive_source_text(value: Any) -> bool:
    """Conservative deterministic pre-store guard for obvious identifiers/secrets."""
    text = str(value or "")
    return bool(
        _EMAIL_RE.search(text)
        or _PATIENT_ID_RE.search(text)
        or _PHONE_RE.search(text)
        or _CREDENTIAL_RE.search(text)
        or _CLINICAL_SOURCE_RE.search(text)
    )


class LineConversationMemoryStore:
    """SQLite store containing sanitized summaries only; raw LINE identifiers are hashed."""

    def __init__(self, *, db_path: str | Path | None = None, retention_days: int = 365):
        self.db_path = Path(db_path) if db_path is not None else default_line_conversation_memory_db()
        self.retention_days = max(1, min(int(retention_days or 365), 3650))
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.db_path.parent, 0o700)
        self._conn = sqlite3.connect(str(self.db_path), timeout=10, check_same_thread=False)
        os.chmod(self.db_path, 0o600)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=DELETE")
        self._conn.execute("PRAGMA secure_delete=ON")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                group_key TEXT NOT NULL,
                event_key TEXT NOT NULL UNIQUE,
                author_member_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                topic TEXT NOT NULL,
                memory_kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                source_citation TEXT NOT NULL UNIQUE,
                supersedes_id TEXT,
                superseded_by TEXT,
                knowledge_candidate_ref TEXT,
                content_hash TEXT NOT NULL,
                deleted_at_ms INTEGER,
                FOREIGN KEY(supersedes_id) REFERENCES memories(memory_id)
            );
            CREATE INDEX IF NOT EXISTS idx_line_memory_created ON memories(created_at_ms DESC);
            CREATE INDEX IF NOT EXISTS idx_line_memory_status_expiry ON memories(status, expires_at_ms);
            CREATE INDEX IF NOT EXISTS idx_line_memory_topic ON memories(topic);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "LineConversationMemoryStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @staticmethod
    def _hash(namespace: str, *parts: str) -> str:
        payload = "\0".join([namespace, *parts]).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def remember(
        self,
        *,
        group_id: str,
        sender_user_id: str,
        author_member_id: str,
        webhook_event_id: str,
        message_id: str,
        timestamp_ms: int,
        summary: str,
        topic: str,
        memory_kind: str,
        supersedes_id: str | None = None,
        knowledge_candidate_ref: str | None = None,
    ) -> LineMemoryReceipt:
        if not all(str(value or "").strip() for value in (group_id, sender_user_id, author_member_id, webhook_event_id, message_id)):
            raise ValueError("LINE memory identity is incomplete")
        summary = _normalized_text(summary, field="summary", limit=_MAX_SUMMARY)
        topic = _normalized_text(topic, field="topic", limit=_MAX_TOPIC)
        kind = str(memory_kind or "").strip().lower()
        if kind not in _ALLOWED_KINDS:
            raise ValueError("invalid LINE memory kind")
        created_at_ms = int(timestamp_ms or int(time.time() * 1000))
        expires_at_ms = created_at_ms + self.retention_days * _DAY_MS
        group_key = self._hash("line-group", group_id)
        event_key = self._hash("line-event", group_id, webhook_event_id, message_id)
        memory_id = self._hash("line-memory", event_key)
        citation = f"line-memory:{memory_id[:16]}"
        content_hash = self._hash("line-content", summary, topic, kind)

        existing = self._conn.execute(
            "SELECT memory_id, source_citation FROM memories WHERE event_key = ?", (event_key,)
        ).fetchone()
        if existing:
            return LineMemoryReceipt(existing["memory_id"], existing["source_citation"], False)

        if supersedes_id:
            prior = self._conn.execute(
                "SELECT memory_id, group_key, status FROM memories WHERE memory_id = ?", (supersedes_id,)
            ).fetchone()
            if prior is None or prior["group_key"] != group_key or prior["status"] != "active":
                raise ValueError("superseded LINE memory must be active in the same group")

        with self._conn:
            self._conn.execute(
                """INSERT INTO memories (
                    memory_id, group_key, event_key, author_member_id, summary, topic,
                    memory_kind, status, created_at_ms, expires_at_ms, source_citation,
                    supersedes_id, knowledge_candidate_ref, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)""",
                (
                    memory_id, group_key, event_key, str(author_member_id).strip(), summary, topic,
                    kind, created_at_ms, expires_at_ms, citation, supersedes_id,
                    knowledge_candidate_ref, content_hash,
                ),
            )
            if supersedes_id:
                self._conn.execute(
                    "UPDATE memories SET status='superseded', superseded_by=? WHERE memory_id=?",
                    (memory_id, supersedes_id),
                )
        return LineMemoryReceipt(memory_id, citation, True)

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        include_inactive: bool = False,
        now_ms: int | None = None,
        group_id: str | None = None,
        group_ref: str | None = None,
        author_member_id: str | None = None,
        memory_kinds: list[str] | tuple[str, ...] | None = None,
        after_ms: int | None = None,
        before_ms: int | None = None,
        match: str = "any",
    ) -> list[dict[str, Any]]:
        query = re.sub(r"\s+", " ", str(query or "")).strip()
        limit = max(1, min(int(limit or 10), 20))
        now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        terms = [term for term in query.split(" ") if term][:8]
        if match not in {"any", "all"}:
            raise ValueError("match must be any or all")
        term_clauses = ["(summary LIKE ? ESCAPE '\\' OR topic LIKE ? ESCAPE '\\')" for _ in terms]
        clauses: list[str] = []
        params: list[Any] = []
        for term in terms:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.extend([f"%{escaped}%", f"%{escaped}%"])
        if term_clauses:
            clauses.append(f"({' OR '.join(term_clauses)})" if match == "any" else f"({' AND '.join(term_clauses)})")
        if group_id is not None:
            if not str(group_id).strip():
                raise ValueError("group_id cannot be empty")
            clauses.append("group_key = ?")
            params.append(self._hash("line-group", str(group_id)))
        if group_ref is not None:
            normalized_ref = str(group_ref).strip().lower()
            if not re.fullmatch(r"line-group:[0-9a-f]{16}", normalized_ref):
                raise ValueError("group_ref is invalid")
            clauses.append("substr(group_key, 1, 16) = ?")
            params.append(normalized_ref.split(":", 1)[1])
        if author_member_id is not None:
            author = str(author_member_id).strip()
            if not author:
                raise ValueError("author_member_id cannot be empty")
            clauses.append("author_member_id = ?")
            params.append(author)
        if memory_kinds:
            kinds = tuple(dict.fromkeys(str(kind).strip().lower() for kind in memory_kinds))
            if any(kind not in _ALLOWED_KINDS for kind in kinds):
                raise ValueError("invalid LINE memory kind")
            clauses.append(f"memory_kind IN ({','.join('?' for _ in kinds)})")
            params.extend(kinds)
        if after_ms is not None:
            clauses.append("created_at_ms >= ?")
            params.append(int(after_ms))
        if before_ms is not None:
            clauses.append("created_at_ms < ?")
            params.append(int(before_ms))
        if not clauses:
            raise ValueError("query or at least one scope filter is required")
        status_clause = "status != 'deleted'" if include_inactive else "status = 'active'"
        sql = f"""SELECT memory_id, 'line-group:' || substr(group_key, 1, 16) AS group_ref,
                         author_member_id, summary, topic, memory_kind, status,
                         created_at_ms, source_citation, supersedes_id, superseded_by,
                         knowledge_candidate_ref
                  FROM memories
                  WHERE {status_clause} AND expires_at_ms > ? AND {' AND '.join(clauses)}
                  ORDER BY created_at_ms DESC LIMIT ?"""
        rows = self._conn.execute(sql, [now_ms, *params, limit]).fetchall()
        return [dict(row) for row in rows]

    def coverage(self, *, now_ms: int | None = None) -> dict[str, int | None]:
        """Return timestamp coverage without exposing raw conversation identifiers."""
        now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        row = self._conn.execute(
            "SELECT COUNT(*) AS count, MIN(created_at_ms) AS earliest, MAX(created_at_ms) AS latest "
            "FROM memories WHERE status != 'deleted' AND expires_at_ms > ?",
            (now_ms,),
        ).fetchone()
        return {
            "count": int(row["count"] or 0),
            "earliest_created_at_ms": int(row["earliest"]) if row["earliest"] is not None else None,
            "latest_created_at_ms": int(row["latest"]) if row["latest"] is not None else None,
        }

    def delete(self, memory_id: str, *, deleted_at_ms: int | None = None) -> bool:
        deleted_at_ms = int(deleted_at_ms if deleted_at_ms is not None else time.time() * 1000)
        with self._conn:
            cursor = self._conn.execute(
                """UPDATE memories SET status='deleted', summary='', topic='', knowledge_candidate_ref=NULL,
                           deleted_at_ms=? WHERE memory_id=? AND status != 'deleted'""",
                (deleted_at_ms, str(memory_id or "")),
            )
        return cursor.rowcount == 1

    def purge_expired(self, *, now_ms: int | None = None) -> int:
        now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        with self._conn:
            cursor = self._conn.execute(
                """UPDATE memories SET status='deleted', summary='', topic='', knowledge_candidate_ref=NULL,
                           deleted_at_ms=? WHERE status != 'deleted' AND expires_at_ms <= ?""",
                (now_ms, now_ms),
            )
        return cursor.rowcount
