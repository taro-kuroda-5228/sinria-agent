#!/usr/bin/env python3
"""Accept only sanitized synthetic completion receipts."""
import json
import sys


def main() -> int:
    try:
        envelope = json.load(sys.stdin)
        event = envelope.get("event", {})
    except Exception:
        return 2
    forbidden = {"body", "prompt", "rawPrompt", "rawContext", "credentials"}
    if (
        not isinstance(event, dict)
        or event.get("sanitizedPreview") != "Synthetic peer task executed; sanitized completion receipt returned."
        or event.get("bodyRef") is not None
        or forbidden.intersection(event)
    ):
        print(json.dumps({"verdict": "decision_required"}))
        return 0
    print(json.dumps({"verdict": "accepted"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
