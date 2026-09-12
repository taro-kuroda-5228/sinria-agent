"""Privacy-safe evidence for explicit human replies on LINE."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_ALLOWED_SOURCE_TYPES = {"user", "group", "room"}
_ALLOWED_PURPOSES = {"peer_onboarding", "external_action_confirmation", "delivery_confirmation"}


@dataclass(frozen=True)
class LineHumanConfirmationReceipt:
    confirmation_id: str
    purpose: str
    status: str
    human_confirmed: bool
    created_at_ms: int
    confirmed_at_ms: Optional[int] = None


def default_line_human_confirmation_db() -> Path:
    override = os.getenv("SINRIA_LINE_HUMAN_CONFIRMATION_DB", "").strip()
    if override:
        return Path(override).expanduser()
    from sinria_constants import get_sinria_home

    return get_sinria_home() / "private" / "line" / "human-confirmation.sqlite3"


def _digest(namespace: str, value: str) -> str:
    return hashlib.sha256(f"{namespace}\0{value}".encode("utf-8")).hexdigest()


def _required(value: str, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


class LineHumanConfirmationStore:
    """Track API acceptance separately from an explicit, correlated human reply.

    Platform IDs, reply text, and quoted message IDs are stored only as hashes.
    A confirmation requires the expected sender, conversation, source type,
    exact reply text, and quoted outbound message to all match before expiry.
    """

    def __init__(self, db_path: str | Path | None = None, *, ttl_seconds: int = 86400):
        self.db_path = Path(db_path) if db_path is not None else default_line_human_confirmation_db()
        self.ttl_ms = max(60, min(int(ttl_seconds), 7 * 86400)) * 1000
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.db_path.parent, 0o700)
        self._conn = sqlite3.connect(str(self.db_path), timeout=10, check_same_thread=False)
        os.chmod(self.db_path, 0o600)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA secure_delete=ON")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS confirmations (
                confirmation_id TEXT PRIMARY KEY,
                purpose TEXT NOT NULL,
                source_type TEXT NOT NULL,
                conversation_key TEXT NOT NULL,
                sender_key TEXT NOT NULL,
                reply_key TEXT NOT NULL,
                outbound_key TEXT NOT NULL,
                inbound_key TEXT UNIQUE,
                status TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                confirmed_at_ms INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_line_confirmation_pending
                ON confirmations(status, expires_at_ms);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "LineHumanConfirmationStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @staticmethod
    def _receipt(row: sqlite3.Row, *, now_ms: int | None = None) -> LineHumanConfirmationReceipt:
        status = str(row["status"])
        if status == "api_accepted" and now_ms is not None and int(row["expires_at_ms"]) < now_ms:
            status = "expired"
        return LineHumanConfirmationReceipt(
            confirmation_id=str(row["confirmation_id"]),
            purpose=str(row["purpose"]),
            status=status,
            human_confirmed=status == "human_replied",
            created_at_ms=int(row["created_at_ms"]),
            confirmed_at_ms=(int(row["confirmed_at_ms"]) if row["confirmed_at_ms"] is not None else None),
        )

    def register(
        self,
        *,
        conversation_id: str,
        source_type: str,
        expected_sender_id: str,
        expected_reply: str,
        outbound_message_id: str,
        purpose: str,
        now_ms: int | None = None,
    ) -> LineHumanConfirmationReceipt:
        source_type = _required(source_type, "source_type")
        purpose = _required(purpose, "purpose")
        if source_type not in _ALLOWED_SOURCE_TYPES:
            raise ValueError("invalid LINE source type")
        if purpose not in _ALLOWED_PURPOSES:
            raise ValueError("invalid LINE confirmation purpose")
        conversation_key = _digest("line-confirmation-conversation", _required(conversation_id, "conversation_id"))
        sender_key = _digest("line-confirmation-sender", _required(expected_sender_id, "expected_sender_id"))
        reply_key = _digest("line-confirmation-reply", _required(expected_reply, "expected_reply"))
        outbound_key = _digest("line-confirmation-outbound", _required(outbound_message_id, "outbound_message_id"))
        confirmation_id = _digest(
            "line-confirmation",
            "\0".join((purpose, source_type, conversation_key, sender_key, outbound_key)),
        )
        created_at_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        with self._conn:
            self._conn.execute(
                """INSERT OR IGNORE INTO confirmations (
                       confirmation_id, purpose, source_type, conversation_key, sender_key,
                       reply_key, outbound_key, status, created_at_ms, expires_at_ms
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'api_accepted', ?, ?)""",
                (
                    confirmation_id, purpose, source_type, conversation_key, sender_key,
                    reply_key, outbound_key, created_at_ms, created_at_ms + self.ttl_ms,
                ),
            )
        return self.get(confirmation_id, now_ms=created_at_ms)

    def get(self, confirmation_id: str, *, now_ms: int | None = None) -> LineHumanConfirmationReceipt:
        row = self._conn.execute(
            "SELECT * FROM confirmations WHERE confirmation_id = ?", (str(confirmation_id or ""),)
        ).fetchone()
        if row is None:
            raise KeyError("LINE confirmation not found")
        return self._receipt(row, now_ms=now_ms)

    def observe(
        self,
        *,
        conversation_id: str,
        source_type: str,
        sender_id: str,
        text: str,
        quoted_message_id: str,
        inbound_message_id: str,
        now_ms: int | None = None,
    ) -> LineHumanConfirmationReceipt | None:
        values = (conversation_id, source_type, sender_id, text, quoted_message_id, inbound_message_id)
        if not all(str(value or "").strip() for value in values):
            return None
        if source_type not in _ALLOWED_SOURCE_TYPES:
            return None
        now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        keys = (
            source_type,
            _digest("line-confirmation-conversation", str(conversation_id).strip()),
            _digest("line-confirmation-sender", str(sender_id).strip()),
            _digest("line-confirmation-reply", str(text).strip()),
            _digest("line-confirmation-outbound", str(quoted_message_id).strip()),
        )
        row = self._conn.execute(
            """SELECT * FROM confirmations
               WHERE source_type = ? AND conversation_key = ? AND sender_key = ?
                 AND reply_key = ? AND outbound_key = ?
                 AND status IN ('api_accepted', 'human_replied')
               ORDER BY created_at_ms DESC LIMIT 1""",
            keys,
        ).fetchone()
        if row is None or int(row["expires_at_ms"]) < now_ms:
            return None
        inbound_key = _digest("line-confirmation-inbound", str(inbound_message_id).strip())
        with self._conn:
            self._conn.execute(
                """UPDATE confirmations
                   SET status='human_replied', inbound_key=COALESCE(inbound_key, ?),
                       confirmed_at_ms=COALESCE(confirmed_at_ms, ?)
                   WHERE confirmation_id = ?""",
                (inbound_key, now_ms, row["confirmation_id"]),
            )
        return self.get(str(row["confirmation_id"]), now_ms=now_ms)
