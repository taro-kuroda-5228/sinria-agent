"""Durable local journal for Sinria Workspace channels (Slice 2).

The first-party workspace (Workspace › Space › Conversation) needs restart
recovery that is exact: after an app/network/Gateway restart every in-flight
channel resumes once — not zero times, not twice — and duplicate event
delivery never duplicates side effects.

This journal is the local/on-prem durability layer that makes those guarantees
enforceable in code:

  - **inbox**: inbound event dedupe keyed by (channel_key, idempotency_key).
  - **runs**: run lifecycle rows; ``recover_interrupted_runs`` marks every
    non-terminal run ``interrupted`` exactly once (guarded by ``recovered_at``).
  - **outbox**: outbound deliveries dedupe on (channel_key, idempotency_key)
    and transition ``pending → delivered`` at most once.
  - **approvals**: digest-bound approvals execute at most once, only after an
    explicit decision, only for the exact approved payload sha256.

Storage is SQLite (WAL) under ``get_sinria_home()/workspace/journal.db`` —
profile-aware, local-only. The journal stores sanitized metadata and opaque
digests; message bodies stay in the session store, raw payloads stay with the
executor. Every mutation is a single atomic statement whose WHERE clause
encodes the state precondition, so concurrent processes cannot double-fire.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from sinria_constants import get_sinria_home

__all__ = [
    "AlreadyExecutedError",
    "ApprovalStateError",
    "DigestMismatchError",
    "InboundRecord",
    "WorkspaceJournal",
]

# Run statuses considered "in flight" for restart recovery. Mirrors the
# api_server run lifecycle plus the journal's own "interrupted" marker.
_IN_FLIGHT_STATUSES = ("queued", "running", "waiting_for_approval", "stopping")
_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


class ApprovalStateError(RuntimeError):
    """The approval is not in a state that permits the requested transition."""


class AlreadyExecutedError(ApprovalStateError):
    """The approval's single execution has already been consumed."""


class DigestMismatchError(ApprovalStateError):
    """The payload digest does not match the digest that was approved."""


@dataclass(frozen=True)
class InboundRecord:
    """Outcome of recording an inbound event."""

    channel_key: str
    idempotency_key: str
    accepted: bool


_SCHEMA = """
create table if not exists inbox (
  channel_key text not null,
  idempotency_key text not null,
  kind text not null,
  sanitized_preview text not null default '',
  received_at real not null,
  run_id text,
  primary key (channel_key, idempotency_key)
);
create table if not exists runs (
  run_id text primary key,
  channel_key text not null,
  session_id text,
  status text not null,
  attempt integer not null default 1 check (attempt >= 1),
  idempotency_key text,
  parent_run_id text,
  request_json text,
  gateway_session_key text,
  workspace_boundary text,
  sanitized_note text not null default '',
  created_at real not null,
  updated_at real not null,
  completed_at real,
  recovered_at real
);
create index if not exists runs_idem_idx on runs(idempotency_key) where idempotency_key is not null;
create unique index if not exists idx_runs_idempotency_attempt
  on runs(idempotency_key, attempt) where idempotency_key is not null;
create index if not exists runs_channel_idx on runs(channel_key);
create table if not exists outbox (
  outbox_id text primary key,
  channel_key text not null,
  run_id text,
  kind text not null,
  idempotency_key text not null,
  payload_sha256 text,
  status text not null default 'pending' check (status in ('pending', 'delivered')),
  attempts integer not null default 0,
  created_at real not null,
  delivered_at real,
  unique (channel_key, idempotency_key)
);
create table if not exists connector_bindings (
  binding_id text primary key,
  platform text not null check (platform in ('discord', 'slack')),
  external_chat_id text not null,
  external_thread_id text not null default '',
  workspace_id text not null,
  space_id text not null,
  conversation_id text not null,
  boundary text not null check (boundary in ('private', 'internal', 'partner', 'clinical')),
  enabled integer not null default 1 check (enabled in (0, 1)),
  created_at real not null,
  updated_at real not null,
  unique (platform, external_chat_id, external_thread_id)
);
create table if not exists approvals (
  approval_id text primary key,
  run_id text,
  channel_key text not null default '',
  action_kind text not null,
  payload_sha256 text not null,
  sanitized_summary text not null default '',
  status text not null default 'pending' check (status in ('pending', 'approved', 'rejected', 'expired', 'executed')),
  decided_by text,
  decided_at real,
  executed_at real,
  execution_count integer not null default 0 check (execution_count in (0, 1)),
  created_at real not null
);
create table if not exists task_bindings (
  binding_id text primary key,
  task_id text not null,
  channel_key text not null,
  source_message_ref text not null unique,
  relation text not null check (relation in ('origin', 'participant', 'approval')),
  created_at real not null
);
create index if not exists task_bindings_task_idx on task_bindings(task_id);
create table if not exists task_executions (
  execution_id text primary key,
  task_id text not null,
  task_revision integer not null check (task_revision >= 1),
  owner_instance_id text not null,
  idempotency_key text not null,
  status text not null check (status in ('active', 'completed', 'released', 'expired')),
  lease_expires_at real not null,
  created_at real not null,
  updated_at real not null,
  unique (task_id, task_revision, idempotency_key)
);
create index if not exists task_executions_active_idx
  on task_executions(task_id, task_revision, status, lease_expires_at);
create table if not exists resource_claims (
  claim_id text primary key,
  task_id text not null,
  execution_id text not null,
  resource_scope text not null,
  mode text not null check (mode in ('read', 'write', 'side_effect')),
  status text not null check (status in ('active', 'released', 'expired')),
  lease_expires_at real not null,
  created_at real not null
);
create index if not exists resource_claims_active_idx
  on resource_claims(status, lease_expires_at);
"""


class WorkspaceJournal:
    """SQLite-backed durable journal. Safe for multi-process use (WAL +
    single-statement state transitions); connections are per-call."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else get_sinria_home() / "workspace" / "journal.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            columns = {row["name"] for row in conn.execute("pragma table_info(runs)")}
            if "request_json" not in columns:
                conn.execute("alter table runs add column request_json text")
            if "gateway_session_key" not in columns:
                conn.execute("alter table runs add column gateway_session_key text")
            if "workspace_boundary" not in columns:
                conn.execute("alter table runs add column workspace_boundary text")
            conn.execute(
                "create unique index if not exists idx_runs_idempotency_attempt "
                "on runs(idempotency_key, attempt) where idempotency_key is not null"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma journal_mode=wal")
        conn.execute("pragma synchronous=normal")
        return conn

    # ------------------------------------------------------------------
    # Inbox
    # ------------------------------------------------------------------

    def record_inbound(
        self,
        *,
        channel_key: str,
        idempotency_key: str,
        kind: str = "user_message",
        sanitized_preview: str = "",
        run_id: Optional[str] = None,
    ) -> InboundRecord:
        """Record an inbound event; duplicates are reported, never re-accepted."""
        with self._connect() as conn:
            cur = conn.execute(
                "insert or ignore into inbox"
                " (channel_key, idempotency_key, kind, sanitized_preview, received_at, run_id)"
                " values (?, ?, ?, ?, ?, ?)",
                (channel_key, idempotency_key, kind, sanitized_preview, time.time(), run_id),
            )
            return InboundRecord(
                channel_key=channel_key,
                idempotency_key=idempotency_key,
                accepted=cur.rowcount == 1,
            )

    # ------------------------------------------------------------------
    # Cross-channel task coordination
    # ------------------------------------------------------------------

    def bind_task(
        self,
        task_id: str,
        channel_key: str,
        source_message_ref: str,
        relation: str,
    ) -> Tuple[Dict[str, Any], bool]:
        """Bind a source message once; retries cannot silently rebind it."""
        now = time.time()
        binding_id = f"binding_{uuid.uuid4().hex}"
        with self._connect() as conn:
            cur = conn.execute(
                "insert or ignore into task_bindings"
                " (binding_id, task_id, channel_key, source_message_ref, relation, created_at)"
                " values (?, ?, ?, ?, ?, ?)",
                (binding_id, task_id, channel_key, source_message_ref, relation, now),
            )
            row = conn.execute(
                "select * from task_bindings where source_message_ref = ?",
                (source_message_ref,),
            ).fetchone()
        return dict(row), cur.rowcount == 1

    def find_task_by_message(self, source_message_ref: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "select task_id from task_bindings where source_message_ref = ?",
                (source_message_ref,),
            ).fetchone()
        return str(row["task_id"]) if row else None

    def resolve_or_create_task_binding(
        self,
        *,
        channel_key: str,
        source_message_ref: str,
        reply_to_message_ref: Optional[str] = None,
        explicit_task_id: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        """Atomically resolve and bind one inbound message to a durable task.

        Existing source identity wins, followed by reply binding, an existing
        explicit task, and the most recent task in the canonical Workspace
        conversation. ``BEGIN IMMEDIATE`` prevents concurrent first messages
        from creating two tasks for one previously-empty conversation.
        """
        channel_key = str(channel_key).strip()
        source_message_ref = str(source_message_ref).strip()
        reply_to_message_ref = str(reply_to_message_ref or "").strip()
        explicit_task_id = str(explicit_task_id or "").strip()
        if not channel_key or not source_message_ref:
            raise ValueError("channel_key and source_message_ref must be non-empty")

        now = time.time()
        with self._connect() as conn:
            conn.execute("begin immediate")
            existing = conn.execute(
                "select * from task_bindings where source_message_ref = ?",
                (source_message_ref,),
            ).fetchone()
            if existing is not None:
                return dict(existing), False

            selected_task_id = ""
            relation = "origin"
            if reply_to_message_ref:
                row = conn.execute(
                    "select task_id from task_bindings where source_message_ref = ?",
                    (reply_to_message_ref,),
                ).fetchone()
                if row is not None:
                    selected_task_id = str(row["task_id"])
                    relation = "participant"

            if not selected_task_id and explicit_task_id:
                row = conn.execute(
                    "select task_id from task_bindings where task_id = ? limit 1",
                    (explicit_task_id,),
                ).fetchone()
                if row is not None:
                    selected_task_id = explicit_task_id
                    relation = "participant"

            if not selected_task_id:
                row = conn.execute(
                    "select task_id from task_bindings where channel_key = ?"
                    " order by created_at desc, binding_id desc limit 1",
                    (channel_key,),
                ).fetchone()
                if row is not None:
                    selected_task_id = str(row["task_id"])
                    relation = "participant"

            if not selected_task_id:
                selected_task_id = f"task_{uuid.uuid4().hex}"

            binding_id = f"binding_{uuid.uuid4().hex}"
            conn.execute(
                "insert into task_bindings"
                " (binding_id, task_id, channel_key, source_message_ref, relation, created_at)"
                " values (?, ?, ?, ?, ?, ?)",
                (
                    binding_id,
                    selected_task_id,
                    channel_key,
                    source_message_ref,
                    relation,
                    now,
                ),
            )
            row = conn.execute(
                "select * from task_bindings where binding_id = ?",
                (binding_id,),
            ).fetchone()
        return dict(row), True

    def claim_task_execution(
        self,
        *,
        task_id: str,
        task_revision: int,
        owner_instance_id: str,
        idempotency_key: str,
        lease_seconds: float,
    ) -> Dict[str, Any]:
        """Atomically enforce one live execution per task revision."""
        now = time.time()
        with self._connect() as conn:
            conn.execute("begin immediate")
            retry = conn.execute(
                "select * from task_executions where task_id = ? and task_revision = ?"
                " and idempotency_key = ?",
                (task_id, task_revision, idempotency_key),
            ).fetchone()
            if retry:
                return {"ok": True, "execution": dict(retry), "idempotent": True}
            conn.execute(
                "update task_executions set status = 'expired', updated_at = ?"
                " where task_id = ? and task_revision = ? and status = 'active'"
                " and lease_expires_at <= ?",
                (now, task_id, task_revision, now),
            )
            active = conn.execute(
                "select * from task_executions where task_id = ? and task_revision = ?"
                " and status = 'active' and lease_expires_at > ? limit 1",
                (task_id, task_revision, now),
            ).fetchone()
            if active:
                return {"ok": False, "reason": "active_execution", "execution": dict(active)}
            execution_id = f"execution_{uuid.uuid4().hex}"
            conn.execute(
                "insert into task_executions"
                " (execution_id, task_id, task_revision, owner_instance_id, idempotency_key,"
                " status, lease_expires_at, created_at, updated_at)"
                " values (?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                (
                    execution_id, task_id, task_revision, owner_instance_id,
                    idempotency_key, now + lease_seconds, now, now,
                ),
            )
            row = conn.execute(
                "select * from task_executions where execution_id = ?", (execution_id,)
            ).fetchone()
        return {"ok": True, "execution": dict(row), "idempotent": False}

    def claim_resource(
        self,
        *,
        task_id: str,
        execution_id: str,
        resource_scope: str,
        mode: Literal["read", "write", "side_effect"],
        lease_seconds: float,
    ) -> Dict[str, Any]:
        """Claim a normalized scope while preserving unrelated parallel work."""
        from .resource_scope import normalize_resource_scope, resource_scopes_conflict

        scope = normalize_resource_scope(resource_scope)
        now = time.time()
        with self._connect() as conn:
            conn.execute("begin immediate")
            conn.execute(
                "update resource_claims set status = 'expired'"
                " where status = 'active' and lease_expires_at <= ?",
                (now,),
            )
            active = conn.execute(
                "select * from resource_claims where status = 'active' and lease_expires_at > ?",
                (now,),
            ).fetchall()
            for row in active:
                if row["execution_id"] == execution_id and row["resource_scope"] == scope:
                    return {"ok": True, "claim": dict(row), "idempotent": True}
                if resource_scopes_conflict(scope, mode, row["resource_scope"], row["mode"]):
                    return {"ok": False, "reason": "resource_conflict", "claim": dict(row)}
            claim_id = f"claim_{uuid.uuid4().hex}"
            conn.execute(
                "insert into resource_claims"
                " (claim_id, task_id, execution_id, resource_scope, mode, status,"
                " lease_expires_at, created_at) values (?, ?, ?, ?, ?, 'active', ?, ?)",
                (claim_id, task_id, execution_id, scope, mode, now + lease_seconds, now),
            )
            row = conn.execute(
                "select * from resource_claims where claim_id = ?", (claim_id,)
            ).fetchone()
        return {"ok": True, "claim": dict(row), "idempotent": False}

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_run(row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
        record = dict(row)
        raw_request = record.get("request_json")
        if raw_request:
            try:
                record["request"] = json.loads(raw_request)
            except (TypeError, ValueError):
                record["request"] = None
        else:
            record["request"] = None
        return record

    def claim_run_attempt(
        self,
        run_id: str,
        *,
        channel_key: str,
        session_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        attempt: int = 1,
        parent_run_id: Optional[str] = None,
        request: Optional[Dict[str, Any]] = None,
        gateway_session_key: Optional[str] = None,
        workspace_boundary: Optional[str] = None,
        sanitized_note: str = "",
    ) -> Tuple[Dict[str, Any], bool]:
        """Atomically claim an idempotent run and report who created it."""
        row = self.record_run_created(
            run_id,
            channel_key=channel_key,
            session_id=session_id,
            idempotency_key=idempotency_key,
            attempt=attempt,
            parent_run_id=parent_run_id,
            sanitized_note=sanitized_note,
            request_body=request,
            gateway_session_key=gateway_session_key,
            workspace_boundary=workspace_boundary,
        )
        return row, row["run_id"] == run_id

    def record_run_created(
        self,
        run_id: str,
        *,
        channel_key: str,
        session_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        attempt: int = 1,
        parent_run_id: Optional[str] = None,
        sanitized_note: str = "",
        request_body: Optional[Dict[str, Any]] = None,
        gateway_session_key: Optional[str] = None,
        workspace_boundary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Atomically reserve a run attempt.

        Returning an existing row is intentional: the caller compares the
        returned ``run_id`` with its candidate id before launching work.  This
        closes the check-then-insert race between duplicate mobile requests.
        The raw request is stored only in this local/on-prem journal so a
        Gateway restart can resume without sending PHI through a cloud plane.
        """
        now = time.time()
        request_json = json.dumps(request_body, ensure_ascii=False) if request_body is not None else None
        with self._connect() as conn:
            conn.execute("begin immediate")
            if idempotency_key:
                existing = conn.execute(
                    "select * from runs where idempotency_key = ? "
                    "order by attempt desc, created_at desc limit 1",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    current = dict(existing)
                    is_expected_resume = (
                        current["status"] == "interrupted"
                        and parent_run_id == current["run_id"]
                        and attempt == int(current["attempt"]) + 1
                    )
                    if not is_expected_resume:
                        conn.commit()
                        return self._decode_run(current)
            conn.execute(
                "insert or ignore into runs"
                " (run_id, channel_key, session_id, status, attempt, idempotency_key,"
                "  parent_run_id, request_json, gateway_session_key, workspace_boundary, sanitized_note, created_at, updated_at)"
                " values (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    channel_key,
                    session_id,
                    attempt,
                    idempotency_key,
                    parent_run_id,
                    request_json,
                    gateway_session_key,
                    workspace_boundary,
                    sanitized_note,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "select * from runs where idempotency_key = ? order by attempt desc, created_at desc limit 1"
                if idempotency_key
                else "select * from runs where run_id = ?",
                (idempotency_key or run_id,),
            ).fetchone()
            conn.commit()
        assert row is not None
        return self._decode_run(row)

    def record_run_status(
        self, run_id: str, status: str, *, sanitized_note: Optional[str] = None
    ) -> None:
        now = time.time()
        completed_at = now if status in _TERMINAL_STATUSES else None
        with self._connect() as conn:
            if sanitized_note is None:
                conn.execute(
                    "update runs set status = ?, updated_at = ?,"
                    " completed_at = coalesce(?, completed_at) where run_id = ?",
                    (status, now, completed_at, run_id),
                )
            else:
                conn.execute(
                    "update runs set status = ?, updated_at = ?, sanitized_note = ?,"
                    " completed_at = coalesce(?, completed_at) where run_id = ?",
                    (status, now, sanitized_note, completed_at, run_id),
                )

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("select * from runs where run_id = ?", (run_id,)).fetchone()
        return self._decode_run(row) if row else None

    def get_latest_resume_attempt(self, parent_run_id: str) -> Optional[Dict[str, Any]]:
        """Return the newest already-created attempt for an interrupted parent."""
        with self._connect() as conn:
            row = conn.execute(
                "select * from runs where parent_run_id = ?"
                " order by attempt desc, created_at desc limit 1",
                (parent_run_id,),
            ).fetchone()
        return self._decode_run(row) if row else None

    def find_run_by_idempotency_key(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """Latest attempt for a client idempotency key (attempt-descending)."""
        with self._connect() as conn:
            row = conn.execute(
                "select * from runs where idempotency_key = ?"
                " order by attempt desc, created_at desc limit 1",
                (idempotency_key,),
            ).fetchone()
            return self._decode_run(row) if row else None

    def recover_interrupted_runs(self) -> List[Dict[str, Any]]:
        """Mark every in-flight run ``interrupted`` — exactly once.

        The ``recovered_at is null`` guard makes a second sweep (same or a
        different process) a no-op, so one restart produces one recovery per
        channel, never two.
        """
        now = time.time()
        placeholders = ",".join("?" for _ in _IN_FLIGHT_STATUSES)
        with self._connect() as conn:
            conn.execute("begin immediate")
            rows = conn.execute(
                f"select * from runs where status in ({placeholders}) and recovered_at is null",
                _IN_FLIGHT_STATUSES,
            ).fetchall()
            recovered = [dict(r) for r in rows]
            if recovered:
                conn.executemany(
                    "update runs set status = 'interrupted', recovered_at = ?, updated_at = ?"
                    " where run_id = ? and recovered_at is null",
                    [(now, now, r["run_id"]) for r in recovered],
                )
            conn.commit()
        for r in recovered:
            r["status"] = "interrupted"
            r["recovered_at"] = now
        return recovered

    # ------------------------------------------------------------------
    # Outbox
    # ------------------------------------------------------------------

    def enqueue_outbox(
        self,
        *,
        channel_key: str,
        kind: str,
        idempotency_key: str,
        run_id: Optional[str] = None,
        payload_sha256: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        """Enqueue an outbound delivery; duplicate keys return the existing
        entry so retried producers never double-deliver."""
        outbox_id = f"out_{uuid.uuid4().hex}"
        with self._connect() as conn:
            cur = conn.execute(
                "insert or ignore into outbox"
                " (outbox_id, channel_key, run_id, kind, idempotency_key, payload_sha256,"
                "  status, created_at)"
                " values (?, ?, ?, ?, ?, ?, 'pending', ?)",
                (outbox_id, channel_key, run_id, kind, idempotency_key, payload_sha256, time.time()),
            )
            created = cur.rowcount == 1
            row = conn.execute(
                "select * from outbox where channel_key = ? and idempotency_key = ?",
                (channel_key, idempotency_key),
            ).fetchone()
        return dict(row), created

    def list_pending_outbox(self, channel_key: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if channel_key is None:
                rows = conn.execute(
                    "select * from outbox where status = 'pending' order by created_at"
                ).fetchall()
            else:
                rows = conn.execute(
                    "select * from outbox where status = 'pending' and channel_key = ?"
                    " order by created_at",
                    (channel_key,),
                ).fetchall()
            return [dict(r) for r in rows]

    def mark_outbox_delivered(self, outbox_id: str) -> bool:
        """Transition pending → delivered. Returns False when the entry was
        already delivered (or never existed) — the caller must not re-deliver."""
        with self._connect() as conn:
            cur = conn.execute(
                "update outbox set status = 'delivered', delivered_at = ?,"
                " attempts = attempts + 1"
                " where outbox_id = ? and status = 'pending'",
                (time.time(), outbox_id),
            )
            return cur.rowcount == 1

    # ------------------------------------------------------------------
    # Approvals (digest-bound, execute once)
    # ------------------------------------------------------------------

    def record_approval(
        self,
        approval_id: str,
        *,
        action_kind: str,
        payload_sha256: str,
        run_id: Optional[str] = None,
        channel_key: str = "",
        sanitized_summary: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "insert or ignore into approvals"
                " (approval_id, run_id, channel_key, action_kind, payload_sha256,"
                "  sanitized_summary, status, created_at)"
                " values (?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    approval_id,
                    run_id,
                    channel_key,
                    action_kind,
                    payload_sha256,
                    sanitized_summary,
                    time.time(),
                ),
            )

    def record_approval_decision(
        self, approval_id: str, decision: str, *, decided_by: str
    ) -> None:
        if decision not in ("approved", "rejected"):
            raise ApprovalStateError(f"unknown approval decision: {decision}")
        with self._connect() as conn:
            cur = conn.execute(
                "update approvals set status = ?, decided_by = ?, decided_at = ?"
                " where approval_id = ? and status = 'pending'",
                (decision, decided_by, time.time(), approval_id),
            )
            if cur.rowcount != 1:
                raise ApprovalStateError(
                    f"approval {approval_id} is not pending (already decided or unknown)"
                )

    def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "select * from approvals where approval_id = ?", (approval_id,)
            ).fetchone()
            return dict(row) if row else None

    def mark_approval_executed(self, approval_id: str, *, payload_sha256: str) -> Dict[str, Any]:
        """Consume the approval's single execution.

        One atomic statement encodes every precondition: the approval must be
        ``approved``, unexecuted, and the caller must present the exact digest
        that was approved. Anything else raises a specific error and consumes
        nothing.
        """
        with self._connect() as conn:
            cur = conn.execute(
                "update approvals set status = 'executed', executed_at = ?, execution_count = 1"
                " where approval_id = ? and status = 'approved' and execution_count = 0"
                "   and payload_sha256 = ?",
                (time.time(), approval_id, payload_sha256),
            )
            if cur.rowcount == 1:
                row = conn.execute(
                    "select * from approvals where approval_id = ?", (approval_id,)
                ).fetchone()
                return dict(row)
            # Diagnose why the transition was refused — without weakening it.
            row = conn.execute(
                "select * from approvals where approval_id = ?", (approval_id,)
            ).fetchone()
        if row is None:
            raise ApprovalStateError(f"approval not found: {approval_id}")
        record = dict(row)
        if record["execution_count"] >= 1 or record["status"] == "executed":
            raise AlreadyExecutedError(
                f"approval {approval_id} has already consumed its single execution"
            )
        if record["payload_sha256"] != payload_sha256 and record["status"] == "approved":
            raise DigestMismatchError(
                f"approval {approval_id} was approved for a different payload digest"
            )
        raise ApprovalStateError(
            f"approval {approval_id} is {record['status']}; only approved approvals execute"
        )
