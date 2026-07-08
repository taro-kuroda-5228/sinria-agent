#!/usr/bin/env python3
"""Local reviewer/extractor for Sinria Context Share v2.

Default is report-only. `--write-candidates` writes sanitized candidate rows to
`~/.sinria/context_share/review_queue.jsonl`; it never exports raw transcript
content or performs external actions.
"""

from __future__ import annotations

import argparse
import json

from agent.context_share.extraction import discover_session_evidence_candidates
from agent.context_share.intent_resolver import build_context_resolver_prompt
from agent.context_share.review_queue import load_review_candidates, write_review_candidates


def _load_db():
    from hermes_state import SessionDB
    return SessionDB()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not mutate memory, skills, or vault notes.")
    parser.add_argument("--write-candidates", action="store_true", help="Write sanitized evidence candidates to the local review queue.")
    parser.add_argument("--since", default="30d")
    parser.add_argument("--limit-per-query", type=int, default=20)
    args = parser.parse_args()
    if not args.dry_run and not args.write_candidates:
        parser.error("choose --dry-run or --write-candidates")

    candidates = []
    discovery_error = None
    try:
        db = _load_db()
        try:
            candidates = discover_session_evidence_candidates(db, limit_per_query=max(1, min(args.limit_per_query, 50)), since=args.since)
        finally:
            close = getattr(db, "close", None)
            if callable(close):
                close()
    except Exception as exc:  # recoverable; resolver preview still useful
        discovery_error = f"{type(exc).__name__}: {str(exc)[:180]}"

    written_path = None
    if args.write_candidates and candidates:
        written_path = str(write_review_candidates(candidates, append=True))

    queued = load_review_candidates()
    report = {
        "dry_run": bool(args.dry_run),
        "write_candidates": bool(args.write_candidates),
        "since": args.since,
        "external_action_performed": False,
        "raw_private_context_exported": False,
        "discovery_error": discovery_error,
        "candidate_count": len(candidates),
        "queued_candidate_count": len(queued),
        "written_path": written_path,
        "candidate_previews": [
            {
                "candidate_id": candidate.candidate_id,
                "evidence_id": candidate.evidence.evidence_id,
                "source_session_id": candidate.evidence.source_session_id,
                "summary": candidate.evidence.summary,
                "approval_state": candidate.approval_state,
                "raw_context_stored": candidate.raw_context_stored,
            }
            for candidate in candidates[:10]
        ],
        "resolver_preview": build_context_resolver_prompt("Sinria context share self-improvement review", project="sinria"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
