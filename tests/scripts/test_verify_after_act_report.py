"""Tests for scripts/verify_after_act_report.py (P1 運用観察 measurement)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_report_module():
    spec = importlib.util.spec_from_file_location(
        "verify_after_act_report", REPO_ROOT / "scripts" / "verify_after_act_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _outcome(ts, goal="practical_action", actual="verified_practical_completion",
             cause="none", session="s1"):
    return {
        "timestamp": ts,
        "goal_kind": goal,
        "actual_kind": actual,
        "cause_kind": cause,
        "session_id": session,
    }


BASELINE_END = "2026-07-06T09:52:00Z"


def test_report_compares_window_against_baseline():
    mod = _load_report_module()
    records = [
        # Baseline (pre-P1): 2 practical, 1 unverified claim
        _outcome("2026-07-01T00:00:00Z", actual="claimed_without_visible_verification", cause="verification_gap"),
        _outcome("2026-07-02T00:00:00Z"),
        # Window (post-P1): 3 practical, all verified
        _outcome("2026-07-07T00:00:00Z", session="s2"),
        _outcome("2026-07-08T00:00:00Z", session="s3"),
        _outcome("2026-07-09T00:00:00Z", session="s4"),
        # Non-practical noise is excluded from rates
        _outcome("2026-07-09T01:00:00Z", goal="question", actual="answered_question"),
    ]
    report = mod.build_report(
        outcome_records=records, nudge_events=[], routing_signals=[],
        baseline_end=BASELINE_END, window_start="2026-07-06T09:52:00Z",
    )
    assert report["baseline"]["practical_turns"] == 2
    assert report["baseline"]["unverified_claim_rate"] == 0.5
    assert report["window"]["practical_turns"] == 3
    assert report["window"]["verified_rate"] == 1.0
    assert report["window"]["unverified_claim_rate"] == 0.0


def test_nudge_conversion_joins_by_session_and_time():
    mod = _load_report_module()
    records = [
        _outcome("2026-07-07T10:05:00Z", session="s1"),  # verified 5 min after nudge
        _outcome("2026-07-07T12:00:00Z", session="s2",
                 actual="claimed_without_visible_verification", cause="verification_gap"),
    ]
    nudges = [
        {"timestamp": "2026-07-07T10:00:00Z", "session_id": "s1", "tier": "large"},
        {"timestamp": "2026-07-07T11:58:00Z", "session_id": "s2", "tier": "small"},
    ]
    report = mod.build_report(
        outcome_records=records, nudge_events=nudges, routing_signals=[],
        baseline_end=BASELINE_END, window_start="2026-07-06T09:52:00Z",
    )
    assert report["window"]["nudges_fired"] == 2
    assert report["window"]["nudge_converted_to_verified"] == 1


def test_routing_signals_counted_in_window():
    mod = _load_report_module()
    report = mod.build_report(
        outcome_records=[], nudge_events=[],
        routing_signals=[
            {"timestamp": "2026-07-07T00:00:00Z", "tier": "small", "cause_kind": "verification_gap"},
            {"timestamp": "2026-07-01T00:00:00Z", "tier": "small", "cause_kind": "verification_gap"},
        ],
        baseline_end=BASELINE_END, window_start="2026-07-06T09:52:00Z",
    )
    assert report["window"]["routing_signals"] == 1


def test_markdown_render_is_sanitized_counts_only():
    mod = _load_report_module()
    report = mod.build_report(
        outcome_records=[_outcome("2026-07-07T00:00:00Z")],
        nudge_events=[], routing_signals=[],
        baseline_end=BASELINE_END, window_start="2026-07-06T09:52:00Z",
    )
    text = mod.render_markdown(report)
    assert "verify-after-act" in text.lower()
    assert "verified_rate" in text or "verified rate" in text.lower()
    # Sanitized: no session ids leak into the report body.
    assert "s1" not in text


def test_empty_stores_render_without_error():
    mod = _load_report_module()
    report = mod.build_report(
        outcome_records=[], nudge_events=[], routing_signals=[],
        baseline_end=BASELINE_END, window_start="2026-07-06T09:52:00Z",
    )
    assert report["window"]["practical_turns"] == 0
    assert mod.render_markdown(report)
