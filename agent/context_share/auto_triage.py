"""Auto-triage for the Context Share review queue.

Implements the review-backlog convergence design
(`docs/plans/2026-07-03-context-share-backlog-auto-triage.md`):

- compaction: pending candidates of the same correction class collapse onto one
  representative row; merged rows are kept with a ``merged_into`` pointer so the
  audit trail is non-destructive.
- fail-closed auto-approval policy: only sanctioned low-risk classes that have
  recurred enough times may be promoted without per-candidate human review
  (plan 2026-06-06 §4). Anything org-scoped, sensitive, or touching
  production/auth/clinical-style keywords stays human-review gated.

Only sanitized category metadata is read or written; raw conversation content
never enters this module.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .extraction import EvidenceCandidate
from .review_queue import dedup_key, load_review_candidates, review_queue_path, write_review_candidates
from .storage import append_evidence_jsonl

AUTO_TRIAGE_REVIEWER = "auto_triage_v1"
DEFAULT_MIN_OCCURRENCES = 3

_ALLOWED_SENSITIVITIES = {"public", "internal"}
_ALLOWED_SOURCE_KINDS = {"workflow_outcome", "user_correction", "repeated_failure"}
_ALLOWED_APPLIES_TO = {"self_improvement", "practical_completion", "context_share", "outcome_loop"}
_DENY_MARKERS = (
    "production", "deploy", "credential", "secret", "token", "billing", "auth",
    "delete", "migration", "clinical", "patient", "phi", "pii", "dns", "domain",
    "external send", "本番", "デプロイ", "認証", "課金", "削除", "患者", "外部送信",
)
# Freeform captured user text (sanitized_correction_capture) may only enter the
# durable evidence store through explicit human review, regardless of scope,
# sensitivity, or recurrence.
_DENY_EXTRACTION_REASONS = ("sanitized_correction_capture",)


def classify_auto_approval(
    candidate: EvidenceCandidate,
    *,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
    allow_correction_auto_promote: bool = False,
) -> tuple[bool, str]:
    """Return (eligible, reason). Fail-closed: anything unexpected → human review.

    ``allow_correction_auto_promote`` is the install-type opt-in (default False =
    org/multi-tenant safe): when True, freeform captured corrections may
    auto-promote *after passing every other gate* (recurrence, sanitized
    sensitivity/scope/source_kind/applies_to, and the deny-marker screen). The
    excerpt was already sanitization-checked at capture time; recurrence plus the
    deny-marker screen keep production/auth/clinical corrections human-gated.
    """
    evidence = candidate.evidence
    if not allow_correction_auto_promote and any(
        candidate.extraction_reason.startswith(reason) for reason in _DENY_EXTRACTION_REASONS
    ):
        return False, "freeform captured correction requires human review"
    if evidence.sensitivity not in _ALLOWED_SENSITIVITIES:
        return False, f"sensitivity {evidence.sensitivity} requires human review"
    if evidence.scope == "org":
        return False, "org-scoped policy requires human review"
    if evidence.source_kind not in _ALLOWED_SOURCE_KINDS:
        return False, f"source_kind {evidence.source_kind} requires human review"
    if not _ALLOWED_APPLIES_TO.intersection(evidence.applies_to):
        return False, "applies_to outside sanctioned self-improvement scopes"
    haystack = f"{evidence.summary} {evidence.sanitized_sample}".lower()
    for marker in _DENY_MARKERS:
        if marker in haystack:
            return False, f"deny marker {marker!r} requires human review"
    if candidate.occurrence_count < min_occurrences:
        return False, f"occurrence_count {candidate.occurrence_count} below threshold {min_occurrences}"
    return True, f"low-risk class recurred {candidate.occurrence_count}x"


def _parse_ts(value: str | None) -> datetime:
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def compact_candidates(candidates: list[EvidenceCandidate]) -> tuple[list[EvidenceCandidate], int]:
    """Collapse same-class pending rows onto one representative per class.

    Returns the rewritten row list (original order preserved) and the number of
    rows that were merged. Approved and already-merged rows are never demoted.
    """
    by_class: dict[tuple, list[EvidenceCandidate]] = {}
    for row in candidates:
        if row.approval_state in {"proposed", "approved"}:
            by_class.setdefault(dedup_key(row), []).append(row)

    replacement: dict[str, EvidenceCandidate] = {}
    merged_count = 0
    for members in by_class.values():
        proposed = [row for row in members if row.approval_state == "proposed"]
        if not proposed:
            continue
        approved = [row for row in members if row.approval_state == "approved"]
        if approved:
            representative = min(approved, key=lambda row: (_parse_ts(row.evidence.valid_from), row.candidate_id))
            to_merge = proposed
        else:
            representative = min(proposed, key=lambda row: (_parse_ts(row.evidence.valid_from), row.candidate_id))
            to_merge = [row for row in proposed if row.candidate_id != representative.candidate_id]
        if not to_merge:
            continue
        last_seen = max(
            (row.last_seen_at or row.evidence.valid_from for row in members),
            key=_parse_ts,
        )
        replacement[representative.candidate_id] = replace(
            representative,
            occurrence_count=sum(row.occurrence_count for row in [representative, *to_merge]),
            last_seen_at=last_seen,
        )
        for row in to_merge:
            replacement[row.candidate_id] = replace(
                row,
                approval_state="merged",
                merged_into=representative.candidate_id,
            )
            merged_count += 1

    rewritten = [replacement.get(row.candidate_id, row) for row in candidates]
    return rewritten, merged_count


def run_auto_triage(
    *,
    queue_path: Path | None = None,
    evidence_path: Path | None = None,
    apply: bool = False,
    approve_low_risk: bool = False,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
    allow_correction_auto_promote: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compact the review queue and optionally auto-approve low-risk classes.

    Dry-run by default: without ``apply`` nothing is written. Auto-approval
    additionally requires ``approve_low_risk`` and only promotes candidates that
    pass the fail-closed :func:`classify_auto_approval` policy.
    """
    target = queue_path or review_queue_path()
    rows = load_review_candidates(path=target)
    pending_before = sum(1 for row in rows if row.approval_state == "proposed")

    compacted, merged_count = compact_candidates(rows)

    eligible: list[dict[str, Any]] = []
    human_required: list[dict[str, Any]] = []
    for row in compacted:
        if row.approval_state != "proposed":
            continue
        allowed, reason = classify_auto_approval(
            row,
            min_occurrences=min_occurrences,
            allow_correction_auto_promote=allow_correction_auto_promote,
        )
        entry = {"candidate_id": row.candidate_id, "occurrence_count": row.occurrence_count, "reason": reason}
        (eligible if allowed else human_required).append(entry)

    auto_approved: list[dict[str, Any]] = []
    if apply and approve_low_risk and eligible:
        approved_ids = {entry["candidate_id"] for entry in eligible}
        approved_at = (now or datetime.now(timezone.utc)).isoformat()
        promoted: list[EvidenceCandidate] = []
        for index, row in enumerate(compacted):
            if row.candidate_id not in approved_ids:
                continue
            approved_row = replace(
                row,
                evidence=replace(row.evidence, human_approved=True),
                approval_state="approved",
                approved_at=approved_at,
                approved_by=AUTO_TRIAGE_REVIEWER,
            )
            compacted[index] = approved_row
            promoted.append(approved_row)
            auto_approved.append({
                "candidate_id": approved_row.candidate_id,
                "evidence_id": approved_row.evidence.evidence_id,
                "occurrence_count": approved_row.occurrence_count,
            })
        if promoted:
            append_evidence_jsonl([row.evidence for row in promoted], path=evidence_path)

    if apply:
        write_review_candidates(compacted, path=target)

    pending_after = sum(1 for row in compacted if row.approval_state == "proposed")
    distinct_pending_classes = len({dedup_key(row) for row in compacted if row.approval_state == "proposed"})

    return {
        "dry_run": not apply,
        "total_candidates": len(rows),
        "pending_before": pending_before,
        "pending_after": pending_after,
        "merged_count": merged_count,
        "distinct_pending_classes": distinct_pending_classes,
        "auto_approve_eligible": eligible,
        "human_review_required": human_required,
        "auto_approved": auto_approved,
        "external_action_performed": False,
        "raw_private_context_exported": False,
    }
