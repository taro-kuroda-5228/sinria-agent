"""Policy enforcement for the autonomy kernel."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Dict, Optional

from .models import ActionRequest, CapabilityGrant, Decision


_SECRETS_PATTERNS = [
    r"\b\d{3}-\d{2}-\d{4}\b",
    r"\b\d{16}\b",
    r"\b\d{9}\b",
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
]

_PHI_KEYWORDS = {
    "phi",
    "ssn",
    "mrn",
    "病歴",
    "患者",
    "medical",
    "diagnosis",
    "dob",
    "birth",
    "diagnostic",
}


def _normalize_constraints(request: ActionRequest) -> Dict:
    return request.constraints or {}


def _parse_datetime(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _seems_phi(body: str) -> bool:
    lowered = (body or "").lower()
    if any(keyword in lowered for keyword in _PHI_KEYWORDS):
        return True
    for pattern in _SECRETS_PATTERNS:
        if re.search(pattern, body or ""):
            return True
    return False


def _scope_allows(grant_scope: str, request_scope: str) -> bool:
    if grant_scope == "*":
        return True
    if grant_scope == request_scope:
        return True
    if request_scope.startswith(f"{grant_scope}."):
        return True
    return False


def _limit_remaining(value: int, used: int) -> int:
    if value < 0:
        return -1
    return value - used


def _grant_limit_and_used(
    grant: CapabilityGrant, action: str, scope: str, usage: Dict[str, Dict[str, int]]
) -> int:
    key_candidates = [f"{scope}:{action}", scope, action, "global", "*"]
    raw_limit = None
    for key in key_candidates:
        if key in grant.limits:
            raw_limit = grant.limits[key]
            break
    if raw_limit is None:
        return 2**31
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return 0
    used = usage.get(grant.grant_id, {}).get(str(action), 0) + usage.get(grant.grant_id, {}).get(scope, 0)
    return _limit_remaining(limit, used)


def _find_active_grant(
    request: ActionRequest,
    now: datetime,
    grant_usage: Optional[Dict[str, Dict[str, int]]],
) -> Optional[CapabilityGrant]:
    usage = grant_usage or {}
    for grant in request.capability_grants:
        if grant.account != request.account:
            continue
        if not _scope_allows(grant.scope, request.scope):
            continue
        expiry = _parse_datetime(grant.expires_at)
        if expiry is not None and expiry <= now:
            continue
        used_by_grant = usage.get(grant.grant_id, {}) if isinstance(usage, dict) else {}
        if _grant_limit_and_used(grant, request.action, request.scope, {grant.grant_id: used_by_grant}) <= 0:
            continue
        return grant
    return None


def evaluate_request(
    request: ActionRequest,
    *,
    now: Optional[datetime] = None,
    kill_switch: bool = False,
    grant_usage: Optional[Dict[str, Dict[str, int]]] = None,
) -> Decision:
    """Return the policy decision for request.

    Rules:
      - kill switch blocks all actions
      - secret/PHI-like text in body is denied
      - valid grant required
      - grant is validated for expiry/scope/account/limits
      - constraints.require_human => ask
      - otherwise allow
    """

    current_time = _parse_datetime(now or datetime.now(timezone.utc))
    assert current_time is not None

    if kill_switch:
        return Decision("block", "kill_switch")

    if _seems_phi(request.body):
        return Decision("block", "phi_detected")

    grant = _find_active_grant(request, current_time, grant_usage)
    if grant is None:
        return Decision("block", "grant_missing_or_expired_or_out_of_scope_or_limit")

    constraints = _normalize_constraints(request)
    if constraints.get("require_human"):
        return Decision("ask", "require_human", grant_id=grant.grant_id)

    return Decision("allow", "granted", grant_id=grant.grant_id)
