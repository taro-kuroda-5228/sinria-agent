"""Tests for the Phase 1 code-defect observation report."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load_module():
    spec = importlib.util.spec_from_file_location("repair_report", REPO / "scripts" / "repair_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _event(ts, fingerprint="fp-a", exc="ValueError", severity="medium", transient=False, repo="sinria"):
    return {
        "timestamp": ts,
        "fingerprint": fingerprint,
        "exc_class": exc,
        "severity": severity,
        "transient_likely": transient,
        "repo": repo,
        "code_location": "agent/x.py:10",
        "defect_kind": "unhandled_exception",
    }


NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)


def test_build_report_counts_and_window():
    mod = _load_module()
    events = [
        _event("2026-07-05T12:00:00Z"),
        _event("2026-07-06T10:00:00Z"),
        _event("2026-07-06T11:00:00Z", fingerprint="fp-b", exc="TimeoutError", transient=True),
        _event("2026-06-01T00:00:00Z"),  # outside the window
    ]
    report = mod.build_report(events, window_days=7, now=NOW)
    assert report["window_events"] == 3
    assert report["total_events"] == 4
    assert report["unique_fingerprints"] == 2
    assert report["transient_ratio"] == round(1 / 3, 3)
    assert report["top_fingerprints"][0]["fingerprint"] == "fp-a"
    assert report["top_fingerprints"][0]["occurrence_count"] == 2


def test_build_report_empty():
    mod = _load_module()
    report = mod.build_report([], window_days=7, now=NOW)
    assert report["window_events"] == 0
    assert report["transient_ratio"] is None


def test_render_markdown_mentions_key_numbers():
    mod = _load_module()
    report = mod.build_report([_event("2026-07-06T10:00:00Z")], window_days=7, now=NOW)
    md = mod.render_markdown(report)
    assert "code-defect" in md
    assert "fp-a" in md
    assert "ValueError" in md
