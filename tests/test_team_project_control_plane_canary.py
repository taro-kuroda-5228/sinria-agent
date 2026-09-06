import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "team-project-control-plane-canary.py"


def request(**overrides):
    value = {
        "schemaVersion": "team-project.v1",
        "type": "task_request",
        "dispatchId": "dispatch-live-canary-1",
        "projectId": "project-live-canary",
        "taskId": "task-runtime-receipt",
        "capability": "control-plane-canary",
        "summary": "Persist a metadata-only local execution receipt.",
        "operation": "write",
        "scope": "local",
        "reversible": True,
        "inputRefs": [],
        "acceptanceCriteria": ["local execution receipt persisted"],
        "attempt": 1,
        "approvalRef": None,
        "rawContextStored": False,
        "externalActionPerformed": False,
    }
    value.update(overrides)
    return value


def invoke(tmp_path, payload):
    env = {**os.environ, "SINRIA_TEAM_EVIDENCE_DIR": str(tmp_path)}
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
        check=False,
    )


def test_canary_executor_persists_idempotent_metadata_only_receipt(tmp_path):
    first = invoke(tmp_path, request())
    second = invoke(tmp_path, request())

    assert first.returncode == 0
    assert second.returncode == 0
    result = json.loads(first.stdout)
    assert result == json.loads(second.stdout)
    assert result["verdict"] == "accepted"
    assert result["externalActionPerformed"] is False
    assert set(result["criteriaEvidence"]) == {"local execution receipt persisted"}
    assert result["evidence"] == [result["criteriaEvidence"]["local execution receipt persisted"]]

    receipts = list(tmp_path.glob("*.json"))
    assert len(receipts) == 1
    stored = json.loads(receipts[0].read_text())
    assert stored == {
        "schemaVersion": "sinria.team-project-receipt.v1",
        "dispatchId": "dispatch-live-canary-1",
        "projectId": "project-live-canary",
        "taskId": "task-runtime-receipt",
        "capability": "control-plane-canary",
        "attempt": 1,
        "criteria": ["local execution receipt persisted"],
        "rawContextStored": False,
        "externalActionPerformed": False,
    }
    serialized = json.dumps(stored).lower()
    assert "rawprompt" not in serialized
    assert "credential" not in serialized
    assert "patientdata" not in serialized


def test_canary_executor_rejects_non_canary_capability(tmp_path):
    completed = invoke(tmp_path, request(capability="research"))

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert list(tmp_path.glob("*.json")) == []
