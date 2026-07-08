#!/usr/bin/env python3
"""Self-improvement loop CLI for Sinria Context Share.

Closes the Goal→Actual→Gap→Cause→Fix loop with human-review tooling:

  status       — JSON loop report including the convergence KPI
                 (same-gap recurrence after an approved durable fix → zero).
  list         — pending review-gated improvement candidates.
  approve      — promote one candidate to the durable evidence store.
  auto-triage  — compact duplicate pending candidates onto one row per
                 correction class; optionally auto-approve low-risk classes
                 (fail-closed policy). Dry-run unless --apply is given.

All output is sanitized category metadata and source pointers; raw
conversation content is never read or emitted. ``status``/``list`` are
read-only; ``approve`` is the explicit human review gate, and
``auto-triage --apply --approve-low-risk`` is the bounded automatic gate
sanctioned by docs/plans/2026-06-06-context-share-v2-self-improving-agent-os.md §4.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.context_share.auto_triage import DEFAULT_MIN_OCCURRENCES, run_auto_triage
from agent.context_share.loop_metrics import compute_loop_status
from agent.context_share.review_queue import approve_candidate, load_review_candidates


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _cmd_status(args: argparse.Namespace) -> int:
    status = compute_loop_status(
        outcome_path=Path(args.outcome_path) if args.outcome_path else None,
        queue_path=Path(args.queue_path) if args.queue_path else None,
        evidence_path=Path(args.evidence_path) if args.evidence_path else None,
    )
    report = status.to_dict()
    report["external_action_performed"] = False
    report["raw_private_context_exported"] = False
    _print(report)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    candidates = load_review_candidates(path=Path(args.queue_path) if args.queue_path else None)
    pending = [candidate for candidate in candidates if candidate.approval_state == "proposed"]
    _print({
        "pending_count": len(pending),
        "total_count": len(candidates),
        "external_action_performed": False,
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "approval_state": candidate.approval_state,
                "extraction_reason": candidate.extraction_reason,
                "summary": candidate.evidence.summary,
                "sanitized_sample": candidate.evidence.sanitized_sample,
                "source_session_id": candidate.evidence.source_session_id,
                "applies_to": candidate.evidence.applies_to,
            }
            for candidate in pending
        ],
    })
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    try:
        approved = approve_candidate(
            args.candidate_id,
            queue_path=Path(args.queue_path) if args.queue_path else None,
            evidence_path=Path(args.evidence_path) if args.evidence_path else None,
        )
    except ValueError as exc:
        _print({"error": str(exc), "approved_candidate_id": None})
        return 1
    _print({
        "approved_candidate_id": approved.candidate_id,
        "evidence_id": approved.evidence.evidence_id,
        "summary": approved.evidence.summary,
        "human_approved": approved.evidence.human_approved,
        "external_action_performed": False,
    })
    return 0


def _cmd_auto_triage(args: argparse.Namespace) -> int:
    if args.approve_low_risk and not args.apply:
        _print({"error": "--approve-low-risk requires --apply", "dry_run": True})
        return 1
    report = run_auto_triage(
        queue_path=Path(args.queue_path) if args.queue_path else None,
        evidence_path=Path(args.evidence_path) if args.evidence_path else None,
        apply=args.apply,
        approve_low_risk=args.approve_low_risk,
        min_occurrences=args.min_occurrences,
    )
    _print(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    status_parser = sub.add_parser("status", help="Loop convergence report (read-only).")
    status_parser.add_argument("--outcome-path", default=None)
    status_parser.add_argument("--queue-path", default=None)
    status_parser.add_argument("--evidence-path", default=None)
    status_parser.set_defaults(func=_cmd_status)

    list_parser = sub.add_parser("list", help="Pending review-gated candidates (read-only).")
    list_parser.add_argument("--queue-path", default=None)
    list_parser.set_defaults(func=_cmd_list)

    approve_parser = sub.add_parser("approve", help="Promote a candidate to durable evidence (human review gate).")
    approve_parser.add_argument("candidate_id")
    approve_parser.add_argument("--queue-path", default=None)
    approve_parser.add_argument("--evidence-path", default=None)
    approve_parser.set_defaults(func=_cmd_approve)

    triage_parser = sub.add_parser(
        "auto-triage",
        help="Compact duplicate pending candidates; optionally auto-approve low-risk classes (dry-run by default).",
    )
    triage_parser.add_argument("--queue-path", default=None)
    triage_parser.add_argument("--evidence-path", default=None)
    triage_parser.add_argument("--apply", action="store_true", help="Write the compaction (default: dry-run report only).")
    triage_parser.add_argument(
        "--approve-low-risk",
        action="store_true",
        help="With --apply: auto-approve representatives that pass the fail-closed low-risk policy.",
    )
    triage_parser.add_argument("--min-occurrences", type=int, default=DEFAULT_MIN_OCCURRENCES)
    triage_parser.set_defaults(func=_cmd_auto_triage)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
