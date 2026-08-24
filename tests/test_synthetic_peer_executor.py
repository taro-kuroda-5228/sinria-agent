import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "synthetic-peer-executor.py"


def run(payload):
    return subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload), text=True, capture_output=True)


def test_accepts_only_synthetic_metadata_event():
    result = run({"eventId": "evt_1", "sanitizedPreview": "Synthetic metadata-only task: verify", "bodyRef": None})
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["rawContextStored"] is False
    assert body["externalActionPerformed"] is False
    assert body["refs"] == ["run://event/evt_1"]


def test_rejects_body_ref_and_raw_keys():
    for payload in (
        {"eventId": "evt_1", "sanitizedPreview": "Synthetic metadata-only task: verify", "bodyRef": {"ref": "local://x"}},
        {"eventId": "evt_1", "sanitizedPreview": "Synthetic metadata-only task: verify", "bodyRef": None, "rawContext": "x"},
        {"eventId": "evt_1", "sanitizedPreview": "ordinary task", "bodyRef": None},
    ):
        result = run(payload)
        assert result.returncode == 3
