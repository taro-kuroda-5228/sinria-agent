#!/usr/bin/env python3
"""Deliver sanitized peer-validator results through Sinria's configured sender."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def build_message(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    actions = {
        "accepted": "対応不要です。",
        "decision_required": "内容を確認し、判断または返信してください。",
        "revision_requested": "内容を修正して再送してください。",
    }
    status = payload.get("status")
    run_id = payload.get("runId")
    preview = payload.get("sanitizedPreview")
    if status not in actions or not isinstance(run_id, str) or not isinstance(preview, str) or not preview.strip():
        return None

    def clean(value: object, *, limit: int) -> str:
        text = "".join(char for char in str(value) if char in "\n\t" or ord(char) >= 32)
        return text.strip()[:limit]

    preview = clean(preview, limit=1000)
    if not preview:
        return None
    member = clean(payload.get("authorMemberId") or "unknown", limit=80)
    instance = clean(payload.get("authorInstanceId") or "unknown", limit=80)
    return (
        "菊地Sinriaから報告を受信しました。\n"
        f"内容: {preview}\n"
        f"対応: {actions[status]}\n"
        f"送信元: {member} / {instance}\n"
        f"status: {status} / run: {run_id}"
    )


def main() -> int:
    target = os.environ.get("PEER_NOTIFY_TARGET", "").strip()
    if not target:
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 2
    message = build_message(payload)
    if message is None:
        return 0
    if os.environ.get("PEER_NOTIFY_DRY_RUN") == "1":
        print(json.dumps({"success": True, "target": target, "message": message}, ensure_ascii=False))
        return 0
    configured_cli = os.environ.get("SINRIA_CLI_PATH", "").strip()
    sinria_cli = configured_cli or str(Path.home() / ".local" / "bin" / "sinria")
    if not Path(sinria_cli).is_file():
        sinria_cli = shutil.which("sinria") or ""
    if not sinria_cli:
        print(json.dumps({"success": False, "error": "Sinria CLI is unavailable"}), file=sys.stderr)
        return 1
    completed = subprocess.run(
        [sinria_cli, "send", "--to", target, "--json"],
        input=message,
        text=True,
        capture_output=True,
        timeout=30,
    )
    try:
        parsed = json.loads(completed.stdout)
    except Exception:
        parsed = {}
    if completed.returncode != 0 or not isinstance(parsed, dict) or not parsed.get("success"):
        print(json.dumps({"success": False, "error": "notification delivery failed"}), file=sys.stderr)
        return 1
    print(json.dumps({"success": True, "target": target, "messageId": parsed.get("message_id") or parsed.get("messageId")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
