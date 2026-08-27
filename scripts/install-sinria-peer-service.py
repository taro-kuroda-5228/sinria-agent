#!/usr/bin/env python3
"""Install a persistent macOS Sinria peer executor or validator service."""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
from pathlib import Path


def git_common_dir(root: Path) -> Path:
    value = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        text=True,
    ).strip()
    return Path(value).resolve()


def resolve_primary_checkout(root: Path) -> Path:
    common = git_common_dir(root.resolve())
    if common.name == ".git":
        return common.parent.resolve()
    return root.resolve()


def python_path(root: Path) -> Path:
    for candidate in (root / ".venv/bin/python", root / "venv/bin/python", Path.home() / ".sinria/sinria-agent/venv/bin/python"):
        if candidate.exists():
            # Keep the venv entrypoint path. Resolving its symlink to the base
            # interpreter disables venv dependency discovery under launchd.
            return candidate.absolute()
    raise SystemExit("Sinria Python environment not found")


def build_plist(*, root: Path, mode: str, member_id: str, instance_id: str,
                subject: str, base_url: str, poll_interval: int, notify_target: str = "") -> dict:
    root = root.resolve()
    python = python_path(root)
    worker = root / "scripts/sinria-peer-worker.py"
    command_script = root / "scripts" / (
        "peer-consultation-executor.py" if mode == "executor" else "synthetic-peer-validator.py"
    )
    if not worker.exists() or not command_script.exists():
        raise SystemExit("peer worker scripts not found in primary checkout")
    command_env = "PEER_EXECUTOR_COMMAND" if mode == "executor" else "PEER_VALIDATOR_COMMAND"
    logs = Path.home() / ".sinria/logs"
    return {
        "Label": f"ai.sinria.peer-worker.{mode}",
        "ProgramArguments": [str(python), str(worker), "--mode", mode, "--poll-interval", str(poll_interval)],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {
            "COMPANY_OS_BASE_URL": base_url,
            "COMPANY_OS_MEMBER_ID": member_id,
            "COMPANY_OS_INSTANCE_ID": instance_id,
            "COMPANY_OS_TRANSPORT_SUBJECT": subject,
            command_env: f"{python} {command_script}",
            **({"SINRIA_PROFILE": subject} if mode == "executor" and subject.startswith("profile-") else {}),
            "PYTHONUNBUFFERED": "1",
            **({"PEER_NOTIFY_TARGET": notify_target} if mode == "validator" and notify_target else {}),
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(logs / f"peer-worker-{mode}.log"),
        "StandardErrorPath": str(logs / f"peer-worker-{mode}.error.log"),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("executor", "validator"), required=True)
    p.add_argument("--member-id", required=True)
    p.add_argument("--instance-id", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--base-url", default="https://medical-horizon-company-os.vercel.app")
    p.add_argument("--poll-interval", type=int, default=15)
    p.add_argument("--notify-target", default="", help="validator-only Sinria message target")
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--no-load", action="store_true")
    p.add_argument("--preflight", action="store_true")
    a = p.parse_args()
    root = resolve_primary_checkout(a.root)
    plist = build_plist(root=root, mode=a.mode, member_id=a.member_id, instance_id=a.instance_id,
                        subject=a.subject, base_url=a.base_url, poll_interval=a.poll_interval,
                        notify_target=a.notify_target)
    if a.preflight:
        env = os.environ.copy(); env.update(plist["EnvironmentVariables"])
        command = plist["ProgramArguments"][:2] + ["--preflight", "--mode", a.mode]
        result = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, timeout=60)
        print(json.dumps({
            "exit": result.returncode,
            "result": json.loads(result.stdout) if result.stdout else None,
            "error": result.stderr[-500:] if result.stderr else None,
        }))
        raise SystemExit(result.returncode)
    launch_agents = Path.home() / "Library/LaunchAgents"
    logs = Path.home() / ".sinria/logs"
    launch_agents.mkdir(parents=True, exist_ok=True); logs.mkdir(parents=True, exist_ok=True)
    path = launch_agents / f"{plist['Label']}.plist"
    temporary = path.with_suffix(".plist.tmp")
    temporary.write_bytes(plistlib.dumps(plist)); os.chmod(temporary, 0o600); temporary.replace(path)
    if not a.no_load:
        domain = f"gui/{os.getuid()}"  # windows-footgun: ok - LaunchAgent installer is macOS-only
        subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
        subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{plist['Label']}"], check=True)
    print(json.dumps({"installed": True, "label": plist["Label"], "plist": str(path), "root": str(root), "loaded": not a.no_load}))


if __name__ == "__main__":
    main()
