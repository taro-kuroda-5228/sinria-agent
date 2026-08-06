"""Local durable state for Sinria multiplayer collaboration.

This store shares Sinria's profile-aware ``state.db`` while owning only tables
prefixed with ``collaboration_``.  It stores operational proposal content
locally, but append-only audit rows contain digests rather than raw content.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Iterable, Optional

from sinria_constants import get_sinria_home

from gateway.collaboration import (
    CollaborationAuditEvent,
    ConflictError,
    Handoff,
    HandoffStatus,
    InvalidState,
    Participant,
    PermissionDenied,
    PresenceState,
    Proposal,
    WorkItem,
    WorkItemStatus,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collaboration_participants (
    session_key TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    last_seen REAL NOT NULL,
    PRIMARY KEY (session_key, actor_id)
);
CREATE TABLE IF NOT EXISTS collaboration_work_items (
    id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    owner_actor_id TEXT,
    requester_actor_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS collaboration_one_active_per_session
ON collaboration_work_items(session_key)
WHERE status NOT IN ('completed', 'cancelled');
CREATE TABLE IF NOT EXISTS collaboration_proposals (
    id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    resolved_at REAL,
    resolved_by TEXT
);
CREATE INDEX IF NOT EXISTS collaboration_proposals_work_item
ON collaboration_proposals(work_item_id, status, created_at);
CREATE TABLE IF NOT EXISTS collaboration_handoffs (
    id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL,
    from_actor_id TEXT NOT NULL,
    target_actor_id TEXT NOT NULL,
    work_item_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL,
    resolved_at REAL
);
CREATE INDEX IF NOT EXISTS collaboration_handoffs_work_item
ON collaboration_handoffs(work_item_id, status, created_at);
CREATE TABLE IF NOT EXISTS collaboration_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_item_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    work_item_version INTEGER NOT NULL,
    subject_id TEXT,
    payload_sha256 TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS collaboration_events_work_item
ON collaboration_events(work_item_id, id);
"""

_TERMINAL = (WorkItemStatus.COMPLETED.value, WorkItemStatus.CANCELLED.value)


class CollaborationStore:
    """SQLite-backed collaboration state with optimistic concurrency control."""

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path) if db_path else get_sinria_home() / "state.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), timeout=30.0, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "CollaborationStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _begin(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _now(now: Optional[float]) -> float:
        return time.time() if now is None else float(now)

    @staticmethod
    def _work_item(row: sqlite3.Row) -> WorkItem:
        return WorkItem(
            id=row["id"],
            session_key=row["session_key"],
            owner_actor_id=row["owner_actor_id"],
            requester_actor_id=row["requester_actor_id"],
            platform=row["platform"],
            status=WorkItemStatus(row["status"]),
            version=int(row["version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _proposal(row: sqlite3.Row) -> Proposal:
        return Proposal(
            id=row["id"],
            work_item_id=row["work_item_id"],
            actor_id=row["actor_id"],
            content=row["content"],
            content_sha256=row["content_sha256"],
            status=row["status"],
            created_at=float(row["created_at"]),
            resolved_at=row["resolved_at"],
            resolved_by=row["resolved_by"],
        )

    @staticmethod
    def _handoff(row: sqlite3.Row) -> Handoff:
        return Handoff(
            id=row["id"],
            work_item_id=row["work_item_id"],
            from_actor_id=row["from_actor_id"],
            target_actor_id=row["target_actor_id"],
            work_item_version=int(row["work_item_version"]),
            status=HandoffStatus(row["status"]),
            created_at=float(row["created_at"]),
            expires_at=row["expires_at"],
            resolved_at=row["resolved_at"],
        )

    def _append_event(
        self,
        work_item_id: str,
        event_type: str,
        actor_id: str,
        work_item_version: int,
        now: float,
        subject_id: Optional[str] = None,
        payload_sha256: Optional[str] = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO collaboration_events
               (work_item_id, event_type, actor_id, work_item_version,
                subject_id, payload_sha256, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                work_item_id,
                event_type,
                actor_id,
                work_item_version,
                subject_id,
                payload_sha256,
                now,
            ),
        )

    def touch_participant(
        self,
        session_key: str,
        actor_id: str,
        platform: str,
        now: Optional[float] = None,
    ) -> None:
        timestamp = self._now(now)
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO collaboration_participants
                   (session_key, actor_id, platform, last_seen)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(session_key, actor_id) DO UPDATE SET
                       platform=excluded.platform,
                       last_seen=MAX(last_seen, excluded.last_seen)""",
                (session_key, actor_id, platform, timestamp),
            )

    def list_participants(
        self,
        session_key: str,
        now: Optional[float] = None,
        active_ttl: float = 300.0,
    ) -> list[Participant]:
        timestamp = self._now(now)
        with self._lock:
            rows = self._conn.execute(
                """SELECT session_key, actor_id, platform, last_seen
                   FROM collaboration_participants
                   WHERE session_key = ? ORDER BY last_seen DESC""",
                (session_key,),
            ).fetchall()
        return [
            Participant(
                session_key=row["session_key"],
                actor_id=row["actor_id"],
                platform=row["platform"],
                last_seen=float(row["last_seen"]),
                presence=(
                    PresenceState.ACTIVE
                    if timestamp - float(row["last_seen"]) <= active_ttl
                    else PresenceState.IDLE
                ),
            )
            for row in rows
        ]

    def get_or_create_active_work_item(
        self,
        session_key: str,
        actor_id: str,
        platform: str,
        now: Optional[float] = None,
    ) -> WorkItem:
        timestamp = self._now(now)
        with self._lock:
            self._begin()
            try:
                row = self._conn.execute(
                    """SELECT * FROM collaboration_work_items
                       WHERE session_key = ? AND status NOT IN (?, ?)
                       ORDER BY created_at DESC LIMIT 1""",
                    (session_key, *_TERMINAL),
                ).fetchone()
                if row is not None:
                    self._conn.commit()
                    return self._work_item(row)
                item_id = uuid.uuid4().hex
                self._conn.execute(
                    """INSERT INTO collaboration_work_items
                       (id, session_key, owner_actor_id, requester_actor_id,
                        platform, status, version, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (
                        item_id,
                        session_key,
                        actor_id,
                        actor_id,
                        platform,
                        WorkItemStatus.ACTIVE.value,
                        timestamp,
                        timestamp,
                    ),
                )
                self._append_event(
                    item_id, "work_item.created", actor_id, 1, timestamp
                )
                row = self._conn.execute(
                    "SELECT * FROM collaboration_work_items WHERE id = ?",
                    (item_id,),
                ).fetchone()
                self._conn.commit()
                return self._work_item(row)
            except BaseException:
                self._conn.rollback()
                raise

    def get_work_item(self, work_item_id: str) -> Optional[WorkItem]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM collaboration_work_items WHERE id = ?",
                (work_item_id,),
            ).fetchone()
        return self._work_item(row) if row is not None else None

    def get_active_work_item(self, session_key: str) -> Optional[WorkItem]:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM collaboration_work_items
                   WHERE session_key = ? AND status NOT IN (?, ?)
                   ORDER BY created_at DESC LIMIT 1""",
                (session_key, *_TERMINAL),
            ).fetchone()
        return self._work_item(row) if row is not None else None

    def _require_version(self, work_item_id: str, expected_version: int) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM collaboration_work_items WHERE id = ?",
            (work_item_id,),
        ).fetchone()
        if row is None:
            raise InvalidState("work item does not exist")
        if int(row["version"]) != int(expected_version):
            raise ConflictError(
                f"stale work item version: expected {expected_version}, current {row['version']}"
            )
        return row

    def transition_work_item(
        self,
        work_item_id: str,
        expected_version: int,
        status: WorkItemStatus,
        actor_id: str,
        now: Optional[float] = None,
    ) -> WorkItem:
        timestamp = self._now(now)
        with self._lock:
            self._begin()
            try:
                row = self._require_version(work_item_id, expected_version)
                current = WorkItemStatus(row["status"])
                if current.terminal:
                    raise InvalidState("terminal work item cannot transition")
                version = expected_version + 1
                self._conn.execute(
                    """UPDATE collaboration_work_items
                       SET status = ?, version = ?, updated_at = ?
                       WHERE id = ? AND version = ?""",
                    (status.value, version, timestamp, work_item_id, expected_version),
                )
                self._append_event(
                    work_item_id,
                    f"work_item.{status.value}",
                    actor_id,
                    version,
                    timestamp,
                )
                updated = self._conn.execute(
                    "SELECT * FROM collaboration_work_items WHERE id = ?",
                    (work_item_id,),
                ).fetchone()
                self._conn.commit()
                return self._work_item(updated)
            except BaseException:
                self._conn.rollback()
                raise

    def release_owner(
        self,
        work_item_id: str,
        expected_version: int,
        actor_id: str,
        now: Optional[float] = None,
    ) -> WorkItem:
        return self._change_owner(
            work_item_id, expected_version, actor_id, None, False, "owner.released", now
        )

    def claim_work_item(
        self,
        work_item_id: str,
        expected_version: int,
        actor_id: str,
        force: bool = False,
        now: Optional[float] = None,
    ) -> WorkItem:
        return self._change_owner(
            work_item_id, expected_version, actor_id, actor_id, force, "owner.claimed", now
        )

    def _change_owner(
        self,
        work_item_id: str,
        expected_version: int,
        actor_id: str,
        new_owner: Optional[str],
        force: bool,
        event_type: str,
        now: Optional[float],
    ) -> WorkItem:
        timestamp = self._now(now)
        with self._lock:
            self._begin()
            try:
                row = self._require_version(work_item_id, expected_version)
                owner = row["owner_actor_id"]
                if new_owner is None:
                    if not force and owner != actor_id:
                        raise PermissionDenied("only the current owner can release")
                elif owner is not None and owner != actor_id and not force:
                    raise PermissionDenied("work item already has an owner")
                version = expected_version + 1
                self._conn.execute(
                    """UPDATE collaboration_work_items
                       SET owner_actor_id = ?, version = ?, updated_at = ?
                       WHERE id = ? AND version = ?""",
                    (new_owner, version, timestamp, work_item_id, expected_version),
                )
                self._append_event(
                    work_item_id, event_type, actor_id, version, timestamp
                )
                updated = self._conn.execute(
                    "SELECT * FROM collaboration_work_items WHERE id = ?",
                    (work_item_id,),
                ).fetchone()
                self._conn.commit()
                return self._work_item(updated)
            except BaseException:
                self._conn.rollback()
                raise

    def create_proposal(
        self,
        work_item_id: str,
        expected_version: int,
        actor_id: str,
        content: str,
        now: Optional[float] = None,
    ) -> Proposal:
        if not content.strip():
            raise InvalidState("proposal content cannot be empty")
        timestamp = self._now(now)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        proposal_id = uuid.uuid4().hex
        with self._lock:
            self._begin()
            try:
                self._require_version(work_item_id, expected_version)
                self._conn.execute(
                    """INSERT INTO collaboration_proposals
                       (id, work_item_id, actor_id, content, content_sha256,
                        status, created_at)
                       VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                    (proposal_id, work_item_id, actor_id, content, digest, timestamp),
                )
                self._append_event(
                    work_item_id,
                    "proposal.created",
                    actor_id,
                    expected_version,
                    timestamp,
                    subject_id=proposal_id,
                    payload_sha256=digest,
                )
                row = self._conn.execute(
                    "SELECT * FROM collaboration_proposals WHERE id = ?",
                    (proposal_id,),
                ).fetchone()
                self._conn.commit()
                return self._proposal(row)
            except BaseException:
                self._conn.rollback()
                raise

    def list_proposals(
        self, work_item_id: str, status: Optional[str] = None
    ) -> list[Proposal]:
        sql = "SELECT * FROM collaboration_proposals WHERE work_item_id = ?"
        params: list[object] = [work_item_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at, id"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._proposal(row) for row in rows]

    def resolve_proposal(
        self,
        proposal_id: str,
        item_id: str,
        expected_version: int,
        actor_id: str,
        resolution: str,
        now: Optional[float] = None,
    ) -> Proposal:
        if resolution not in {"accepted", "rejected"}:
            raise InvalidState("proposal resolution must be accepted or rejected")
        timestamp = self._now(now)
        with self._lock:
            self._begin()
            try:
                item = self._require_version(item_id, expected_version)
                if item["owner_actor_id"] != actor_id:
                    raise PermissionDenied("only the owner can resolve proposals")
                row = self._conn.execute(
                    "SELECT * FROM collaboration_proposals WHERE id = ? AND work_item_id = ?",
                    (proposal_id, item_id),
                ).fetchone()
                if row is None or row["status"] != "pending":
                    raise InvalidState("proposal is not pending")
                self._conn.execute(
                    """UPDATE collaboration_proposals
                       SET status = ?, resolved_at = ?, resolved_by = ? WHERE id = ?""",
                    (resolution, timestamp, actor_id, proposal_id),
                )
                self._append_event(
                    item_id,
                    f"proposal.{resolution}",
                    actor_id,
                    expected_version,
                    timestamp,
                    subject_id=proposal_id,
                    payload_sha256=row["content_sha256"],
                )
                updated = self._conn.execute(
                    "SELECT * FROM collaboration_proposals WHERE id = ?",
                    (proposal_id,),
                ).fetchone()
                self._conn.commit()
                return self._proposal(updated)
            except BaseException:
                self._conn.rollback()
                raise

    def offer_handoff(
        self,
        work_item_id: str,
        expected_version: int,
        actor_id: str,
        target_actor_id: str,
        now: Optional[float] = None,
        expires_at: Optional[float] = None,
    ) -> Handoff:
        timestamp = self._now(now)
        if actor_id == target_actor_id:
            raise InvalidState("handoff target must differ from owner")
        handoff_id = uuid.uuid4().hex
        with self._lock:
            self._begin()
            try:
                item = self._require_version(work_item_id, expected_version)
                if item["owner_actor_id"] != actor_id:
                    raise PermissionDenied("only the current owner can offer handoff")
                pending = self._conn.execute(
                    """SELECT 1 FROM collaboration_handoffs
                       WHERE work_item_id = ? AND status = 'offered'""",
                    (work_item_id,),
                ).fetchone()
                if pending:
                    raise InvalidState("a handoff is already pending")
                self._conn.execute(
                    """INSERT INTO collaboration_handoffs
                       (id, work_item_id, from_actor_id, target_actor_id,
                        work_item_version, status, created_at, expires_at)
                       VALUES (?, ?, ?, ?, ?, 'offered', ?, ?)""",
                    (
                        handoff_id,
                        work_item_id,
                        actor_id,
                        target_actor_id,
                        expected_version,
                        timestamp,
                        expires_at,
                    ),
                )
                self._append_event(
                    work_item_id,
                    "handoff.offered",
                    actor_id,
                    expected_version,
                    timestamp,
                    subject_id=handoff_id,
                )
                row = self._conn.execute(
                    "SELECT * FROM collaboration_handoffs WHERE id = ?",
                    (handoff_id,),
                ).fetchone()
                self._conn.commit()
                return self._handoff(row)
            except BaseException:
                self._conn.rollback()
                raise

    def get_handoff(self, handoff_id: str) -> Optional[Handoff]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM collaboration_handoffs WHERE id = ?", (handoff_id,)
            ).fetchone()
        return self._handoff(row) if row else None

    def get_pending_handoff(self, work_item_id: str) -> Optional[Handoff]:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM collaboration_handoffs
                   WHERE work_item_id = ? AND status = 'offered'
                   ORDER BY created_at DESC LIMIT 1""",
                (work_item_id,),
            ).fetchone()
        return self._handoff(row) if row else None

    def accept_handoff(
        self,
        handoff_id: str,
        expected_version: int,
        actor_id: str,
        now: Optional[float] = None,
    ) -> tuple[Handoff, WorkItem]:
        timestamp = self._now(now)
        with self._lock:
            self._begin()
            try:
                handoff = self._conn.execute(
                    "SELECT * FROM collaboration_handoffs WHERE id = ?", (handoff_id,)
                ).fetchone()
                if handoff is None or handoff["status"] != HandoffStatus.OFFERED.value:
                    raise InvalidState("handoff is not pending")
                if handoff["target_actor_id"] != actor_id:
                    raise PermissionDenied("only the target can accept handoff")
                if handoff["expires_at"] is not None and handoff["expires_at"] <= timestamp:
                    raise InvalidState("handoff has expired")
                item = self._require_version(handoff["work_item_id"], expected_version)
                if int(handoff["work_item_version"]) != expected_version:
                    raise ConflictError("handoff was offered for a stale work item version")
                if item["owner_actor_id"] != handoff["from_actor_id"]:
                    raise ConflictError("work item owner changed after handoff offer")
                version = expected_version + 1
                self._conn.execute(
                    """UPDATE collaboration_work_items
                       SET owner_actor_id = ?, version = ?, updated_at = ?
                       WHERE id = ? AND version = ?""",
                    (
                        actor_id,
                        version,
                        timestamp,
                        handoff["work_item_id"],
                        expected_version,
                    ),
                )
                self._conn.execute(
                    """UPDATE collaboration_handoffs
                       SET status = 'accepted', resolved_at = ? WHERE id = ?""",
                    (timestamp, handoff_id),
                )
                self._append_event(
                    handoff["work_item_id"],
                    "handoff.accepted",
                    actor_id,
                    version,
                    timestamp,
                    subject_id=handoff_id,
                )
                updated_handoff = self._conn.execute(
                    "SELECT * FROM collaboration_handoffs WHERE id = ?", (handoff_id,)
                ).fetchone()
                updated_item = self._conn.execute(
                    "SELECT * FROM collaboration_work_items WHERE id = ?",
                    (handoff["work_item_id"],),
                ).fetchone()
                self._conn.commit()
                return self._handoff(updated_handoff), self._work_item(updated_item)
            except BaseException:
                self._conn.rollback()
                raise

    def _resolve_handoff(
        self,
        handoff_id: str,
        expected_version: int,
        actor_id: str,
        status: HandoffStatus,
        target_must_match: bool,
        now: Optional[float],
    ) -> Handoff:
        timestamp = self._now(now)
        with self._lock:
            self._begin()
            try:
                handoff = self._conn.execute(
                    "SELECT * FROM collaboration_handoffs WHERE id = ?", (handoff_id,)
                ).fetchone()
                if handoff is None or handoff["status"] != HandoffStatus.OFFERED.value:
                    raise InvalidState("handoff is not pending")
                self._require_version(handoff["work_item_id"], expected_version)
                if int(handoff["work_item_version"]) != expected_version:
                    raise ConflictError("handoff was offered for a stale work item version")
                required = (
                    handoff["target_actor_id"]
                    if target_must_match
                    else handoff["from_actor_id"]
                )
                if actor_id != required:
                    raise PermissionDenied("actor cannot resolve this handoff")
                self._conn.execute(
                    """UPDATE collaboration_handoffs
                       SET status = ?, resolved_at = ? WHERE id = ?""",
                    (status.value, timestamp, handoff_id),
                )
                self._append_event(
                    handoff["work_item_id"],
                    f"handoff.{status.value}",
                    actor_id,
                    expected_version,
                    timestamp,
                    subject_id=handoff_id,
                )
                updated = self._conn.execute(
                    "SELECT * FROM collaboration_handoffs WHERE id = ?", (handoff_id,)
                ).fetchone()
                self._conn.commit()
                return self._handoff(updated)
            except BaseException:
                self._conn.rollback()
                raise

    def reject_handoff(
        self, handoff_id: str, expected_version: int, actor_id: str, now: Optional[float] = None
    ) -> Handoff:
        return self._resolve_handoff(
            handoff_id, expected_version, actor_id, HandoffStatus.REJECTED, True, now
        )

    def cancel_handoff(
        self, handoff_id: str, expected_version: int, actor_id: str, now: Optional[float] = None
    ) -> Handoff:
        return self._resolve_handoff(
            handoff_id, expected_version, actor_id, HandoffStatus.CANCELLED, False, now
        )

    def expire_handoffs(self, now: Optional[float] = None) -> int:
        timestamp = self._now(now)
        with self._lock, self._conn:
            rows = self._conn.execute(
                """SELECT * FROM collaboration_handoffs
                   WHERE status = 'offered' AND expires_at IS NOT NULL AND expires_at <= ?""",
                (timestamp,),
            ).fetchall()
            for row in rows:
                self._conn.execute(
                    """UPDATE collaboration_handoffs
                       SET status = 'expired', resolved_at = ? WHERE id = ?""",
                    (timestamp, row["id"]),
                )
                self._append_event(
                    row["work_item_id"],
                    "handoff.expired",
                    "system",
                    int(row["work_item_version"]),
                    timestamp,
                    subject_id=row["id"],
                )
            return len(rows)

    def list_audit_events(self, work_item_id: str) -> list[CollaborationAuditEvent]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM collaboration_events
                   WHERE work_item_id = ? ORDER BY id""",
                (work_item_id,),
            ).fetchall()
        return [
            CollaborationAuditEvent(
                id=int(row["id"]),
                work_item_id=row["work_item_id"],
                event_type=row["event_type"],
                actor_id=row["actor_id"],
                work_item_version=int(row["work_item_version"]),
                subject_id=row["subject_id"],
                payload_sha256=row["payload_sha256"],
                created_at=float(row["created_at"]),
            )
            for row in rows
        ]
