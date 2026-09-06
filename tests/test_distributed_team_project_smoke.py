import json
import subprocess
import sys
from pathlib import Path


def test_distributed_team_project_smoke_completes_with_remote_worker_and_no_raw_context():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "sinria-distributed-team-project-smoke.py")],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "completed"
    assert payload["assignedMemberId"] == "member-kikuchi"
    assert payload["assignedInstanceId"] == "inst-kikuchi"
    assert payload["requestRuns"] == 1
    assert payload["leaseSeconds"] >= 180
    assert payload["rawContextStored"] is False
    assert payload["externalActionPerformed"] is False
