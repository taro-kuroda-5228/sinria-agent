import json
import subprocess
import sys

import pytest

from agent.company_context.operational_drill import DrillError, STEPS, run_synthetic_operational_drill


def test_complete_drill_is_metadata_only_and_idempotent(tmp_path):
    db = tmp_path / "drill.db"
    result = run_synthetic_operational_drill(db, synthetic=True)
    assert result["status"] == "completed"
    assert result["step_order"] == list(STEPS)
    assert result["external_action_performed"] is False
    assert result["receipt_storage"] == "metadata-only"
    assert run_synthetic_operational_drill(db, synthetic=True)["status"] == "already_completed"
    assert all("raw_context_stored" not in json.dumps(item).lower() or "false" in json.dumps(item).lower() for item in result["steps"])


def test_synthetic_capability_and_fault_stop(tmp_path):
    with pytest.raises(DrillError, match="synthetic capability"):
        run_synthetic_operational_drill(tmp_path / "denied.db")
    with pytest.raises(DrillError, match="fault injected"):
        run_synthetic_operational_drill(tmp_path / "fault.db", synthetic=True, fail_step="canary")


def test_runtime_cli_one_shot_json(tmp_path):
    script = "scripts/company_context_runtime.py"
    proc = subprocess.run(
        [sys.executable, script, "--db", str(tmp_path / "cli.db"), "--profile", "cli", "drill", "--synthetic"],
        text=True, capture_output=True,
    )
    assert proc.returncode == 0
    output = json.loads(proc.stdout)
    assert output["status"] == "completed"
    assert output["step_order"][-1] == "governance_change_control"
