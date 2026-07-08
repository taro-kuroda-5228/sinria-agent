"""Keeps the Agent OS Team Mode local-routing smoke green in CI (Task 20)."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "smoke_team_mode_local_routing.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("smoke_team_mode_local_routing", SMOKE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mock_routing_rejects_wrong_member_and_accepts_target():
    smoke = _load_smoke()
    outcome = smoke._run_mock()
    assert outcome["ok"] is True
    assert outcome["wrong_member_claim_rejected"] is True
    assert outcome["right_member_claimed"] is True
    assert outcome["claimed_by_instance_id"] == "taro-local-sinria"
    assert outcome["external_action_performed"] is False
    assert outcome["raw_context_stored"] is False


def test_mock_router_target_member_enforcement_is_explicit():
    smoke = _load_smoke()
    router = smoke.MockTeamModeRouter()
    task = router.create_task(
        workspace_id="medical_horizon",
        agent_os_id="sales_agent_os",
        task_kind="sales_outreach_plan",
        instruction="x",
        requested_by="taro",
        target_member="taro",
        target_instance="taro-local-sinria",
    )
    # Wrong instance for the right member is also rejected.
    wrong_instance = router.claim_task(
        task_id=task["id"], member_id="taro", instance_id="someone-elses-laptop"
    )
    assert wrong_instance["ok"] is False
    # Correct member + instance succeeds and is idempotent on retry.
    first = router.claim_task(task_id=task["id"], member_id="taro", instance_id="taro-local-sinria")
    second = router.claim_task(task_id=task["id"], member_id="taro", instance_id="taro-local-sinria")
    assert first["ok"] is True
    assert second["ok"] is True
    assert first["claim"]["id"] == second["claim"]["id"]
