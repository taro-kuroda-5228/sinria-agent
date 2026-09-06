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


def validate_metadata(metadata):
    payload = {
        "run": {"runId": "run_1"},
        "event": {
            "eventId": "evt_1",
            "sanitizedPreview": "Team project task completed.",
            "consultationMetadata": metadata,
            "bodyRef": None,
        },
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    return json.loads(result.stdout)["verdict"]


def test_validator_supports_accept_revision_and_decision_required():
    assert validate("Synthetic peer task executed; sanitized completion receipt returned.") == "accepted"
    assert validate("Synthetic revision-requested canary") == "revision_requested"
    assert validate("Synthetic decision-required canary") == "decision_required"


def test_validator_uses_the_team_project_response_verdict():
    metadata = {
        "schemaVersion": "team-project.v1",
        "type": "task_response",
        "dispatchId": "dispatch-1",
        "projectId": "project-1",
        "taskId": "research",
        "status": "completed",
        "summary": "Research completed",
        "evidence": ["company-knowledge://projects/project-1/research"],
        "criteriaEvidence": {
            "facts-grounded": "company-knowledge://projects/project-1/research"
        },
        "verdict": "accepted",
        "rawContextStored": False,
        "externalActionPerformed": False,
    }

    assert validate_metadata(metadata) == "accepted"
