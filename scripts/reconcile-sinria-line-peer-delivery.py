#!/usr/bin/env python3
"""Human-reviewed recovery for indeterminate LINE peer delivery."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.line_peer_routing import LinePeerDeliveryGate  # noqa: E402
from sinria_constants import get_sinria_home  # noqa: E402

_REF = re.compile(r"^sha256:[0-9a-f]{64}$")


def reconcile(
    state_path: str | Path,
    conversation_ref: str,
    message_ref: str,
    decision: str,
    *,
    human_confirmed: bool,
) -> dict:
    if not human_confirmed:
        raise SystemExit("human review confirmation is required")
    if not _REF.fullmatch(conversation_ref) or not _REF.fullmatch(message_ref):
        raise SystemExit("hash-only conversation/message refs are required")
    gate = LinePeerDeliveryGate(state_path)
    try:
        gate.reconcile(
            conversation_ref,
            message_ref,
            decision=decision,
            human_confirmed=True,
        )
    finally:
        gate.close()
    return {
        "ok": True,
        "decision": decision,
        "conversationRef": conversation_ref,
        "messageRef": message_ref,
        "rawContextStored": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-path", type=Path, default=(
        get_sinria_home() / "private" / "line-peer" / "front-door-delivery.sqlite3"
    ))
    parser.add_argument("--conversation-ref", required=True)
    parser.add_argument("--message-ref", required=True)
    parser.add_argument("--decision", choices=("retry", "delivered"), required=True)
    parser.add_argument("--confirm-human-review", action="store_true")
    args = parser.parse_args()
    receipt = reconcile(
        args.state_path,
        args.conversation_ref,
        args.message_ref,
        args.decision,
        human_confirmed=args.confirm_human_review,
    )
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
