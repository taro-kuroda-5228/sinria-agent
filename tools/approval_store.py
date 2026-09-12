"""Shared approval authority and sanitized UI projections.

SQLite transactions serialize pending -> resolved -> consumed transitions across
processes, including active/deferred ownership. Consumed IDs remain tombstones
so stale memory or a delayed projection update cannot recreate a grant. The DB
stores IDs, immutable session/digest/operation/expiry bindings, ownership, state,
and choices; JSON files retain the existing
sanitized UI projection. Legacy JSON-only entries fail closed (request a fresh
approval); they are never imported as new authorization.

Invariants:
  * Store failures never grant execution; public operations fail closed.
  * No raw metadata or full commands are added to the state journal.
  * Remote answers remain once/deny. Session/permanent decisions are available
    only to the active owning surface.
  * Every process participating in approvals must use this protocol; it cannot
    revoke grants held by an already-running older implementation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_constants import get_sinria_home

logger = logging.getLogger(__name__)

PREVIEW_LIMIT = 2000
ALLOWED_RESPONSE_CHOICES = frozenset({"once", "deny"})

_ID_SAFE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _safe_id(approval_id: str) -> str:
    if not isinstance(approval_id, str) or not _ID_SAFE.fullmatch(approval_id):
        raise ValueError("Malformed approval ID")
    return approval_id


def _pending_dir() -> Path:
    return Path(get_sinria_home()) / "approvals" / "pending"


def _responses_dir() -> Path:
    return Path(get_sinria_home()) / "approvals" / "responses"


@contextmanager
def _transaction():
    """Serialize all state transitions across threads and processes.

    The journal contains bindings, ownership, and decisions. Consumed IDs are
    tombstones: a stale memory projection must never recreate an authorization.
    JSON files remain UI projections, not the authority for granting execution.
    """
    root = Path(get_sinria_home()) / "approvals"
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "state.sqlite3", timeout=10)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS approvals ("
                   "id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                   "state TEXT NOT NULL, choice TEXT)")
        columns = {row[1] for row in db.execute("PRAGMA table_info(approvals)")}
        for name, kind in (("session_key", "TEXT"), ("digest", "TEXT"),
                           ("operation", "TEXT"), ("expires_at", "REAL")):
            if name not in columns:
                db.execute(f"ALTER TABLE approvals ADD COLUMN {name} {kind}")
        # Legacy rows lack trustworthy bindings. Expire after acquiring the lock.
        db.execute("UPDATE approvals SET state='consumed' WHERE state!='consumed' "
                   "AND (expires_at IS NULL OR expires_at<=? OR session_key IS NULL OR digest IS NULL)",
                   (time.time(),))
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def is_live(approval_id: str) -> bool:
    """Missing/unreadable shared state fails closed, regardless of memory."""
    try:
        with _transaction() as db:
            row = db.execute("SELECT state FROM approvals WHERE id=?",
                             (_safe_id(approval_id),)).fetchone()
            return bool(row and row[0] != "consumed")
    except Exception:
        return False


def mark_deferred(approval_id: str) -> bool:
    """Transfer ownership only after the caller retires its active queue entry."""
    try:
        with _transaction() as db:
            return bool(db.execute(
                "UPDATE approvals SET owner='deferred' WHERE id=? AND state!='consumed'",
                (_safe_id(approval_id),),
            ).rowcount)
    except Exception:
        return False


def resolve_active(approval_id: str, choice: str) -> bool:
    """Atomically decide and consume an active owner's unanswered request."""
    if choice not in {"once", "deny", "session", "always"}:
        return False
    try:
        with _transaction() as db:
            return bool(db.execute(
                "UPDATE approvals SET state='consumed', choice=? "
                "WHERE id=? AND owner='active' AND state='pending'",
                (choice, _safe_id(approval_id)),
            ).rowcount)
    except Exception:
        return False


def record_pending(approval_id: str, session_key: str, data: dict, *, owner: str = "active",
                   ttl_seconds: float = 7200) -> str | None:
    """Publish a request and return its authoritative ID (None on failure).

    Script requests occupy one session/operation slot. Under the same write
    lock, an identical live script reuses its ID without extending its expiry;
    a different script retires the old slot before publishing the replacement.
    Other active operations retain independent queue entries. Re-publication
    may annotate a pending projection but never changes its stored binding.
    """
    try:
        command = str(data.get("command", ""))
        raw_metadata = data.get("metadata")
        metadata: dict = raw_metadata if isinstance(raw_metadata, dict) else {}
        collaboration_bound = bool(metadata.get("work_item_id"))
        preview = "" if collaboration_bound else command[:PREVIEW_LIMIT]
        record = {
            "id": _safe_id(approval_id),
            "session_key": str(session_key),
            "command_preview": preview,
            "command_sha256": hashlib.sha256(command.encode("utf-8", "replace")).hexdigest(),
            "truncated": False if collaboration_bound else len(command) > PREVIEW_LIMIT,
            "description": str(data.get("description", "")),
            "pattern_keys": [str(k) for k in (data.get("pattern_keys") or [])],
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        if collaboration_bound:
            record["collaboration_binding"] = {
                key: metadata[key]
                for key in (
                    "work_item_id",
                    "work_item_version",
                    "requester_actor_id",
                    "required_capability",
                    "payload_sha256",
                    "require_distinct_approver",
                    "allowed_role_ids",
                )
                if key in metadata
            }
        operation = "execute_code" if owner == "deferred" or metadata.get("tool") == "execute_code" else None
        with _transaction() as db:
            row = db.execute("SELECT state, session_key, digest FROM approvals WHERE id=?", (record['id'],)).fetchone()
            if row:
                if row != ("pending", record["session_key"], record["command_sha256"]):
                    return None
            else:
                if operation:
                    existing = db.execute(
                        "SELECT id, digest FROM approvals WHERE session_key=? AND operation=? AND state!='consumed'",
                        (record["session_key"], operation),
                    ).fetchone()
                    if existing and existing[1] == record["command_sha256"]:
                        return existing[0]
                    db.execute("UPDATE approvals SET state='consumed' WHERE session_key=? AND operation=?",
                               (record["session_key"], operation))
                db.execute("INSERT INTO approvals (id, owner, state, session_key, digest, operation, expires_at) "
                           "VALUES (?, ?, 'pending', ?, ?, ?, ?)",
                           (record['id'], owner, record["session_key"], record["command_sha256"],
                            operation, time.time() + ttl_seconds))
            directory = _pending_dir()
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{record['id']}.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        return record["id"]
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("approval_store.record_pending failed: %s", exc)


def clear_pending(approval_id: str) -> None:
    """Remove the pending projection (and any unread response). Idempotent."""
    try:
        with _transaction() as db:
            safe = _safe_id(approval_id)
            db.execute("INSERT INTO approvals (id, owner, state, choice) VALUES (?, 'active', 'consumed', NULL) "
                       "ON CONFLICT(id) DO UPDATE SET state='consumed'", (safe,))
        for directory in (_pending_dir(), _responses_dir()):
            (directory / f"{safe}.json").unlink(missing_ok=True)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("approval_store.clear_pending failed: %s", exc)


def clear_session(session_key: str) -> None:
    """Retire shared session authority, including other processes' deferred IDs."""
    with _transaction() as db:
        db.execute("UPDATE approvals SET state='consumed' WHERE session_key=?", (session_key,))


def list_pending(max_age_seconds: int = 7200) -> list[dict]:
    """Return pending approvals sorted by requested_at (oldest first).

    Entries older than *max_age_seconds* are treated as crashed waiters:
    deleted and skipped. Corrupt files are skipped. Never raises.
    """
    try:
        directory = _pending_dir()
        if not directory.is_dir():
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        records: list[dict] = []
        for path in directory.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                requested = datetime.fromisoformat(record["requested_at"])
            except Exception:
                continue
            if requested < cutoff:
                clear_pending(record.get("id", path.stem))
                continue
            with _transaction() as db:
                row = db.execute("SELECT session_key, digest FROM approvals WHERE id=? AND state!='consumed'",
                                 (_safe_id(path.stem),)).fetchone()
                if row and record.get("id") == path.stem and row == (record.get("session_key"), record.get("command_sha256")):
                    records.append(record)
        records.sort(key=lambda r: r.get("requested_at", ""))
        return records
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("approval_store.list_pending failed: %s", exc)
        return []


def write_response(approval_id: str, choice: str) -> bool:
    """Record a remote answer for a pending approval.

    Only succeeds when *choice* is allowed and the approval is actually
    pending (prevents blind writes for unknown ids).
    """
    if choice not in ALLOWED_RESPONSE_CHOICES:
        return False
    try:
        safe = _safe_id(approval_id)
        with _transaction() as db:
            if not (_pending_dir() / f"{safe}.json").is_file():
                return False
            changed = db.execute(
                "UPDATE approvals SET state='resolved', choice=? WHERE id=? AND state='pending'",
                (choice, safe),
            ).rowcount
            if not changed:
                return False
            directory = _responses_dir()
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{safe}.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"choice": choice}), encoding="utf-8")
            tmp.replace(path)
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("approval_store.write_response failed: %s", exc)
        return False


def poll_response(approval_id: str, *, owner: str = "active",
                  session_key: str | None = None, digest: str | None = None) -> str | None:
    """Claim a resolved response for the specified ownership class exactly once.

    State is committed before returning the grant. A crash may lose an approval,
    but cannot replay it. Deferred retries cannot claim active waiter responses.
    """
    try:
        safe = _safe_id(approval_id)
        with _transaction() as db:
            row = db.execute(
                "SELECT choice, session_key, digest FROM approvals WHERE id=? AND owner=? AND state='resolved'",
                (safe, owner),
            ).fetchone()
            if not row or row[0] not in ALLOWED_RESPONSE_CHOICES:
                return None
            if session_key is not None and row[1] != session_key:
                return None
            if digest is not None and row[2] != digest:
                return None
            db.execute("UPDATE approvals SET state='consumed' WHERE id=?", (safe,))
        # Cleanup is best-effort AFTER the durable claim. Never roll back a
        # consumption because a UI projection could not be removed.
        clear_pending(approval_id)
        return row[0]
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("approval_store.poll_response failed: %s", exc)
        return None
