#!/usr/bin/env python3
"""Accept only sanitized synthetic completion receipts."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sinria_consultation import validate_consultation


def main() -> int:
    try:
        envelope = json.load(sys.stdin)
        event = envelope.get("event", {})
    except Exception:
        return 2
    forbidden = {"body", "prompt", "rawPrompt", "rawContext", "credentials"}
    preview = event.get("sanitizedPreview") if isinstance(event, dict) else None
    if (
        not isinstance(event, dict)
        or event.get("bodyRef") is not None
        or forbidden.intersection(event)
    ):
        verdict = "decision_required"
    elif event.get("consultationMetadata") is not None:
        try:
            meta = validate_consultation(event["consultationMetadata"])
            verdict = "accepted" if meta and meta["type"] == "consultation_response" else "decision_required"
        except ValueError:
            verdict = "decision_required"
    elif preview == "Synthetic peer task executed; sanitized completion receipt returned.":
        verdict = "accepted"
    elif preview == "Synthetic revision-requested canary":
        verdict = "revision_requested"
    else:
        verdict = "decision_required"
    print(json.dumps({"verdict": verdict}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
