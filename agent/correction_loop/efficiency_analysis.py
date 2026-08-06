"""Read-only efficiency analysis for Sinria's verified-completion loop.

Only counters and fixed categorical metadata from ``efficiency_metrics`` are
processed. No prompt, response, tool payload, PHI, or PII is accepted or
emitted by this module.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

from .efficiency_metrics import TurnEfficiencyRecord


@dataclass(frozen=True)
class EfficiencySummary:
    record_count: int
    practical_record_count: int
    completed_count: int
    verified_completion_count: int
    gap_count: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int
    request_count: int
    retry_count: int
    tool_call_count: int
    tool_error_count: int
    system_chars: int
    history_chars: int
    tool_result_chars: int
    tool_schema_chars: int
    request_chars: int
    tool_selection_evaluated_record_count: int
    tool_selection_evaluated_request_count: int
    tool_selection_applied_request_count: int
    tool_selection_schema_chars_before: int
    tool_selection_schema_chars_after: int
    projected_tool_schema_char_savings: int
    projected_tool_schema_reduction_rate: float | None
    tokens_per_verified_completion: float | None
    success_rate: float | None
    gap_rate: float | None
    retry_rate: float | None
    tool_error_rate: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OptimizationCandidate:
    candidate_type: str
    reason_code: str
    priority: str
    sample_size: int
    evidence: dict[str, int | float | None]
    requires_human_review: bool = True
    auto_apply_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def summarize_efficiency(records: Iterable[TurnEfficiencyRecord]) -> EfficiencySummary:
    rows = list(records)
    practical_rows = [row for row in rows if row.goal_kind == "practical_action"]
    completed_count = sum(1 for row in rows if row.completed)
    verified_count = sum(1 for row in rows if row.verified_completion)
    gap_count = sum(1 for row in rows if row.gap_detected)

    def total(field_name: str) -> int:
        return sum(int(getattr(row, field_name, 0) or 0) for row in rows)

    total_tokens = total("total_tokens")
    request_count = total("request_count")
    retry_count = total("retry_count")
    tool_call_count = total("tool_call_count")
    tool_error_count = total("tool_error_count")
    success_numerator = verified_count if practical_rows else completed_count
    success_denominator = len(practical_rows) if practical_rows else len(rows)
    selection_rows = [
        row
        for row in rows
        if getattr(row, "tool_selection_mode", "off") in {"shadow", "active"}
    ]
    selection_before = total("tool_selection_schema_chars_before")
    selection_after = total("tool_selection_schema_chars_after")
    projected_savings = max(0, selection_before - selection_after)

    return EfficiencySummary(
        record_count=len(rows),
        practical_record_count=len(practical_rows),
        completed_count=completed_count,
        verified_completion_count=verified_count,
        gap_count=gap_count,
        input_tokens=total("input_tokens"),
        output_tokens=total("output_tokens"),
        reasoning_tokens=total("reasoning_tokens"),
        cache_read_tokens=total("cache_read_tokens"),
        cache_write_tokens=total("cache_write_tokens"),
        total_tokens=total_tokens,
        request_count=request_count,
        retry_count=retry_count,
        tool_call_count=tool_call_count,
        tool_error_count=tool_error_count,
        system_chars=total("system_chars"),
        history_chars=total("history_chars"),
        tool_result_chars=total("tool_result_chars"),
        tool_schema_chars=total("tool_schema_chars"),
        request_chars=total("request_chars"),
        tool_selection_evaluated_record_count=len(selection_rows),
        tool_selection_evaluated_request_count=sum(
            max(0, int(row.request_count)) for row in selection_rows
        ),
        tool_selection_applied_request_count=total(
            "tool_selection_applied_requests"
        ),
        tool_selection_schema_chars_before=selection_before,
        tool_selection_schema_chars_after=selection_after,
        projected_tool_schema_char_savings=projected_savings,
        projected_tool_schema_reduction_rate=_rate(
            projected_savings, selection_before
        ),
        tokens_per_verified_completion=_rate(total_tokens, verified_count),
        success_rate=_rate(success_numerator, success_denominator),
        gap_rate=_rate(gap_count, len(rows)),
        retry_rate=_rate(retry_count, request_count),
        tool_error_rate=_rate(tool_error_count, tool_call_count),
    )


_COHORT_FIELDS = ("policy_variant", "goal_kind", "model", "provider", "platform")


def _cohort_key(record: TurnEfficiencyRecord) -> tuple[str, ...]:
    return tuple(str(getattr(record, field_name)) for field_name in _COHORT_FIELDS)


def cohort_metadata(key: Sequence[str]) -> dict[str, str]:
    return dict(zip(_COHORT_FIELDS, key))


def group_efficiency_records(
    records: Iterable[TurnEfficiencyRecord],
) -> dict[tuple[str, ...], list[TurnEfficiencyRecord]]:
    grouped: dict[tuple[str, ...], list[TurnEfficiencyRecord]] = defaultdict(list)
    for record in records:
        grouped[_cohort_key(record)].append(record)
    return dict(grouped)


def filter_efficiency_records(
    records: Iterable[TurnEfficiencyRecord],
    *,
    policy_variant: str | None = None,
    goal_kind: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    platform: str | None = None,
) -> list[TurnEfficiencyRecord]:
    filters = {
        "policy_variant": policy_variant,
        "goal_kind": goal_kind,
        "model": model,
        "provider": provider,
        "platform": platform,
    }
    return [
        record
        for record in records
        if all(expected is None or getattr(record, field_name) == expected for field_name, expected in filters.items())
    ]


def build_efficiency_status(records: Iterable[TurnEfficiencyRecord]) -> dict[str, Any]:
    rows = list(records)
    grouped = group_efficiency_records(rows)
    cohorts = [
        {
            "cohort": cohort_metadata(key),
            "summary": summarize_efficiency(grouped[key]).to_dict(),
        }
        for key in sorted(grouped)
    ]
    return {
        "schema_version": 1,
        "overall": summarize_efficiency(rows).to_dict(),
        "cohorts": cohorts,
        "raw_private_context_exported": False,
        "external_action_performed": False,
    }


def _share(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return part / whole


def _priority(value: float, high_threshold: float) -> str:
    return "high" if value >= high_threshold else "medium"


def generate_optimization_candidates(
    records: Iterable[TurnEfficiencyRecord],
    *,
    min_records: int = 20,
) -> list[OptimizationCandidate]:
    rows = list(records)
    summary = summarize_efficiency(rows)
    if summary.record_count < max(1, int(min_records)):
        return []

    candidates: list[OptimizationCandidate] = []
    schema_share = _share(summary.tool_schema_chars, summary.request_chars)
    history_share = _share(summary.history_chars, summary.request_chars)
    tool_result_share = _share(summary.tool_result_chars, summary.request_chars)
    retry_rate = summary.retry_rate or 0.0

    common = {
        "sample_size": summary.record_count,
        "tokens_per_verified_completion": summary.tokens_per_verified_completion,
    }
    projected_reduction = summary.projected_tool_schema_reduction_rate or 0.0
    if projected_reduction > 0.0:
        candidates.append(
            OptimizationCandidate(
                candidate_type="dynamic_tool_selection",
                reason_code="shadow_tool_schema_reduction_available",
                priority=_priority(projected_reduction, 0.50),
                sample_size=summary.record_count,
                evidence={
                    **common,
                    "tool_schema_share": schema_share,
                    "projected_schema_reduction_rate": projected_reduction,
                    "projected_schema_char_savings": summary.projected_tool_schema_char_savings,
                },
            )
        )
    elif schema_share >= 0.20:
        candidates.append(
            OptimizationCandidate(
                candidate_type="dynamic_tool_selection",
                reason_code="tool_schema_share_high",
                priority=_priority(schema_share, 0.40),
                sample_size=summary.record_count,
                evidence={**common, "tool_schema_share": schema_share},
            )
        )
    if history_share >= 0.45:
        candidates.append(
            OptimizationCandidate(
                candidate_type="structured_work_state",
                reason_code="history_share_high",
                priority=_priority(history_share, 0.60),
                sample_size=summary.record_count,
                evidence={**common, "history_share": history_share},
            )
        )
    if tool_result_share >= 0.25:
        candidates.append(
            OptimizationCandidate(
                candidate_type="tool_result_references",
                reason_code="tool_result_share_high",
                priority=_priority(tool_result_share, 0.40),
                sample_size=summary.record_count,
                evidence={**common, "tool_result_share": tool_result_share},
            )
        )
    if retry_rate >= 0.10:
        candidates.append(
            OptimizationCandidate(
                candidate_type="retry_diagnosis",
                reason_code="retry_rate_high",
                priority=_priority(retry_rate, 0.20),
                sample_size=summary.record_count,
                evidence={**common, "retry_rate": retry_rate},
            )
        )
    return candidates
