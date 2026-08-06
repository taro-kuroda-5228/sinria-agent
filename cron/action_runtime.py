"""Durable, local state machine for Sinria cron actions.

This module owns only the ``cron_action_*`` tables in a profile's local SQLite
store. Payloads are retained locally for recovery; audit metadata contains only
bounded identifiers and a payload digest.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import sinria_constants

try:
    from tools.audit_log import record_audit_event
except Exception:  # pragma: no cover - audit must never block state changes
    record_audit_event = None


class CronActionState(str, Enum):
    PROPOSED = "proposed"
    AWAITING_DECISION = "awaiting_decision"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


_TERMINAL = {
    CronActionState.REJECTED,
    CronActionState.EXPIRED,
    CronActionState.COMPLETED,
    CronActionState.FAILED,
}
_ALLOWED = {
    CronActionState.PROPOSED: {CronActionState.AWAITING_DECISION, CronActionState.EXPIRED},
    CronActionState.AWAITING_DECISION: {
        CronActionState.APPROVED,
        CronActionState.REJECTED,
        CronActionState.EXPIRED,
        CronActionState.FAILED,
    },
    CronActionState.APPROVED: {CronActionState.EXECUTING, CronActionState.EXPIRED},
    CronActionState.EXECUTING: {
        CronActionState.VERIFYING,
        CronActionState.FAILED,
        CronActionState.NEEDS_REVIEW,
    },
    CronActionState.VERIFYING: {
        CronActionState.COMPLETED,
        CronActionState.FAILED,
        CronActionState.NEEDS_REVIEW,
    },
    CronActionState.NEEDS_REVIEW: {
        CronActionState.EXECUTING,
        CronActionState.FAILED,
    },
    CronActionState.REJECTED: set(),
    CronActionState.EXPIRED: set(),
    CronActionState.COMPLETED: set(),
    CronActionState.FAILED: set(),
}


_SAFE_METADATA_KEYS = frozenset({"profile", "lease_owner", "lease_expires_at", "payload_sha256"})


class InvalidTransition(RuntimeError):
    """The requested state change is not valid for the current state."""


class StaleActionVersion(RuntimeError):
    """A compare-and-swap transition observed a newer action version."""


@dataclass(frozen=True)
class CronAction:
    action_id: str
    profile: str
    payload: dict[str, Any]
    state: CronActionState
    version: int
    created_at: float
    updated_at: float
    expires_at: float | None = None
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: float | None = None
    last_actor_id: str | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cron_action_actions (
    action_id TEXT PRIMARY KEY, profile TEXT NOT NULL, payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL, state TEXT NOT NULL, version INTEGER NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL,
    lease_owner TEXT, lease_token TEXT, lease_expires_at REAL, last_actor_id TEXT
);
CREATE INDEX IF NOT EXISTS cron_action_actions_profile ON cron_action_actions(profile);
CREATE TABLE IF NOT EXISTS cron_action_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT, action_id TEXT NOT NULL,
    event_type TEXT NOT NULL, from_state TEXT, to_state TEXT, version INTEGER NOT NULL,
    actor_id TEXT, metadata_json TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS cron_action_events_action ON cron_action_events(action_id, event_id);
"""


class CronActionStore:
    """SQLite-backed CronAction store with atomic state transitions."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else sinria_constants.get_sinria_home() / "cron" / "actions.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.db_path.parent, 0o700)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._secure_files()

    def _secure_files(self) -> None:
        for path in (self.db_path, Path(str(self.db_path) + "-wal"), Path(str(self.db_path) + "-shm")):
            if path.exists():
                os.chmod(path, 0o600)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "CronActionStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @staticmethod
    def _now(now: float | None) -> float:
        return time.time() if now is None else float(now)

    @staticmethod
    def _digest(payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    @staticmethod
    def _action(row: sqlite3.Row) -> CronAction:
        return CronAction(
            action_id=row["action_id"], profile=row["profile"], payload=json.loads(row["payload_json"]),
            state=CronActionState(row["state"]), version=int(row["version"]),
            created_at=float(row["created_at"]), updated_at=float(row["updated_at"]),
            expires_at=row["expires_at"], lease_owner=row["lease_owner"], lease_token=row["lease_token"],
            lease_expires_at=row["lease_expires_at"], last_actor_id=row["last_actor_id"],
        )

    def get(self, action_id: str) -> CronAction:
        with self._lock:
            row = self._conn.execute("SELECT * FROM cron_action_actions WHERE action_id=?", (action_id,)).fetchone()
        if row is None:
            raise KeyError(action_id)
        return self._action(row)

    def list_actions(
        self,
        *,
        states: set[CronActionState] | None = None,
        profile: str | None = None,
    ) -> list[CronAction]:
        """Return durable actions in creation order with optional isolation filters."""
        clauses: list[str] = []
        params: tuple[Any, ...] = ()
        sql = "SELECT * FROM cron_action_actions"
        if states:
            values = tuple(sorted(state.value for state in states))
            clauses.append(f"state IN ({','.join('?' for _ in values)})")
            params = values
        if profile is not None:
            clauses.append("profile=?")
            params += (profile,)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at, action_id"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._action(row) for row in rows]

    def update_payload(
        self,
        action_id: str,
        updates: dict[str, Any],
        *,
        expected_version: int,
        now: float | None = None,
    ) -> CronAction:
        """Merge local recovery metadata under compare-and-swap version control."""
        timestamp = self._now(now)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT payload_json, version FROM cron_action_actions WHERE action_id=?",
                (action_id,),
            ).fetchone()
            if row is None:
                raise KeyError(action_id)
            if int(row["version"]) != expected_version:
                raise StaleActionVersion(action_id)
            payload = json.loads(row["payload_json"])
            payload.update(updates)
            updated = self._conn.execute(
                """UPDATE cron_action_actions
                   SET payload_json=?, version=version+1, updated_at=?
                   WHERE action_id=? AND version=?""",
                (json.dumps(payload, ensure_ascii=False, sort_keys=True), timestamp, action_id, expected_version),
            )
            if updated.rowcount != 1:
                raise StaleActionVersion(action_id)
        return self.get(action_id)

    def create(self, action_id: str | None = None, profile: str = "default", payload: dict[str, Any] | None = None,
               *, expires_at: float | None = None, actor_id: str | None = None, now: float | None = None) -> CronAction:
        action_id = action_id or uuid.uuid4().hex
        payload = dict(payload or {})
        timestamp = self._now(now)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO cron_action_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)",
                (action_id, profile, json.dumps(payload, sort_keys=True, separators=(",", ":")), self._digest(payload),
                 CronActionState.PROPOSED.value, 1, timestamp, timestamp, expires_at, actor_id),
            )
            self._event(action_id, "created", None, CronActionState.PROPOSED, 1, actor_id, timestamp, {"profile": profile})
        return self.get(action_id)

    def _event(self, action_id: str, event_type: str, old: CronActionState | None,
               new: CronActionState, version: int, actor_id: str | None, now: float, metadata: dict[str, Any]) -> None:
        safe = {}
        for key, value in metadata.items():
            if key in _SAFE_METADATA_KEYS:
                text = "".join(char for char in str(value) if ord(char) >= 32).replace("\n", " ")[:200]
                safe[key] = text
        digest = self._conn.execute(
            "SELECT payload_sha256 FROM cron_action_actions WHERE action_id=?", (action_id,)
        ).fetchone()
        if digest:
            safe["payload_sha256"] = digest[0]
        safe_actor = None if actor_id is None else "".join(char for char in str(actor_id) if ord(char) >= 32)[:200]
        self._conn.execute(
            "INSERT INTO cron_action_events(action_id,event_type,from_state,to_state,version,actor_id,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (action_id, event_type, old.value if old else None, new.value, version, safe_actor, json.dumps(safe, sort_keys=True), now),
        )
        if record_audit_event:
            try:
                record_audit_event("cron_action_" + event_type, action_id=action_id, state=new.value,
                                   version=version, actor_id=safe_actor, **safe)
            except Exception:
                pass

    def transition(self, action_id: str, new_state: CronActionState, *, expected_version: int,
                   actor_id: str | None = None, now: float | None = None) -> CronAction:
        timestamp = self._now(now)
        with self._lock, self._conn:
            row = self._conn.execute("SELECT * FROM cron_action_actions WHERE action_id=?", (action_id,)).fetchone()
            if row is None:
                raise KeyError(action_id)
            current = CronActionState(row["state"])
            if int(row["version"]) != expected_version:
                raise StaleActionVersion(action_id)
            if (row["expires_at"] is not None and float(row["expires_at"]) <= timestamp
                    and new_state is not CronActionState.EXPIRED
                    and current not in _TERMINAL):
                expired_version = expected_version + 1
                updated = self._conn.execute(
                    "UPDATE cron_action_actions SET state='expired',version=?,updated_at=?,last_actor_id=? WHERE action_id=? AND version=?",
                    (expired_version, timestamp, "expiry", action_id, expected_version),
                )
                if updated.rowcount != 1:
                    raise StaleActionVersion(action_id)
                self._event(action_id, "expired", current, CronActionState.EXPIRED,
                            expired_version, "expiry", timestamp, {})
                self._conn.commit()
                raise InvalidTransition("action has expired")
            if new_state not in _ALLOWED[current]:
                raise InvalidTransition(f"{current.value} -> {new_state.value}")
            updated = self._conn.execute(
                "UPDATE cron_action_actions SET state=?,version=version+1,updated_at=?,last_actor_id=?,"
                "lease_owner=CASE WHEN ?='needs_review' THEN NULL ELSE lease_owner END,"
                "lease_token=CASE WHEN ?='needs_review' THEN NULL ELSE lease_token END,"
                "lease_expires_at=CASE WHEN ?='needs_review' THEN NULL ELSE lease_expires_at END "
                "WHERE action_id=? AND version=?",
                (new_state.value, timestamp, actor_id, new_state.value, new_state.value, new_state.value,
                 action_id, expected_version),
            )
            if updated.rowcount != 1:
                raise StaleActionVersion(action_id)
            self._event(action_id, "transitioned", current, new_state, expected_version + 1, actor_id, timestamp, {})
        return self.get(action_id)

    def decide(
        self,
        action_id: str,
        decision: CronActionState,
        *,
        actor_id: str,
        expected_version: int | None = None,
        now: float | None = None,
    ) -> CronAction:
        if decision not in {CronActionState.APPROVED, CronActionState.REJECTED}:
            raise ValueError("decision must be approved or rejected")
        current = self.get(action_id)
        if current.state is not CronActionState.AWAITING_DECISION:
            raise InvalidTransition("decision already resolved")
        if expected_version is not None and current.version != expected_version:
            raise StaleActionVersion(action_id)
        version = current.version if expected_version is None else expected_version
        try:
            return self.transition(
                action_id,
                decision,
                expected_version=version,
                actor_id=actor_id,
                now=now,
            )
        except StaleActionVersion as exc:
            raise InvalidTransition("decision already resolved") from exc

    def acquire_execution_lease(self, action_id: str, owner: str, *, ttl: float = 300, now: float | None = None) -> CronAction:
        timestamp = self._now(now)
        with self._lock, self._conn:
            row = self._conn.execute("SELECT * FROM cron_action_actions WHERE action_id=?", (action_id,)).fetchone()
            if row is None:
                raise KeyError(action_id)
            state = CronActionState(row["state"])
            if state not in {CronActionState.APPROVED, CronActionState.NEEDS_REVIEW}:
                raise InvalidTransition("action is not approved for execution")
            if row["expires_at"] is not None and float(row["expires_at"]) <= timestamp:
                version = int(row["version"]) + 1
                updated = self._conn.execute(
                    "UPDATE cron_action_actions SET state='expired',version=?,updated_at=?,last_actor_id=? WHERE action_id=? AND version=?",
                    (version, timestamp, "expiry", action_id, int(row["version"])),
                )
                if updated.rowcount != 1:
                    raise StaleActionVersion(action_id)
                self._event(action_id, "expired", state, CronActionState.EXPIRED, version, "expiry", timestamp, {})
                self._conn.commit()
                raise InvalidTransition("action has expired")
            if row["lease_expires_at"] is not None and float(row["lease_expires_at"]) > timestamp:
                raise InvalidTransition("execution lease is held")
            token = uuid.uuid4().hex
            version = int(row["version"]) + 1
            updated = self._conn.execute(
                "UPDATE cron_action_actions SET state=?,version=?,updated_at=?,lease_owner=?,lease_token=?,lease_expires_at=?,last_actor_id=? WHERE action_id=? AND version=?",
                (CronActionState.EXECUTING.value, version, timestamp, owner, token, timestamp + ttl, owner, action_id, int(row["version"])),
            )
            if updated.rowcount != 1:
                raise StaleActionVersion(action_id)
            self._event(action_id, "lease_acquired", state, CronActionState.EXECUTING, version, owner, timestamp, {"lease_owner": owner, "lease_expires_at": timestamp + ttl})
        return self.get(action_id)

    def renew_execution_lease(self, action_id: str, owner: str, token: str, *, ttl: float = 300, now: float | None = None) -> CronAction:
        timestamp = self._now(now)
        with self._lock, self._conn:
            updated = self._conn.execute(
                "UPDATE cron_action_actions SET lease_expires_at=?,updated_at=? WHERE action_id=? AND state='executing' AND lease_owner=? AND lease_token=? AND lease_expires_at > ?",
                (timestamp + ttl, timestamp, action_id, owner, token, timestamp),
            )
            if updated.rowcount != 1:
                raise InvalidTransition("execution lease is not owned or has expired")
        return self.get(action_id)

    def release_execution_lease(self, action_id: str, owner: str, token: str, *, outcome: CronActionState = CronActionState.VERIFYING, now: float | None = None) -> CronAction:
        if outcome not in {CronActionState.VERIFYING, CronActionState.FAILED, CronActionState.NEEDS_REVIEW}:
            raise ValueError("lease outcome must be verifying, failed, or needs_review")
        timestamp = self._now(now)
        with self._lock, self._conn:
            row = self._conn.execute("SELECT * FROM cron_action_actions WHERE action_id=?", (action_id,)).fetchone()
            if row is None:
                raise KeyError(action_id)
            if (row["state"] != CronActionState.EXECUTING.value or row["lease_owner"] != owner
                    or row["lease_token"] != token or row["lease_expires_at"] is None
                    or float(row["lease_expires_at"]) <= timestamp):
                raise InvalidTransition("execution lease is not owned")
            version = int(row["version"]) + 1
            updated = self._conn.execute("UPDATE cron_action_actions SET state=?,version=?,updated_at=?,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,last_actor_id=? WHERE action_id=? AND version=? AND lease_expires_at > ?",
                               (outcome.value, version, timestamp, owner, action_id, int(row["version"]), timestamp))
            if updated.rowcount != 1:
                raise StaleActionVersion(action_id)
            self._event(action_id, "lease_released", CronActionState.EXECUTING, outcome, version, owner, timestamp, {"lease_owner": owner})
        return self.get(action_id)

    def expire(self, *, now: float | None = None) -> list[CronAction]:
        timestamp = self._now(now)
        with self._lock:
            rows = self._conn.execute("SELECT action_id,state,version,expires_at,lease_expires_at FROM cron_action_actions WHERE (expires_at IS NOT NULL AND expires_at <= ?) OR (state = 'executing' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)", (timestamp, timestamp)).fetchall()
        expired = []
        for row in rows:
            target = CronActionState.NEEDS_REVIEW if row["state"] == CronActionState.EXECUTING else CronActionState.EXPIRED
            try:
                expired.append(self.transition(row["action_id"], target, expected_version=int(row["version"]), actor_id="expiry", now=timestamp))
            except (InvalidTransition, StaleActionVersion):
                pass
        return expired
