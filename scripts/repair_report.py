#!/usr/bin/env python3
"""code-defect telemetry report — self-repair loop Phase 1 observation.

Reads the sanitized ``SINRIA_HOME/context_share/code_defects.jsonl`` and
reports, for the observation window: event counts, unique fingerprints,
severity/repo distribution, transient-noise ratio, and the top recurring
fingerprints. Aggregate-only — nothing beyond exception class names and
repo-relative code locations ever reaches the report body.

Designed to run as a no-agent Sinria cron job (stdout is the delivered
report), same operating shape as scripts/verify_after_act_report.py.

Usage:
    python scripts/repair_report.py [--window-days 7] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermes_constants import get_sinria_home  # noqa: E402

TOP_N = 10


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


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            events.append(json.loads(stripped))
        except ValueError:
            continue
    return events


def build_report(events: list[dict[str, Any]], *, window_days: int, now: datetime) -> dict[str, Any]:
    cutoff = now - timedelta(days=window_days)
    windowed = [e for e in events if (_parse_ts(str(e.get("timestamp", ""))) or cutoff) >= cutoff]
    buckets: dict[str, list[dict[str, Any]]] = {}
    for event in windowed:
        buckets.setdefault(str(event.get("fingerprint", "")), []).append(event)
    buckets.pop("", None)
    transient = sum(1 for e in windowed if e.get("transient_likely"))
    by_severity: dict[str, int] = {}
    by_repo: dict[str, int] = {}
    for event in windowed:
        severity = str(event.get("severity", "unknown"))
        repo = str(event.get("repo", "unknown"))
        by_severity[severity] = by_severity.get(severity, 0) + 1
        by_repo[repo] = by_repo.get(repo, 0) + 1
    top = sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True)[:TOP_N]
    return {
        "window_days": window_days,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "total_events": len(events),
        "window_events": len(windowed),
        "unique_fingerprints": len(buckets),
        "transient_ratio": round(transient / len(windowed), 3) if windowed else None,
        "by_severity": by_severity,
        "by_repo": by_repo,
        "top_fingerprints": [
            {
                "fingerprint": fingerprint,
                "occurrence_count": len(items),
                "exc_class": str(items[-1].get("exc_class", "unknown")),
                "code_location": str(items[-1].get("code_location", "unknown")),
                "severity": str(items[-1].get("severity", "unknown")),
                "transient_likely": bool(items[-1].get("transient_likely", False)),
            }
            for fingerprint, items in top
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "## code-defect telemetry report (self-repair Phase 1)",
        "",
        f"- window: last {report['window_days']} days / generated: {report['generated_at']}",
        f"- events in window: {report['window_events']} (all-time {report['total_events']})",
        f"- unique fingerprints: {report['unique_fingerprints']}",
        f"- transient-noise ratio: {report['transient_ratio'] if report['transient_ratio'] is not None else 'n/a'}",
        f"- by severity: {report['by_severity'] or 'none'}",
        f"- by repo: {report['by_repo'] or 'none'}",
        "",
        "| fingerprint | count | exc_class | location | severity | transient |",
        "|---|---|---|---|---|---|",
    ]
    for item in report["top_fingerprints"]:
        lines.append(
            f"| {item['fingerprint']} | {item['occurrence_count']} | {item['exc_class']} "
            f"| {item['code_location']} | {item['severity']} | {item['transient_likely']} |"
        )
    if not report["top_fingerprints"]:
        lines.append("| (no defects in window) | - | - | - | - | - |")
    lines.append("")
    lines.append(
        "判定メモ: transient-noise ratio が高い場合は捕捉フィルタを先に調整。"
        "再発 fingerprint (count ≥ 3) が Phase 2 Repair Orchestrator の起票候補。"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    path = get_sinria_home() / "context_share" / "code_defects.jsonl"
    report = build_report(load_events(path), window_days=args.window_days, now=datetime.now(timezone.utc))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
