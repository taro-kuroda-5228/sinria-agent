#!/usr/bin/env python3
"""verify-after-act 運用観察 report — measures the P1 actuator's effect.

Reads the sanitized JSONL stores under ``SINRIA_HOME/context_share/``
(outcome_gap.jsonl, verify_nudges.jsonl, routing_signals.jsonl) and reports,
for the observation window vs the pre-P1 baseline:

* practical-action turn counts
* verified-completion rate (up is good)
* unverified-claim rate / verification_gap cause rate (down is good)
* nudges fired and the nudge→verified conversion rate
* routing escalation signals recorded

Counts and rates only — no conversation text, session ids never leave the
JSON detail (the markdown body is aggregate-only). Designed to run as a
no-agent Sinria cron job (stdout is the delivered report).

Usage:
    python scripts/verify_after_act_report.py [--window-days 7] [--json]
        [--baseline-end 2026-07-06T09:52:00Z]

The default baseline boundary is the P1 gateway go-live
(2026-07-06 18:52 JST). Observation plan: weekly report, judge after two
reports (≥2 weeks) whether agent.verify_after_act stays default-on.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# P1 gateway go-live (2026-07-06 18:52 JST).
DEFAULT_BASELINE_END = "2026-07-06T09:52:00Z"
NUDGE_JOIN_WINDOW_MINUTES = 30


def _parse_ts(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _rate(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator, 3) if denominator else None


def _bucket_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    practical = [r for r in records if r.get("goal_kind") == "practical_action"]
    verified = [
        r for r in practical if r.get("actual_kind") == "verified_practical_completion"
    ]
    unverified = [
        r for r in practical
        if r.get("actual_kind") == "claimed_without_visible_verification"
    ]
    gap = [r for r in practical if r.get("cause_kind") == "verification_gap"]
    return {
        "practical_turns": len(practical),
        "verified": len(verified),
        "verified_rate": _rate(len(verified), len(practical)),
        "unverified_claims": len(unverified),
        "unverified_claim_rate": _rate(len(unverified), len(practical)),
        "verification_gap_causes": len(gap),
    }


def build_report(
    *,
    outcome_records: list[dict[str, Any]],
    nudge_events: list[dict[str, Any]],
    routing_signals: list[dict[str, Any]],
    baseline_end: str,
    window_start: str,
    window_end: Optional[str] = None,
) -> dict[str, Any]:
    """Compute the observation report from already-loaded JSONL rows."""
    baseline_cut = _parse_ts(baseline_end)
    start = _parse_ts(window_start)
    end = _parse_ts(window_end) if window_end else None

    def _in_window(ts: Optional[datetime]) -> bool:
        return ts is not None and ts >= start and (end is None or ts <= end)

    baseline_rows, window_rows = [], []
    for row in outcome_records:
        ts = _parse_ts(row.get("timestamp", ""))
        if ts is None:
            continue
        if ts < baseline_cut:
            baseline_rows.append(row)
        if _in_window(ts):
            window_rows.append(row)

    window = _bucket_stats(window_rows)

    window_nudges = [
        n for n in nudge_events if _in_window(_parse_ts(n.get("timestamp", "")))
    ]
    join_delta = timedelta(minutes=NUDGE_JOIN_WINDOW_MINUTES)
    converted = 0
    for nudge in window_nudges:
        nudge_ts = _parse_ts(nudge.get("timestamp", ""))
        session = nudge.get("session_id") or ""
        for row in window_rows:
            if row.get("session_id") != session:
                continue
            if row.get("actual_kind") != "verified_practical_completion":
                continue
            row_ts = _parse_ts(row.get("timestamp", ""))
            if row_ts and nudge_ts and nudge_ts <= row_ts <= nudge_ts + join_delta:
                converted += 1
                break
    window["nudges_fired"] = len(window_nudges)
    window["nudge_converted_to_verified"] = converted
    window["nudge_conversion_rate"] = _rate(converted, len(window_nudges))
    window["routing_signals"] = sum(
        1 for s in routing_signals if _in_window(_parse_ts(s.get("timestamp", "")))
    )

    return {
        "baseline_end": baseline_end,
        "window_start": window_start,
        "window_end": window_end,
        "baseline": _bucket_stats(baseline_rows),
        "window": window,
    }


def render_markdown(report: dict[str, Any]) -> str:
    baseline, window = report["baseline"], report["window"]

    def _pct(value: Optional[float]) -> str:
        return f"{value:.0%}" if value is not None else "n/a"

    lines = [
        "## verify-after-act 運用観察レポート",
        "",
        f"- window: {report['window_start']} → {report['window_end'] or 'now'}"
        f" / baseline: pre {report['baseline_end']} (P1 go-live)",
        "",
        "| metric | baseline | window |",
        "|---|---|---|",
        f"| practical turns | {baseline['practical_turns']} | {window['practical_turns']} |",
        f"| verified rate ↑ | {_pct(baseline['verified_rate'])} | {_pct(window['verified_rate'])} |",
        f"| unverified-claim rate ↓ | {_pct(baseline['unverified_claim_rate'])} | {_pct(window['unverified_claim_rate'])} |",
        f"| verification_gap causes | {baseline['verification_gap_causes']} | {window['verification_gap_causes']} |",
        "",
        f"- nudges fired: {window['nudges_fired']}"
        f" / converted to verified: {window['nudge_converted_to_verified']}"
        f" ({_pct(window['nudge_conversion_rate'])})",
        f"- routing escalation signals: {window['routing_signals']}",
        "",
        "判定基準: verified rate 上昇 + unverified-claim rate 低下 + nudge 変換率が有意なら "
        "verify-after-act は既定 on 維持。nudge が高頻度かつ低変換なら "
        "`agent.verify_after_act: false` を検討し、routing signals が蓄積していれば "
        "自動エスカレーション設計に進む。",
    ]
    return "\n".join(lines)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--baseline-end", default=DEFAULT_BASELINE_END)
    parser.add_argument("--json", action="store_true", help="Emit the JSON report instead of markdown")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from hermes_constants import get_sinria_home

    share = get_sinria_home() / "context_share"
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(days=args.window_days)).isoformat().replace("+00:00", "Z")

    report = build_report(
        outcome_records=_load_jsonl(share / "outcome_gap.jsonl"),
        nudge_events=_load_jsonl(share / "verify_nudges.jsonl"),
        routing_signals=_load_jsonl(share / "routing_signals.jsonl"),
        baseline_end=args.baseline_end,
        window_start=window_start,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
