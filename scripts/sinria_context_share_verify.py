#!/usr/bin/env python3
"""Bounded verification runner for Sinria Context Share v2.

Runs focused suites serially to avoid the full-repo `Too many open files` failure
mode seen with very large xdist runs. Output is sanitized and bounded.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

_SECRETISH = re.compile(r"(?i)(api[_-]?key|secret|token|password|authorization|bearer)\s*[:= ]\s*\S+")


def _sanitize(text: str, *, limit: int = 1200) -> str:
    text = _SECRETISH.sub(r"\1=[REDACTED]", text or "")
    return text[-limit:]


def build_verification_commands() -> list[list[str]]:
    # sys.executable, not bare "python": cron/CI shells on this host lack a
    # PATH-resolvable python and must reuse the invoking interpreter.
    return [
        [
            sys.executable, "-m", "pytest",
            "tests/agent/context_share",
            "tests/agent/test_system_prompt_context_resolver.py",
            "tests/cron/test_context_resolver_injection.py",
            "tests/gateway/test_context_resolver_injection.py",
            "-q",
        ],
        [sys.executable, "-m", "compileall", "-q", "agent/context_share", "agent/system_prompt.py", "agent/conversation_loop.py", "run_agent.py", "gateway/session.py", "cron/scheduler.py", "scripts/sinria-context-share-review.py", "scripts/sinria_context_share_verify.py"],
    ]


def run_commands(commands: Sequence[Sequence[str]], *, cwd: str | os.PathLike[str]) -> dict:
    env = os.environ.copy()
    env["PYTEST_ADDOPTS"] = " ".join(part for part in [env.get("PYTEST_ADDOPTS", ""), "-n 0"] if part).strip()
    results = []
    for cmd in commands:
        proc = subprocess.run(list(cmd), cwd=cwd, text=True, capture_output=True, timeout=600, env=env)
        results.append({
            "command": list(cmd),
            "exit_code": proc.returncode,
            "stdout_tail": _sanitize(proc.stdout),
            "stderr_tail": _sanitize(proc.stderr),
        })
    return {
        "all_passed": all(result["exit_code"] == 0 for result in results),
        "external_action_performed": False,
        "raw_private_context_exported": False,
        "commands": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run_commands(build_verification_commands(), cwd=Path(__file__).resolve().parents[1])
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else report)
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
