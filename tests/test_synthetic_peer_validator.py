import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "synthetic-peer-validator.py"


def validate(preview):
    payload = {"run": {"runId": "run_1"}, "event": {"eventId": "evt_1", "sanitizedPreview": preview, "bodyRef": None}}
    result = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload), text=True, capture_output=True)
    assert result.returncode == 0
    return json.loads(result.stdout)["verdict"]


def test_validator_supports_accept_revision_and_decision_required():
    assert validate("Synthetic peer task executed; sanitized completion receipt returned.") == "accepted"
    assert validate("Synthetic revision-requested canary") == "revision_requested"
    assert validate("Synthetic decision-required canary") == "decision_required"
