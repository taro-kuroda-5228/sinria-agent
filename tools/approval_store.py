"""File-backed sanitized approval queue store.

Gateway/CLI approval state lives in per-process memory
(``tools.approval._gateway_queues``), so surfaces in other processes — the
FastAPI dashboard and Sinria Desktop — cannot see or answer pending
approvals. This module projects each blocking gateway approval into
``{SINRIA_HOME}/approvals/pending/<id>.json`` (sanitized: bounded preview +
SHA-256 digest, mirroring tools/audit_log.py) and accepts answers through
``responses/<id>.json`` which the blocked waiter polls.

Invariants:
  * Write paths never raise — the approval flow is safety-critical, this
    store is observability/remote-answer plumbing.
  * No raw metadata, no full command text, no secrets are persisted.
  * Remote answers are restricted to {"once", "deny"} — session/permanent
    escalation is only possible on the owning surface.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_constants import get_sinria_home

logger = logging.getLogger(__name__)

PREVIEW_LIMIT = 2000
ALLOWED_RESPONSE_CHOICES = frozenset({"once", "deny"})

_ID_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _safe_id(approval_id: str) -> str:
    return _ID_SAFE.sub("_", str(approval_id))[:64] or "unknown"


def _pending_dir() -> Path:
    return Path(get_sinria_home()) / "approvals" / "pending"


def _responses_dir() -> Path:
    return Path(get_sinria_home()) / "approvals" / "responses"


def record_pending(approval_id: str, session_key: str, data: dict) -> None:
    """Persist a sanitized projection of a blocking gateway approval."""
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
        directory = _pending_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{record['id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("approval_store.record_pending failed: %s", exc)


def clear_pending(approval_id: str) -> None:
    """Remove the pending projection (and any unread response). Idempotent."""
    for directory in (_pending_dir(), _responses_dir()):
        try:
            (directory / f"{_safe_id(approval_id)}.json").unlink(missing_ok=True)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("approval_store.clear_pending failed: %s", exc)


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
                path.unlink(missing_ok=True)
                # Also remove the corresponding response file so it does not
                # accumulate as an orphan when the waiter thread died without
                # calling clear_pending (e.g. process crash, timeout).
                stale_id = record.get("id", path.stem)
                (_responses_dir() / f"{_safe_id(stale_id)}.json").unlink(missing_ok=True)
                continue
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
        if not (_pending_dir() / f"{safe}.json").is_file():
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


def poll_response(approval_id: str) -> str | None:
    """Read-and-delete the remote answer for *approval_id*, if any."""
    try:
        path = _responses_dir() / f"{_safe_id(approval_id)}.json"
        if not path.is_file():
            return None
        try:
            choice = json.loads(path.read_text(encoding="utf-8")).get("choice")
        finally:
            path.unlink(missing_ok=True)
        return choice if choice in ALLOWED_RESPONSE_CHOICES else None
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("approval_store.poll_response failed: %s", exc)
        return None
