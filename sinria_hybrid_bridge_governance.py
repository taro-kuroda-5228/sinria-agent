"""Review gate and self-improvement primitives for Sinria Hybrid Agent Bridge."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


_ROLE_RANK = {
    "user": 1,
    "admin": 2,
    "compliance": 3,
    "physician": 4,
}


class ImprovementKind(str, Enum):
    SKILL_UPDATE = "skill_update"
    POLICY_CHANGE = "policy_change"
    TEMPLATE_UPDATE = "template_update"
    EVAL_OR_TEST = "eval_or_test"
    CONNECTOR_BUG = "connector_bug"


@dataclass(frozen=True)
class ReviewRequest:
    review_id: str
    task_id: str
    required_role: str
    action_summary: str
    reason: str = ""
    status: str = "pending"


@dataclass(frozen=True)
class ReviewDecision:
    approved: bool
    decided_by: str
    role: str
    comment: str = ""


@dataclass(frozen=True)
class ReviewOutcome:
    execution_allowed: bool
    next_status: str
    reason: str


@dataclass(frozen=True)
class ImprovementCandidate:
    tenant_id: str
    source_run_id: str
    kind: ImprovementKind
    summary: str
    requires_human_approval: bool = True
    external_action_performed: bool = False
    status: str = "proposed"


_PATIENT_ID_RE = re.compile(r"\bMRN-?\d+\b", re.IGNORECASE)
_CARD_NUMBER_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+81[- ]?)?0?\d{1,3}[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_POSTAL_CODE_RE = re.compile(r"\b\d{3}-\d{4}\b")
_JAPANESE_DEMO_NAME_RE = re.compile(r"山田[一-龥ぁ-んァ-ン]{1,4}")


def _redact_improvement_summary(summary: str) -> str:
    """Return a cloud-visible improvement summary with identifiers removed."""

    text = _PATIENT_ID_RE.sub("[REDACTED_ID]", summary)
    text = _CARD_NUMBER_RE.sub("[REDACTED_CARD]", text)
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    text = _POSTAL_CODE_RE.sub("[REDACTED_POSTAL]", text)
    return _JAPANESE_DEMO_NAME_RE.sub("[REDACTED_NAME]", text)


def _role_satisfies(actual: str, required: str) -> bool:
    if required == "physician":
        return actual == "physician"
    return _ROLE_RANK.get(actual, 0) >= _ROLE_RANK.get(required, 999)


def apply_review_decision(request: ReviewRequest, decision: ReviewDecision) -> ReviewOutcome:
    """Apply a human review decision to a planned side-effect action."""

    if not decision.approved:
        return ReviewOutcome(
            execution_allowed=False,
            next_status="rejected",
            reason=f"Review rejected by {decision.decided_by}: {decision.comment or 'no comment'}",
        )
    if not _role_satisfies(decision.role, request.required_role):
        return ReviewOutcome(
            execution_allowed=False,
            next_status="waiting_review",
            reason=f"Action requires {request.required_role} approval; got {decision.role} from {decision.decided_by}.",
        )
    return ReviewOutcome(
        execution_allowed=True,
        next_status="approved_for_execution",
        reason=f"Approved by {decision.decided_by} with role {decision.role}.",
    )


def propose_improvement_candidate(
    *,
    tenant_id: str,
    source_run_id: str,
    signal: str,
    summary: str,
) -> ImprovementCandidate:
    """Classify a correction/failure signal into a reviewable improvement candidate."""

    normalized = signal.strip().lower()
    text = f"{normalized} {summary}".lower()
    if "safe_block" in text or "policy" in text or "blocked" in text:
        kind = ImprovementKind.POLICY_CHANGE
    elif "template" in text or "copy" in text or "tone" in text:
        kind = ImprovementKind.TEMPLATE_UPDATE if "template" in text else ImprovementKind.SKILL_UPDATE
    elif "connector" in text or "adapter" in text or "api" in text:
        kind = ImprovementKind.CONNECTOR_BUG
    elif "test" in text or "eval" in text or "regression" in text:
        kind = ImprovementKind.EVAL_OR_TEST
    else:
        kind = ImprovementKind.SKILL_UPDATE
    return ImprovementCandidate(
        tenant_id=tenant_id,
        source_run_id=source_run_id,
        kind=kind,
        summary=_redact_improvement_summary(summary),
        requires_human_approval=True,
        external_action_performed=False,
    )
