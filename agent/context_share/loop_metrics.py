"""Effect measurement for the Sinria self-improvement loop (stage 7 KPI).

The loop is: Goal → Sinria action → Actual result → Gap extraction → Cause
classification → durable-fix candidates (memory/skill/test/runbook/code/config)
→ review-gated promotion → changed behavior on the next run.

This module closes the loop by measuring whether the *same gap* recurs after a
durable fix was approved. The KPI is "same-correction recurrence after fix →
zero convergence". Only sanitized category strings and source pointers are
read; raw conversation content is never stored or emitted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .outcome_gap import PracticalOutcomeRecord, load_outcome_records, outcome_gap_path
from .review_queue import dedup_key, load_review_candidates, review_queue_path
from .storage import evidence_store_path, load_evidence_jsonl
from .evidence import EvidenceLedger
from .intent_resolver import IntentResolver

DEFAULT_BACKLOG_ALERT_THRESHOLD = 50

# Gap causes that describe an in-progress or interrupted turn rather than a
# violation of an approved constraint. Counting them as recurrence makes zero
# convergence structurally impossible (any multi-turn task emits them), so the
# KPI alerts on genuine violation classes only and reports these separately.
_PROGRESS_NOISE_CAUSES = ("execution_incomplete", "interrupted_or_failed")


def is_progress_noise_gap(gap_summary: str) -> bool:
    return any(gap_summary.endswith(f":{cause}") for cause in _PROGRESS_NOISE_CAUSES)


def candidate_id_for_record(record_id: str) -> str:
    """Return the review-queue candidate id linked to an outcome record id.

    Mirrors the linkage created in ``outcome_gap._candidate_for_record``:
    ``outcome-gap-<digest>`` ↔ ``ctx-candidate-<digest>``.
    """
    digest = record_id.removeprefix("outcome-gap-")
    return f"ctx-candidate-{digest}"


@dataclass(frozen=True)
class GapRecurrence:
    """Recurrence stats for one gap category (``gap_summary``)."""

    gap_summary: str
    occurrences: int
    fix_approved: bool
    fix_approved_record_ids: list[str] = field(default_factory=list)
    fix_approved_at: str | None = None
    recurrence_after_fix: int = 0
    converging: bool = False
    last_seen: str = ""
    approved_constraint_injected: bool = False
    behavior_change_verified: bool = False
    effect_gap: str | None = None
    noise_class: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LoopStatus:
    """Aggregate health of the self-improvement loop."""

    total_outcomes: int
    gap_count: int
    pending_candidate_count: int
    approved_candidate_count: int
    recurrences: list[GapRecurrence] = field(default_factory=list)
    merged_candidate_count: int = 0
    distinct_pending_classes: int = 0
    backlog_alert: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_outcomes": self.total_outcomes,
            "gap_count": self.gap_count,
            "pending_candidate_count": self.pending_candidate_count,
            "approved_candidate_count": self.approved_candidate_count,
            "merged_candidate_count": self.merged_candidate_count,
            "distinct_pending_classes": self.distinct_pending_classes,
            "backlog_alert": self.backlog_alert,
            "converging_gap_count": sum(1 for rec in self.recurrences if rec.converging and not rec.noise_class),
            "behavior_change_verified_count": sum(1 for rec in self.recurrences if rec.behavior_change_verified and not rec.noise_class),
            "prompt_injection_gap_count": sum(1 for rec in self.recurrences if rec.effect_gap == "approved_constraint_not_in_prompt" and not rec.noise_class),
            "approved_recurrence_alert": any(
                rec.effect_gap == "constraint_injected_but_behavior_recurred" and not rec.noise_class
                for rec in self.recurrences
            ),
            "progress_noise_gap_count": sum(1 for rec in self.recurrences if rec.noise_class),
            "recurrences": [rec.to_dict() for rec in self.recurrences],
        }


def _parse_ts(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _probe_for_gap_summary(summary: str) -> str:
    """Return a sanitized current-request probe for resolver coverage checks."""
    if "practical_action" in summary or "completion" in summary:
        return "Complete the Sinria implementation task and apply approved Context Share self-improvement constraints before claiming done."
    return "Apply approved Context Share self-improvement constraints so the same correction does not recur."


def _approved_constraint_injected(
    *,
    evidence_id: str,
    prompt: str,
    durable_evidence_by_id: dict[str, Any],
) -> bool:
    """Verify a durable approved constraint reaches the resolver prompt.

    This is deliberately a prompt-assembly contract test, not an LLM judgment:
    the approved evidence must be present in the already-loaded durable store
    and appear in the formatted resolver block for a representative next
    request. ``compute_loop_status`` loads the evidence store once and builds at
    most one resolver prompt per gap class so status remains usable on large
    historical queues.
    """
    approved = durable_evidence_by_id.get(evidence_id)
    if approved is None or not approved.human_approved:
        return False
    return evidence_id in prompt and approved.summary in prompt


def compute_loop_status(
    *,
    outcome_path: Path | None = None,
    queue_path: Path | None = None,
    evidence_path: Path | None = None,
    backlog_alert_threshold: int = DEFAULT_BACKLOG_ALERT_THRESHOLD,
) -> LoopStatus:
    """Compute loop convergence status from sanitized local records.

    Read-only: never mutates the outcome log, review queue, or evidence store.
    """
    records: list[PracticalOutcomeRecord] = load_outcome_records(path=outcome_path or outcome_gap_path())
    candidates = load_review_candidates(path=queue_path or review_queue_path())
    durable_evidence = load_evidence_jsonl(evidence_path or evidence_store_path())
    durable_evidence_by_id = {item.evidence_id: item for item in durable_evidence}
    resolver = IntentResolver(ledger=EvidenceLedger(durable_evidence))

    approval_by_candidate_id = {candidate.candidate_id: candidate.approval_state for candidate in candidates}
    approved_at_by_candidate_id = {candidate.candidate_id: candidate.approved_at for candidate in candidates}
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    pending = sum(1 for state in approval_by_candidate_id.values() if state == "proposed")
    approved = sum(1 for state in approval_by_candidate_id.values() if state == "approved")
    merged = sum(1 for state in approval_by_candidate_id.values() if state == "merged")
    distinct_pending_classes = len({
        dedup_key(candidate) for candidate in candidates if candidate.approval_state == "proposed"
    })

    gap_records = [record for record in records if record.gap_detected]
    by_summary: dict[str, list[PracticalOutcomeRecord]] = {}
    for record in gap_records:
        by_summary.setdefault(record.gap_summary, []).append(record)

    recurrences: list[GapRecurrence] = []
    for summary, group in sorted(by_summary.items()):
        ordered = sorted(group, key=lambda record: _parse_ts(record.timestamp))
        approved_records = [
            record for record in ordered
            if approval_by_candidate_id.get(candidate_id_for_record(record.record_id)) == "approved"
        ]
        fix_approved = bool(approved_records)
        recurrence_after_fix = 0
        fix_approved_at: str | None = None
        injected = False
        behavior_change_verified = False
        effect_gap: str | None = None
        if fix_approved:
            # The fix point is the human review approval timestamp, not the
            # historical outcome timestamp. Bulk-approving historical gaps
            # should measure whether the gap recurs after approval, not punish
            # already-known pre-approval recurrences. Legacy candidates without
            # approved_at fall back to the linked outcome timestamp.
            fix_points: list[datetime] = []
            for record in approved_records:
                candidate_id = candidate_id_for_record(record.record_id)
                approved_at = approved_at_by_candidate_id.get(candidate_id)
                fix_points.append(_parse_ts(approved_at or record.timestamp))
            fix_ts = min(fix_points)
            fix_approved_at = fix_ts.isoformat()
            recurrence_after_fix = sum(1 for record in ordered if _parse_ts(record.timestamp) > fix_ts)
            approved_candidates = list({
                candidate.evidence.evidence_id: candidate
                for candidate in (
                    candidate_by_id[candidate_id_for_record(record.record_id)]
                    for record in approved_records
                    if candidate_id_for_record(record.record_id) in candidate_by_id
                )
            }.values())
            prompt = resolver.resolve(
                _probe_for_gap_summary(summary),
                platform="loop_metrics",
                project="sinria",
            ).format_for_prompt()
            injected = any(
                _approved_constraint_injected(
                    evidence_id=candidate.evidence.evidence_id,
                    prompt=prompt,
                    durable_evidence_by_id=durable_evidence_by_id,
                )
                for candidate in approved_candidates
            )
            behavior_change_verified = injected and recurrence_after_fix == 0
            if not injected:
                effect_gap = "approved_constraint_not_in_prompt"
            elif recurrence_after_fix > 0:
                effect_gap = "constraint_injected_but_behavior_recurred"
        recurrences.append(GapRecurrence(
            gap_summary=summary,
            occurrences=len(ordered),
            fix_approved=fix_approved,
            fix_approved_record_ids=[record.record_id for record in approved_records],
            fix_approved_at=fix_approved_at,
            recurrence_after_fix=recurrence_after_fix,
            converging=fix_approved and recurrence_after_fix == 0,
            last_seen=ordered[-1].timestamp if ordered else "",
            approved_constraint_injected=injected,
            behavior_change_verified=behavior_change_verified,
            effect_gap=effect_gap,
            noise_class=is_progress_noise_gap(summary),
        ))

    return LoopStatus(
        total_outcomes=len(records),
        gap_count=len(gap_records),
        pending_candidate_count=pending,
        approved_candidate_count=approved,
        recurrences=recurrences,
        merged_candidate_count=merged,
        distinct_pending_classes=distinct_pending_classes,
        backlog_alert=pending >= backlog_alert_threshold,
    )
