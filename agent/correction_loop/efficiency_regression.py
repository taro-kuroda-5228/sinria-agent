"""Quality-gated efficiency comparisons for Sinria policy variants."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .efficiency_analysis import EfficiencySummary, summarize_efficiency
from .efficiency_metrics import TurnEfficiencyRecord


@dataclass(frozen=True)
class RegressionThresholds:
    min_records: int = 20
    min_token_reduction_ratio: float = 0.10
    max_token_increase_ratio: float = 0.05
    max_success_rate_drop: float = 0.0
    max_gap_rate_increase: float = 0.0
    max_retry_rate_increase: float = 0.0
    max_tool_error_rate_increase: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RegressionDecision:
    status: str
    safe_to_promote: bool
    reasons: tuple[str, ...]
    token_change_ratio: float | None
    success_rate_delta: float | None
    gap_rate_delta: float | None
    retry_rate_delta: float | None
    tool_error_rate_delta: float | None
    baseline: EfficiencySummary
    candidate: EfficiencySummary

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def _delta(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline


def compare_efficiency(
    baseline: EfficiencySummary,
    candidate: EfficiencySummary,
    *,
    thresholds: RegressionThresholds | None = None,
) -> RegressionDecision:
    limits = thresholds or RegressionThresholds()
    success_delta = _delta(candidate.success_rate, baseline.success_rate)
    gap_delta = _delta(candidate.gap_rate, baseline.gap_rate)
    retry_delta = _delta(candidate.retry_rate, baseline.retry_rate)
    tool_error_delta = _delta(candidate.tool_error_rate, baseline.tool_error_rate)

    token_change: float | None = None
    if (
        baseline.tokens_per_verified_completion is not None
        and baseline.tokens_per_verified_completion > 0
        and candidate.tokens_per_verified_completion is not None
    ):
        token_change = (
            candidate.tokens_per_verified_completion - baseline.tokens_per_verified_completion
        ) / baseline.tokens_per_verified_completion

    if baseline.record_count < limits.min_records or candidate.record_count < limits.min_records:
        return RegressionDecision(
            status="insufficient_data",
            safe_to_promote=False,
            reasons=("minimum_sample_not_met",),
            token_change_ratio=token_change,
            success_rate_delta=success_delta,
            gap_rate_delta=gap_delta,
            retry_rate_delta=retry_delta,
            tool_error_rate_delta=tool_error_delta,
            baseline=baseline,
            candidate=candidate,
        )

    quality_reasons: list[str] = []
    if success_delta is not None and success_delta < -limits.max_success_rate_drop:
        quality_reasons.append("success_rate_drop")
    if gap_delta is not None and gap_delta > limits.max_gap_rate_increase:
        quality_reasons.append("gap_rate_increase")
    if retry_delta is not None and retry_delta > limits.max_retry_rate_increase:
        quality_reasons.append("retry_rate_increase")
    if tool_error_delta is not None and tool_error_delta > limits.max_tool_error_rate_increase:
        quality_reasons.append("tool_error_rate_increase")

    if quality_reasons:
        status = "quality_regression"
        reasons = tuple(quality_reasons)
        safe_to_promote = False
    elif token_change is None:
        status = "insufficient_data"
        reasons = ("verified_completion_cost_unavailable",)
        safe_to_promote = False
    elif token_change <= -limits.min_token_reduction_ratio:
        status = "improved"
        reasons = ("verified_completion_cost_reduced",)
        safe_to_promote = True
    elif token_change > limits.max_token_increase_ratio:
        status = "efficiency_regression"
        reasons = ("verified_completion_cost_increased",)
        safe_to_promote = False
    else:
        status = "no_material_change"
        reasons = ("change_below_decision_threshold",)
        safe_to_promote = False

    return RegressionDecision(
        status=status,
        safe_to_promote=safe_to_promote,
        reasons=reasons,
        token_change_ratio=token_change,
        success_rate_delta=success_delta,
        gap_rate_delta=gap_delta,
        retry_rate_delta=retry_delta,
        tool_error_rate_delta=tool_error_delta,
        baseline=baseline,
        candidate=candidate,
    )


_MATCH_FIELDS = ("goal_kind", "model", "provider", "platform")
_SAFE_VARIANT = re.compile(r"^[A-Za-z0-9_.:/=()+-]{1,128}$")


def _validated_variant(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_VARIANT.fullmatch(value):
        raise ValueError("policy variant must be a sanitized identifier token")
    return value


def _match_key(record: TurnEfficiencyRecord) -> tuple[str, ...]:
    return tuple(str(getattr(record, field_name)) for field_name in _MATCH_FIELDS)


def _cohort_payload(key: tuple[str, ...]) -> dict[str, str]:
    return dict(zip(_MATCH_FIELDS, key))


def compare_policy_variants(
    records: Iterable[TurnEfficiencyRecord],
    *,
    baseline_variant: str,
    candidate_variant: str,
    thresholds: RegressionThresholds | None = None,
) -> dict[str, Any]:
    limits = thresholds or RegressionThresholds()
    baseline_variant = _validated_variant(baseline_variant)
    candidate_variant = _validated_variant(candidate_variant)
    baseline_groups: dict[tuple[str, ...], list[TurnEfficiencyRecord]] = defaultdict(list)
    candidate_groups: dict[tuple[str, ...], list[TurnEfficiencyRecord]] = defaultdict(list)

    for record in records:
        if record.policy_variant == baseline_variant:
            baseline_groups[_match_key(record)].append(record)
        elif record.policy_variant == candidate_variant:
            candidate_groups[_match_key(record)].append(record)

    baseline_keys = set(baseline_groups)
    candidate_keys = set(candidate_groups)
    matched_keys = sorted(baseline_keys & candidate_keys)
    cohorts = []
    matched_baseline: list[TurnEfficiencyRecord] = []
    matched_candidate: list[TurnEfficiencyRecord] = []
    cohort_decisions: list[RegressionDecision] = []
    for key in matched_keys:
        baseline_rows = baseline_groups[key]
        candidate_rows = candidate_groups[key]
        matched_baseline.extend(baseline_rows)
        matched_candidate.extend(candidate_rows)
        decision = compare_efficiency(
            summarize_efficiency(baseline_rows),
            summarize_efficiency(candidate_rows),
            thresholds=limits,
        )
        cohort_decisions.append(decision)
        cohorts.append({"cohort": _cohort_payload(key), "decision": decision.to_dict()})

    overall_decision = compare_efficiency(
        summarize_efficiency(matched_baseline),
        summarize_efficiency(matched_candidate),
        thresholds=limits,
    )
    unmatched_baseline = baseline_keys - candidate_keys
    unmatched_candidate = candidate_keys - baseline_keys
    safe_for_matched_cohorts = bool(cohort_decisions) and all(
        decision.safe_to_promote for decision in cohort_decisions
    )
    safe_to_promote_globally = (
        safe_for_matched_cohorts
        and overall_decision.safe_to_promote
        and not unmatched_baseline
        and not unmatched_candidate
    )
    return {
        "schema_version": 1,
        "baseline_variant": baseline_variant,
        "candidate_variant": candidate_variant,
        "thresholds": limits.to_dict(),
        "matched_cohort_count": len(matched_keys),
        "unmatched_baseline_cohort_count": len(unmatched_baseline),
        "unmatched_candidate_cohort_count": len(unmatched_candidate),
        "safe_for_matched_cohorts": safe_for_matched_cohorts,
        "safe_to_promote_globally": safe_to_promote_globally,
        "overall_decision": overall_decision.to_dict(),
        "cohorts": cohorts,
        "raw_private_context_exported": False,
        "external_action_performed": False,
    }
