#!/usr/bin/env python3
"""Install the member-side LINE peer relay as a stable macOS LaunchAgent."""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
from pathlib import Path

from sinria_constants import get_sinria_home


LABEL = "ai.sinria.line-peer-relay"


def resolve_primary_checkout(root: Path) -> Path:
    value = subprocess.check_output(
        ["git", "-C", str(root.resolve()), "worktree", "list", "--porcelain"],
        text=True,
    )
    for line in value.splitlines():
        if line.startswith("worktree "):
            candidate = Path(line.removeprefix("worktree ")).resolve()
            if candidate.name == ".git" or candidate.suffix == ".git":
                break
            return candidate
    raise SystemExit("Sinria primary checkout could not be resolved")


def python_path(root: Path) -> Path:
    for candidate in (
        root / ".venv/bin/python",
        root / "venv/bin/python",
        get_sinria_home() / "sinria-agent/venv/bin/python",
    ):
        if candidate.exists():
            return candidate.absolute()
    raise SystemExit("Sinria Python environment not found")


def build_plist(*, root: Path, host: str, port: int) -> dict:
    root = root.resolve()
    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("LINE peer relay host must be loopback")
    relay = root / "scripts/sinria-line-peer-relay.py"
    if not relay.exists():
        raise SystemExit("LINE peer relay script not found in primary checkout")
    logs = get_sinria_home() / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(python_path(root)), str(relay), "--host", host, "--port", str(port),
        ],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {
            "SINRIA_HOME": str(get_sinria_home()),
            "PYTHONUNBUFFERED": "1",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(logs / "line-peer-relay.log"),
        "StandardErrorPath": str(logs / "line-peer-relay.error.log"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--no-load", action="store_true")
    args = parser.parse_args()

    root = resolve_primary_checkout(args.root)
    plist = build_plist(root=root, host=args.host, port=args.port)
    env = os.environ.copy()
    env.update(plist["EnvironmentVariables"])
    preflight = subprocess.run(
        plist["ProgramArguments"][:2] + ["--check"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    try:
        receipt = json.loads(preflight.stdout) if preflight.stdout else None
    except json.JSONDecodeError:
        receipt = {"ok": False, "error": "invalid_preflight_output"}
    if args.preflight:
        print(json.dumps({
            "exit": preflight.returncode,
            "result": receipt,
            "root": str(root),
        }))
        raise SystemExit(preflight.returncode)
    if preflight.returncode != 0 or not isinstance(receipt, dict) or receipt.get("ok") is not True:
        print(json.dumps({"installed": False, "preflight": receipt, "root": str(root)}))
        raise SystemExit(preflight.returncode or 2)

    launch_agents = Path.home() / "Library/LaunchAgents"
    logs = get_sinria_home() / "logs"
    launch_agents.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    path = launch_agents / f"{LABEL}.plist"
    temporary = path.with_suffix(".plist.tmp")
    temporary.write_bytes(plistlib.dumps(plist))
    os.chmod(temporary, 0o600)
    temporary.replace(path)

    if not args.no_load:
        if not hasattr(os, "getuid"):
            raise SystemExit("LINE peer relay LaunchAgent installation requires macOS")
        domain = f"gui/{os.getuid()}"  # windows-footgun: ok -- guarded macOS launchd path
        subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
        subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"], check=True)
    print(json.dumps({
        "installed": True,
        "label": LABEL,
        "plist": str(path),
        "root": str(root),
        "loaded": not args.no_load,
        "preflight": receipt,
    }))


if __name__ == "__main__":
    main()
