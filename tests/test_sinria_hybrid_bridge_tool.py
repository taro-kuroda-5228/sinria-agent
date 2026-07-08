import json
import os
import subprocess
import sys

from tools.sinria_hybrid_bridge_tool import sinria_hybrid_bridge


def test_tool_phase_plan_returns_json_and_no_network_note():
    result = json.loads(sinria_hybrid_bridge("phase_plan"))

    assert result["success"] is True
    assert len(result["phases"]) == 5
    assert "no cloud service" in result["safety_note"]


def test_tool_mvp_schema_includes_review_and_improvement_tables():
    result = json.loads(sinria_hybrid_bridge("mvp_schema"))
    table_names = {table["name"] for table in result["tables"]}

    assert {"review_requests", "improvement_candidates"}.issubset(table_names)
    assert "secret" in result["safety_note"].lower()


def test_tool_plan_task_blocks_send_until_review():
    result = json.loads(
        sinria_hybrid_bridge(
            "plan_task",
            task_id="task_send",
            requested_by="kikuchi",
            task_text_summary="send CRM email",
            side_effect="send",
            sensitivity="confidential",
            external_egress=True,
        )
    )

    assert result["success"] is True
    assert result["decision"]["autonomous_execution_allowed"] is False
    assert result["decision"]["review_required"] is True
    assert result["decision"]["required_review_role"] == "admin"


def test_tool_rejects_invalid_mode():
    result = json.loads(sinria_hybrid_bridge("unknown"))

    assert result["success"] is False
    assert "mode must be" in result["error"]


def test_tool_review_decision_and_improvement_modes():
    review = json.loads(
        sinria_hybrid_bridge(
            "review_decision",
            review_id="rev_1",
            task_id="task_1",
            required_role="admin",
            approved=True,
            decided_by="taro",
            role="admin",
        )
    )
    improvement = json.loads(
        sinria_hybrid_bridge(
            "propose_improvement",
            tenant_id="medical_horizon",
            source_run_id="run_1",
            signal="repeated_safe_block",
            task_text_summary="Sanitized CRM draft was over-blocked.",
        )
    )

    assert review["outcome"]["execution_allowed"] is True
    assert improvement["candidate"]["kind"] == "policy_change"


def test_dry_run_worker_does_not_print_token_value(tmp_path):
    env = {**os.environ, "SINRIA_BRIDGE_TOKEN": "super-secret-token"}
    proc = subprocess.run(
        [sys.executable, "scripts/sinria-hybrid-bridge-worker.py", "--dry-run"],
        cwd=os.getcwd(),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert "super-secret-token" not in proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["required_secret_env_present"]["SINRIA_BRIDGE_TOKEN"] is True
    assert payload["no_inbound_ports_required"] is True
