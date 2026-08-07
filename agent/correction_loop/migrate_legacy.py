"""Explicit, non-destructive migration from retired Context Share data stores.

The active runtime never reads the legacy store. This module is packaged so an
existing installation can opt in to copying compatible records before the old
archive is removed. Re-running is idempotent.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from sinria_constants import get_sinria_home

CORRECTION_FILES = (
    "evidence.jsonl",
    "review_queue.jsonl",
    "outcome_gap.jsonl",
    "efficiency_turns.jsonl",
    "loop_health.json",
    "maintenance_signals.jsonl",
    "routing_signals.jsonl",
    "strategist_selection.jsonl",
    "verify_nudges.jsonl",
)
REPAIR_FILES = ("code_defects.jsonl", "repair_outcomes.jsonl")


def migrate(home: Path, *, apply: bool = False) -> list[tuple[Path, Path, str]]:
    legacy = home / "context_share"
    actions: list[tuple[Path, Path, str]] = []
    for name in CORRECTION_FILES:
        src, dst = legacy / name, home / "corrections" / name
        if not src.exists():
            continue
        status = "exists" if dst.exists() else "copy"
        actions.append((src, dst, status))
        if apply and status == "copy":
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    for name in REPAIR_FILES:
        src, dst = legacy / name, home / "repair" / name
        if not src.exists():
            continue
        status = "exists" if dst.exists() else "copy"
        actions.append((src, dst, status))
        if apply and status == "copy":
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Copy compatible legacy files. Without this flag, perform a dry run.",
    )
    args = parser.parse_args()
    home = get_sinria_home()
    actions = migrate(home, apply=args.apply)
    mode = "applied" if args.apply else "dry-run"
    print(f"migration={mode} candidates={len(actions)}")
    for src, dst, status in actions:
        print(f"{status}: {src} -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
