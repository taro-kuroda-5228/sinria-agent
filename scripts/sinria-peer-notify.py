#!/usr/bin/env python3
"""Deliver sanitized peer-validator results through Sinria's configured sender."""
from __future__ import annotations

import json
import os
import sys


def build_message(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    run_id = payload.get("runId")
    if status not in {"accepted", "decision_required", "revision_requested"} or not isinstance(run_id, str):
        return None
    return f"菊地Sinriaから報告を受信しました。status: {status} / run: {run_id}"


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
    from tools.send_message_tool import send_message_tool
    result = send_message_tool({"action": "send", "target": target, "message": message})
    parsed = json.loads(result) if isinstance(result, str) else result
    if not isinstance(parsed, dict) or not parsed.get("success"):
        print(json.dumps({"success": False, "error": "notification delivery failed"}), file=sys.stderr)
        return 1
    print(json.dumps({"success": True, "target": target, "messageId": parsed.get("message_id")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
