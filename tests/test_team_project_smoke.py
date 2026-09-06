import json
import subprocess
import sys
from pathlib import Path


def test_team_project_smoke_completes_after_restart_and_approval(tmp_path):
    root = Path(__file__).resolve().parents[1]
    store = tmp_path / "team-projects.json"
    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "sinria-team-project-smoke.py"), "--store", str(store)],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt == {
        "projectId": "smoke-autonomous-team",
        "status": "completed",
        "workers": ["member-kikuchi", "member-taro"],
        "acceptedTasks": 3,
        "revisionCount": 1,
        "approvalRecorded": True,
        "rawContextStored": False,
        "externalActionPerformed": True,
        "restartVerified": True,
    }
    persisted = json.loads(store.read_text())
    project = persisted["projects"]["smoke-autonomous-team"]
    assert project["status"] == "completed"
    assert project["tasks"]["record"]["approval"]["actor"] == "member-taro"
