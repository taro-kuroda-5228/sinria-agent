"""Durable local outbox for final LINE task results.

Only routing identifiers and a sanitized result summary are stored. Raw LINE
messages remain in the existing private task-evidence file and never enter this
outbox or Company OS.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from sinria_constants import get_sinria_home

_LINE_TASK_REF = re.compile(r"^local://line/task-intake/([0-9a-f]{64})$")
_TERMINAL_STATUSES = {"completed", "failed_recoverable", "waiting_review"}
_MAX_SUMMARY = 2000


@dataclass(frozen=True)
class LineTaskCompletion:
    delivery_id: str
    task_id: str
    conversation_id: str
    source_message_id: str
    status: str
    summary: str


def default_line_task_completion_db() -> Path:
    return get_sinria_home() / "private" / "line" / "task-completions.sqlite3"


class LineTaskCompletionStore:
    """SQLite outbox with idempotent claim/deliver transitions."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or default_line_task_completion_db()).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA secure_delete=ON")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS deliveries ("
            "delivery_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, conversation_id TEXT NOT NULL, "
            "source_message_id TEXT NOT NULL, status TEXT NOT NULL, summary TEXT NOT NULL, "
            "state TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL, updated_at REAL NOT NULL)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_line_completion_pending "
            "ON deliveries(state, created_at)"
        )
        self.connection.commit()
        try:
            os.chmod(self.path.parent, 0o700)
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def __enter__(self) -> "LineTaskCompletionStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def enqueue(
        self,
        *,
        task_id: str,
        conversation_id: str,
        source_message_id: str,
        status: str,
        summary: str,
    ) -> str:
        task_id = str(task_id or "").strip()
        conversation_id = str(conversation_id or "").strip()
        source_message_id = str(source_message_id or "").strip()
        status = str(status or "").strip().lower()
        summary = " ".join(str(summary or "").split()).strip()
        if not task_id or not conversation_id or not source_message_id:
            raise ValueError("LINE completion identity is incomplete")
        if status not in _TERMINAL_STATUSES:
            raise ValueError("LINE completion status is not terminal")
        if not summary or len(summary) > _MAX_SUMMARY:
            raise ValueError("LINE completion summary is invalid")
        fingerprint = hashlib.sha256(
            json.dumps(
                [task_id, conversation_id, source_message_id, status, summary],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        delivery_id = f"line-result:{fingerprint}"
        now = time.time()
        with self._lock, self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO deliveries VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (
                    delivery_id,
                    task_id,
                    conversation_id,
                    source_message_id,
                    status,
                    summary,
                    now,
                    now,
                ),
            )
        return delivery_id

    def pending(self, *, limit: int = 10) -> list[LineTaskCompletion]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT delivery_id, task_id, conversation_id, source_message_id, status, summary "
                "FROM deliveries WHERE state = 'pending' ORDER BY created_at LIMIT ?",
                (max(1, min(int(limit), 50)),),
            ).fetchall()
        return [LineTaskCompletion(**dict(row)) for row in rows]

    def claim(self, delivery_id: str) -> bool:
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE deliveries SET state='sending', updated_at=? "
                "WHERE delivery_id=? AND state='pending'",
                (time.time(), delivery_id),
            )
        return cursor.rowcount == 1

    def release(self, delivery_id: str) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                "UPDATE deliveries SET state='pending', updated_at=? "
                "WHERE delivery_id=? AND state='sending'",
                (time.time(), delivery_id),
            )

    def mark_delivered(self, delivery_id: str) -> None:
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE deliveries SET state='delivered', updated_at=? "
                "WHERE delivery_id=? AND state='sending'",
                (time.time(), delivery_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("LINE completion delivery claim is unavailable")

    def mark_indeterminate(self, delivery_id: str) -> None:
        """Park a send whose provider effect cannot be proved either way.

        LINE has no idempotency key for Push API requests.  Retrying after a
        timeout or connection failure can therefore duplicate a result that
        LINE accepted before the response was lost.  An operator must reconcile
        this state rather than allowing the polling loop to resend it.
        """
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE deliveries SET state='indeterminate', updated_at=? "
                "WHERE delivery_id=? AND state='sending'",
                (time.time(), delivery_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("LINE completion delivery claim is unavailable")

    def recover_stale_sends(self, *, older_than_seconds: float = 300.0) -> int:
        cutoff = time.time() - max(30.0, float(older_than_seconds))
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE deliveries SET state='pending', updated_at=? "
                "WHERE state='sending' AND updated_at < ?",
                (time.time(), cutoff),
            )
        return cursor.rowcount


def enqueue_line_task_completion(
    task: Mapping[str, Any],
    *,
    task_id: str,
    status: str,
    sanitized_summary: str,
    store_path: str | Path | None = None,
) -> str | None:
    """Resolve a local LINE source reference and enqueue one terminal result."""
    raw_payload = task.get("payload")
    payload: Mapping[str, Any] = raw_payload if isinstance(raw_payload, Mapping) else {}
    source_ref = str(payload.get("sourceRef") or "")
    match = _LINE_TASK_REF.fullmatch(source_ref)
    if match is None:
        return None
    root = get_sinria_home() / "private" / "line" / "task-intake"
    path = root / f"{match.group(1)}.json"
    try:
        if path.is_symlink() or not path.is_file() or path.resolve().parent != root.resolve():
            return None
        if path.stat().st_mode & 0o077:
            return None
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(evidence, dict):
        return None
    with LineTaskCompletionStore(store_path) as store:
        return store.enqueue(
            task_id=task_id,
            conversation_id=str(evidence.get("groupId") or ""),
            source_message_id=str(evidence.get("messageId") or ""),
            status=status,
            summary=sanitized_summary,
        )
