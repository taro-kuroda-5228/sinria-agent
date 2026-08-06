"""Local, review-gated model architecture evaluation for Sinria.

The evaluator is model-name agnostic: any provider/model pair observed in the
metadata-only turn ledger is included automatically. It compares routes only
on stable workload references, preventing unrelated easy and hard cron jobs
from being mistaken for a model performance difference.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from statistics import median
from typing import Any, Iterable

from .efficiency_metrics import TurnEfficiencyRecord


@dataclass(frozen=True)
class ModelReviewThresholds:
    min_records_per_route: int = 5
    min_shared_workloads: int = 2
    min_token_reduction_ratio: float = 0.10
    min_latency_reduction_ratio: float = 0.05
    max_completion_rate_drop: float = 0.0
    max_verified_rate_drop: float = 0.0
    max_gap_rate_increase: float = 0.0
    max_retry_rate_increase: float = 0.05
    max_tool_error_rate_increase: float = 0.02


@dataclass(frozen=True)
class RouteSummary:
    provider: str
    model: str
    first_seen: str
    last_seen: str
    record_count: int
    workload_count: int
    total_tokens: int
    median_tokens: float
    median_api_duration_ms: float | None
    median_wall_duration_ms: float | None
    completion_rate: float
    verified_rate: float
    gap_rate: float
    retry_rate: float
    tool_error_rate: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _median_positive(values: Iterable[int]) -> float | None:
    usable = [value for value in values if value > 0]
    return float(median(usable)) if usable else None


def summarize_route(rows: Iterable[TurnEfficiencyRecord]) -> RouteSummary:
    records = list(rows)
    if not records:
        raise ValueError("at least one record is required")
    provider = records[0].provider
    model = records[0].model
    requests = sum(row.request_count for row in records)
    tool_calls = sum(row.tool_call_count for row in records)
    return RouteSummary(
        provider=provider,
        model=model,
        first_seen=min(row.timestamp for row in records),
        last_seen=max(row.timestamp for row in records),
        record_count=len(records),
        workload_count=len({row.workload_ref for row in records if row.workload_ref}),
        total_tokens=sum(row.total_tokens for row in records),
        median_tokens=float(median(row.total_tokens for row in records)),
        median_api_duration_ms=_median_positive(row.api_duration_ms for row in records),
        median_wall_duration_ms=_median_positive(row.wall_duration_ms for row in records),
        completion_rate=_ratio(sum(row.completed for row in records), len(records)),
        verified_rate=_ratio(sum(row.verified_completion for row in records), len(records)),
        gap_rate=_ratio(sum(row.gap_detected for row in records), len(records)),
        retry_rate=_ratio(sum(row.retry_count for row in records), requests),
        tool_error_rate=_ratio(sum(row.tool_error_count for row in records), tool_calls),
    )


def _route_key(row: TurnEfficiencyRecord) -> tuple[str, str]:
    return row.provider, row.model


def discover_routes(rows: Iterable[TurnEfficiencyRecord]) -> list[RouteSummary]:
    grouped: dict[tuple[str, str], list[TurnEfficiencyRecord]] = defaultdict(list)
    for row in rows:
        grouped[_route_key(row)].append(row)
    return [
        summarize_route(grouped[key])
        for key in sorted(grouped, key=lambda item: (item[0], item[1]))
    ]


def _matched_rows(
    rows: list[TurnEfficiencyRecord],
    baseline: tuple[str, str],
    candidate: tuple[str, str],
) -> tuple[list[TurnEfficiencyRecord], list[TurnEfficiencyRecord], set[str]]:
    baseline_workloads = {
        row.workload_ref for row in rows if _route_key(row) == baseline and row.workload_ref
    }
    candidate_workloads = {
        row.workload_ref for row in rows if _route_key(row) == candidate and row.workload_ref
    }
    shared = baseline_workloads & candidate_workloads
    return (
        [row for row in rows if _route_key(row) == baseline and row.workload_ref in shared],
        [row for row in rows if _route_key(row) == candidate and row.workload_ref in shared],
        shared,
    )


def _reduction(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None or baseline <= 0:
        return None
    return (baseline - candidate) / baseline


def compare_routes(
    rows: Iterable[TurnEfficiencyRecord],
    *,
    baseline: tuple[str, str],
    candidate: tuple[str, str],
    thresholds: ModelReviewThresholds | None = None,
) -> dict[str, Any]:
    """Compare two routes on shared workloads and return a review-only decision."""

    limits = thresholds or ModelReviewThresholds()
    records = list(rows)
    baseline_rows, candidate_rows, shared = _matched_rows(records, baseline, candidate)
    enough_data = (
        len(shared) >= limits.min_shared_workloads
        and len(baseline_rows) >= limits.min_records_per_route
        and len(candidate_rows) >= limits.min_records_per_route
    )
    if not baseline_rows or not candidate_rows:
        return {
            "baseline": {"provider": baseline[0], "model": baseline[1]},
            "candidate": {"provider": candidate[0], "model": candidate[1]},
            "shared_workloads": len(shared),
            "baseline_records": len(baseline_rows),
            "candidate_records": len(candidate_rows),
            "decision": "insufficient_data",
            "human_review_required": True,
            "auto_apply": False,
        }

    base = summarize_route(baseline_rows)
    cand = summarize_route(candidate_rows)
    token_reduction = _reduction(cand.median_tokens, base.median_tokens)
    api_latency_reduction = _reduction(
        cand.median_api_duration_ms, base.median_api_duration_ms
    )
    wall_latency_reduction = _reduction(
        cand.median_wall_duration_ms, base.median_wall_duration_ms
    )
    quality_regressions = {
        "completion_rate": cand.completion_rate
        < base.completion_rate - limits.max_completion_rate_drop,
        "verified_rate": cand.verified_rate
        < base.verified_rate - limits.max_verified_rate_drop,
        "gap_rate": cand.gap_rate > base.gap_rate + limits.max_gap_rate_increase,
        "retry_rate": cand.retry_rate
        > base.retry_rate + limits.max_retry_rate_increase,
        "tool_error_rate": cand.tool_error_rate
        > base.tool_error_rate + limits.max_tool_error_rate_increase,
    }
    faster = any(
        value is not None and value >= limits.min_latency_reduction_ratio
        for value in (api_latency_reduction, wall_latency_reduction)
    )
    lower_tokens = (
        token_reduction is not None
        and token_reduction >= limits.min_token_reduction_ratio
    )
    if not enough_data:
        decision = "insufficient_data"
    elif any(quality_regressions.values()):
        decision = "recommend_revert_or_restrict"
    elif lower_tokens or faster:
        decision = "recommend_canary_expansion"
    else:
        decision = "recommend_keep_current_scope"
    return {
        "baseline": base.to_dict(),
        "candidate": cand.to_dict(),
        "shared_workloads": len(shared),
        "token_reduction_ratio": token_reduction,
        "api_latency_reduction_ratio": api_latency_reduction,
        "wall_latency_reduction_ratio": wall_latency_reduction,
        "quality_regressions": quality_regressions,
        "thresholds": asdict(limits),
        "decision": decision,
        "human_review_required": True,
        "auto_apply": False,
    }


def build_architecture_review(
    rows: Iterable[TurnEfficiencyRecord],
    *,
    commander: tuple[str, str],
    thresholds: ModelReviewThresholds | None = None,
    cron_measurement_start: str = "",
) -> dict[str, Any]:
    """Build dynamic, matched-workload reviews across every observed route."""

    records = list(rows)
    routes = discover_routes(records)
    comparisons = []
    for index, left in enumerate(routes):
        for right in routes[index + 1 :]:
            left_key = (left.provider, left.model)
            right_key = (right.provider, right.model)
            # The route first observed earlier is the incumbent. This makes a
            # future Spark→new-light or Terra→new-heavy migration comparable
            # without requiring either route to be the Commander.
            if left_key == commander:
                baseline, candidate = left_key, right_key
            elif right_key == commander:
                baseline, candidate = right_key, left_key
            elif (left.first_seen, left_key) <= (right.first_seen, right_key):
                baseline, candidate = left_key, right_key
            else:
                baseline, candidate = right_key, left_key
            comparisons.append(
                compare_routes(
                    records,
                    baseline=baseline,
                    candidate=candidate,
                    thresholds=thresholds,
                )
            )

    valid_token_comparisons = [
        item
        for item in comparisons
        if item.get("decision") != "insufficient_data"
        and item.get("token_reduction_ratio") is not None
    ]
    cron_rows = [
        row
        for row in records
        if row.platform == "cron"
        and (not cron_measurement_start or row.timestamp >= cron_measurement_start)
    ]
    cron_tokens = sum(row.total_tokens for row in cron_rows)
    off_commander_tokens = sum(
        row.total_tokens for row in cron_rows if _route_key(row) != commander
    )
    return {
        "routes": [route.to_dict() for route in routes],
        "comparisons": comparisons,
        "cron": {
            "measurement_start": cron_measurement_start or None,
            "record_count": len(cron_rows),
            "total_tokens": cron_tokens,
            "tokens_routed_off_commander": off_commander_tokens,
            "off_commander_token_share": _ratio(off_commander_tokens, cron_tokens),
            "actual_token_reduction_ratio": (
                valid_token_comparisons[0]["token_reduction_ratio"]
                if len(valid_token_comparisons) == 1
                else None
            ),
            "matched_comparison_count": len(valid_token_comparisons),
        },
        "proposal_policy": {
            "dynamic_model_discovery": True,
            "matched_workloads_only": True,
            "quality_gated": True,
            "human_review_required": True,
            "auto_apply": False,
            "raw_private_context_exported": False,
        },
    }


__all__ = [
    "ModelReviewThresholds",
    "RouteSummary",
    "build_architecture_review",
    "compare_routes",
    "discover_routes",
    "summarize_route",
]
