"""Pure liveness decisions for the external Sinria Guardian.

This module never performs recovery actions. It accepts only sanitized metadata
and returns a requested action for a separately approval-gated actuator.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

_HASH = re.compile(r"^[0-9a-f]{64}$")
_TOP_KEYS = {"process_heartbeat_at", "event_loop_heartbeat_at", "sessions"}
_SESSION_KEYS = {"session_id_hash", "state", "last_activity_at", "current_phase"}
_ALLOWED_STATES = {"active", "idle", "completed"}
_ALLOWED_PHASES = {"queued", "model", "tool", "compression", "finalizing", "unknown"}


@dataclass(frozen=True)
class GuardianThresholds:
    process_stale_seconds: float
    event_loop_stale_seconds: float
    session_stale_seconds: float

    def __post_init__(self) -> None:
        if min(self.process_stale_seconds, self.event_loop_stale_seconds, self.session_stale_seconds) <= 0:
            raise ValueError("guardian thresholds must be positive")


@dataclass(frozen=True)
class GuardianDecision:
    action: str
    reason: str
    session_id_hash: str | None = None


def _parse_timestamp(value: Any, now: datetime) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    parsed = parsed.astimezone(timezone.utc)
    if parsed > now:
        return None
    return parsed


def evaluate_guardian_snapshot(
    snapshot: dict[str, Any], *, now: datetime, thresholds: GuardianThresholds
) -> GuardianDecision:
    """Classify a sanitized runtime snapshot without causing side effects."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    if not isinstance(snapshot, dict) or set(snapshot) != _TOP_KEYS:
        return GuardianDecision("observe", "invalid_snapshot_schema")
    process_at = _parse_timestamp(snapshot.get("process_heartbeat_at"), now)
    loop_at = _parse_timestamp(snapshot.get("event_loop_heartbeat_at"), now)
    sessions = snapshot.get("sessions")
    if process_at is None or loop_at is None or not isinstance(sessions, list):
        return GuardianDecision("observe", "invalid_snapshot_values")

    parsed_sessions: list[tuple[datetime, str]] = []
    for item in sessions:
        if not isinstance(item, dict) or set(item) != _SESSION_KEYS:
            return GuardianDecision("observe", "unsanitized_session_schema")
        sid = item.get("session_id_hash")
        state = item.get("state")
        phase = item.get("current_phase")
        activity = _parse_timestamp(item.get("last_activity_at"), now)
        if (
            not isinstance(sid, str) or not _HASH.fullmatch(sid)
            or state not in _ALLOWED_STATES or phase not in _ALLOWED_PHASES
            or activity is None
        ):
            return GuardianDecision("observe", "invalid_session_values")
        if state == "active":
            parsed_sessions.append((activity, sid))

    if (now - process_at).total_seconds() > thresholds.process_stale_seconds:
        return GuardianDecision("request_gateway_restart", "process_heartbeat_stale")
    if (now - loop_at).total_seconds() > thresholds.event_loop_stale_seconds:
        return GuardianDecision("request_gateway_restart", "event_loop_heartbeat_stale")
    stale = [pair for pair in parsed_sessions if (now - pair[0]).total_seconds() > thresholds.session_stale_seconds]
    if stale:
        activity, sid = min(stale)
        return GuardianDecision("request_session_interrupt", "session_activity_stale", sid)
    return GuardianDecision("healthy", "all_heartbeats_fresh")
