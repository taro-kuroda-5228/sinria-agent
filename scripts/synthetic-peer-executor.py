#!/usr/bin/env python3
"""Deterministic executor for production synthetic peer-collaboration receipts only."""
from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        envelope = json.load(sys.stdin)
    except Exception:
        print(json.dumps({"error": "invalid event envelope"}))
        return 2
    if not isinstance(envelope, dict):
        print(json.dumps({"error": "invalid event envelope"}))
        return 2
    # Peer workers send a metadata-only callback envelope containing the run
    # receipt and event. Direct event input remains supported for local smoke.
    event = envelope.get("event", envelope)
    if not isinstance(event, dict):
        print(json.dumps({"error": "invalid event envelope"}))
        return 2
    preview = event.get("sanitizedPreview")
    forbidden = {"body", "prompt", "rawPrompt", "rawContext"}
    if (
        not isinstance(preview, str)
        or not preview.startswith("Synthetic metadata-only")
        or event.get("bodyRef") is not None
        or forbidden.intersection(event)
    ):
        print(json.dumps({"error": "non-synthetic or non-metadata-only event rejected"}))
        return 3
    print(json.dumps({
        "summary": "Synthetic peer task executed; sanitized completion receipt returned.",
        "refs": [f"run://event/{event.get('eventId', 'unknown')}"],
        "rawContextStored": False,
        "externalActionPerformed": False,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
