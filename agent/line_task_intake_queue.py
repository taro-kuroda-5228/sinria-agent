"""Durable private queue for LINE task-intake classification.

The queue is local-only and may contain bounded raw conversation context. Nothing
from it is synchronized to Company OS; only the existing sanitized task envelope
or conversation-memory summary may cross that boundary.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from sinria_constants import get_sinria_home


@dataclass(frozen=True)
class QueuedLineIntake:
    event_id: str
    prompt: str
    context: dict[str, Any]


def default_line_intake_queue_db() -> Path:
    return get_sinria_home() / "private" / "line" / "intake-queue.sqlite3"


class LineIntakeQueue:
    """Idempotent local queue acknowledged before slow classification begins."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or default_line_intake_queue_db()).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA secure_delete=ON")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS intake ("
            "event_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, prompt TEXT NOT NULL, "
            "context_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL)"
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

    def enqueue(self, *, event_id: str, prompt: str, context: Mapping[str, Any]) -> bool:
        event_id = str(event_id or "").strip()
        prompt = str(prompt or "").strip()
        if not event_id or not prompt or not isinstance(context, Mapping):
            raise ValueError("LINE intake queue payload is incomplete")
        serialized = json.dumps(dict(context), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(serialized) > 20_000 or len(prompt) > 20_000:
            raise ValueError("LINE intake queue payload is too large")
        fingerprint = hashlib.sha256(f"{prompt}\0{serialized}".encode("utf-8")).hexdigest()
        now = time.time()
        with self._lock:
            row = self.connection.execute(
                "SELECT fingerprint FROM intake WHERE event_id=?", (event_id,)
            ).fetchone()
            if row:
                if str(row["fingerprint"]) != fingerprint:
                    raise ValueError("LINE event identity was reused with different content")
                return False
            with self.connection:
                self.connection.execute(
                    "INSERT INTO intake VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                    (event_id, fingerprint, prompt, serialized, now, now),
                )
        return True

    def pending(self, *, limit: int = 20) -> list[QueuedLineIntake]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT event_id, prompt, context_json FROM intake "
                "WHERE state='pending' ORDER BY created_at LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [
            QueuedLineIntake(
                event_id=str(row["event_id"]),
                prompt=str(row["prompt"]),
                context=json.loads(str(row["context_json"])),
            )
            for row in rows
        ]

    def claim(self, event_id: str) -> bool:
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE intake SET state='processing', updated_at=? "
                "WHERE event_id=? AND state='pending'",
                (time.time(), event_id),
            )
        return cursor.rowcount == 1

    def release(self, event_id: str) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                "UPDATE intake SET state='pending', updated_at=? "
                "WHERE event_id=? AND state='processing'",
                (time.time(), event_id),
            )

    def complete(self, event_id: str) -> None:
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE intake SET state='completed', context_json='{}', prompt='', updated_at=? "
                "WHERE event_id=? AND state='processing'",
                (time.time(), event_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("LINE intake queue claim is unavailable")

    def recover_stale(self, *, older_than_seconds: float = 300.0) -> int:
        cutoff = time.time() - max(30.0, float(older_than_seconds))
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE intake SET state='pending', updated_at=? "
                "WHERE state='processing' AND updated_at < ?",
                (time.time(), cutoff),
            )
        return cursor.rowcount
