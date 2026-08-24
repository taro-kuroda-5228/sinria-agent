import json
from pathlib import Path

import pytest

from sinria_hybrid_bridge import (
    BridgeDataSensitivity,
    BridgeSideEffect,
    BridgeTaskEnvelope,
    BridgeTaskStatus,
    BridgeTransport,
    mvp_table_specs,
    phase_plan,
    plan_task,
    worker_contract,
)


def _cloud_schema_or_skip() -> str:
    path = Path(__file__).resolve().parents[1] / "docs" / "sinria-hybrid-bridge-cloud-schema.sql"
    if not path.exists():
        pytest.skip("Hybrid Bridge cloud schema overlay is not included in this distribution")
    return path.read_text()


def test_phase_plan_covers_all_five_phases():
    phases = phase_plan()

    assert [phase.phase for phase in phases] == [1, 2, 3, 4, 5]
    assert "outbound polling" in phases[0].title.lower()
    assert "k3s" in phases[4].title.lower() or "kubernetes" in phases[4].title.lower()


def test_mvp_schema_has_core_cloud_tables_and_boundaries():
    tables = {table.name: table for table in mvp_table_specs()}

    assert {"agent_tasks", "agent_runs", "agent_results", "review_requests", "improvement_candidates"}.issubset(tables)
    assert "no private vault" in tables["agent_tasks"].cloud_data_boundary.lower() or "detailed context" in tables["agent_tasks"].cloud_data_boundary.lower()
    assert "secret" in tables["chat_messages"].cloud_data_boundary.lower()


def test_mvp_schema_connects_openclaw_crm_concepts_to_sinria_bridge():
    tables = {table.name: table for table in mvp_table_specs()}

    assert {
        "companies",
        "contacts",
        "leads",
        "campaigns",
        "outreach_drafts",
        "outreach_jobs",
        "interactions",
        "agent_notes",
        "audit_logs",
        "agent_tasks",
        "review_requests",
    }.issubset(tables)
    assert "agent_task_id" in tables["outreach_jobs"].columns
    assert "review_request_id" in tables["outreach_drafts"].columns
    assert "sanitized" in tables["agent_notes"].cloud_data_boundary.lower()
    assert "external_action_performed" in tables["audit_logs"].columns
    assert "no raw" in tables["audit_logs"].cloud_data_boundary.lower()
    assert "external action" in tables["audit_logs"].cloud_data_boundary.lower()


def test_cloud_schema_limits_agent_tasks_to_registered_agentos_apps():
    schema = _cloud_schema_or_skip()

    assert "check (app_id in ('chatops_crm', 'sierra_service', 'consent_agent'))" in schema


def test_cloud_schema_audit_logs_record_no_external_action_boundary():
    schema = _cloud_schema_or_skip()

    assert "external_action_performed boolean not null default false" in schema
    assert "check (external_action_performed = false)" in schema


def test_cloud_schema_improvement_candidates_are_draft_only_sanitized_metadata():
    tables = {table.name: table for table in mvp_table_specs()}
    schema = _cloud_schema_or_skip()

    assert "external_action_performed" in tables["improvement_candidates"].columns
    assert "human_approval_required" in tables["improvement_candidates"].columns
    assert "external_action_performed boolean not null default false" in schema
    assert "check (external_action_performed = false)" in schema


def test_low_risk_draft_task_can_run_autonomously_on_prem():
    decision = plan_task(
        BridgeTaskEnvelope(
            task_id="task_1",
            app_id="chatops_crm",
            tenant_id="medical_horizon",
            requested_by="kikuchi",
            task_text_summary="Draft a sanitized lead follow-up",
            side_effect=BridgeSideEffect.DRAFT,
            sensitivity=BridgeDataSensitivity.INTERNAL,
        )
    )

    assert decision.allowed_to_run_on_prem is True
    assert decision.autonomous_execution_allowed is True
    assert decision.review_required is False
    assert decision.next_status == BridgeTaskStatus.RUNNING


def test_sierra_service_bridge_task_is_accepted_but_patient_metadata_is_review_gated():
    decision = plan_task(
        BridgeTaskEnvelope(
            task_id="task_sierra_1",
            app_id="sierra_service",
            tenant_id="hospital_a",
            requested_by="patient_portal",
            task_text_summary="Prepare lab result disclosure draft from sanitized metadata",
            side_effect=BridgeSideEffect.DRAFT,
            sensitivity=BridgeDataSensitivity.PATIENT,
            clinical_context=True,
            metadata={"citation_ids": ["SAFE-RESULT-001"], "externalActionPerformed": False},
        )
    )

    assert decision.allowed_to_run_on_prem is True
    assert decision.autonomous_execution_allowed is False
    assert decision.review_required is True
    assert decision.required_review_role == "physician"
    assert decision.next_status == BridgeTaskStatus.WAITING_REVIEW


def test_unknown_cloud_app_id_is_recoverably_blocked_before_on_prem_execution():
    decision = plan_task(
        BridgeTaskEnvelope(
            task_id="task_unknown_1",
            app_id="unknown_cloud_app",
            tenant_id="medical_horizon",
            requested_by="unknown_user",
            task_text_summary="Run arbitrary task from unregistered app",
            side_effect=BridgeSideEffect.DRAFT,
            sensitivity=BridgeDataSensitivity.INTERNAL,
        )
    )

    assert decision.allowed_to_run_on_prem is False
    assert decision.autonomous_execution_allowed is False
    assert decision.review_required is True
    assert decision.required_review_role == "admin"
    assert decision.next_status == BridgeTaskStatus.FAILED_RECOVERABLE
    assert "unknown app_id" in decision.reason


def test_cloud_policy_can_disable_on_prem_execution_for_registered_app():
    decision = plan_task(
        BridgeTaskEnvelope(
            task_id="task_disabled_by_admin",
            app_id="chatops_crm",
            tenant_id="medical_horizon",
            requested_by="admin_policy",
            task_text_summary="Draft follow-up from a temporarily disabled cloud app",
            side_effect=BridgeSideEffect.DRAFT,
            sensitivity=BridgeDataSensitivity.INTERNAL,
            allowed_to_run_on_prem=False,
            autonomous_execution_allowed=False,
            review_required=True,
            required_review_role="admin",
        )
    )

    assert decision.allowed_to_run_on_prem is False
    assert decision.autonomous_execution_allowed is False
    assert decision.review_required is True
    assert decision.required_review_role == "admin"
    assert decision.next_status == BridgeTaskStatus.FAILED_RECOVERABLE
    assert "cloud policy" in decision.reason


def test_cloud_policy_review_override_blocks_autonomous_low_risk_execution():
    decision = plan_task(
        BridgeTaskEnvelope(
            task_id="task_review_by_policy",
            app_id="chatops_crm",
            tenant_id="medical_horizon",
            requested_by="admin_policy",
            task_text_summary="Draft follow-up that cloud policy requires to be reviewed",
            side_effect=BridgeSideEffect.DRAFT,
            sensitivity=BridgeDataSensitivity.INTERNAL,
            allowed_to_run_on_prem=True,
            autonomous_execution_allowed=False,
            review_required=True,
            required_review_role="compliance",
        )
    )

    assert decision.allowed_to_run_on_prem is True
    assert decision.autonomous_execution_allowed is False
    assert decision.review_required is True
    assert decision.required_review_role == "compliance"
    assert decision.next_status == BridgeTaskStatus.WAITING_REVIEW
    assert "cloud policy" in decision.reason


def test_send_or_confidential_task_requires_admin_review():
    decision = plan_task(
        BridgeTaskEnvelope(
            task_id="task_2",
            app_id="chatops_crm",
            tenant_id="medical_horizon",
            requested_by="kikuchi",
            task_text_summary="Send a proposal email",
            side_effect=BridgeSideEffect.SEND,
            sensitivity=BridgeDataSensitivity.CONFIDENTIAL,
            external_egress=True,
        )
    )

    assert decision.autonomous_execution_allowed is False
    assert decision.review_required is True
    assert decision.required_review_role == "admin"
    assert decision.next_status == BridgeTaskStatus.WAITING_REVIEW
    assert "external_egress" in decision.reason


def test_clinical_or_patient_task_requires_physician_review():
    decision = plan_task(
        BridgeTaskEnvelope(
            task_id="task_3",
            app_id="consent_agent",
            tenant_id="hospital_a",
            requested_by="doctor_1",
            task_text_summary="Draft clinical explanation",
            side_effect=BridgeSideEffect.DRAFT,
            sensitivity=BridgeDataSensitivity.PATIENT,
            clinical_context=True,
        )
    )

    assert decision.review_required is True
    assert decision.required_review_role == "physician"
    assert "clinical_context" in decision.reason


def test_worker_contract_is_outbound_only_and_secret_safe():
    contract = worker_contract(BridgeTransport.POLLING)

    assert contract["direction"] == "on_prem_to_cloud_outbound_only"
    assert contract["no_inbound_ports_required"] is True
    assert "never be logged" in contract["secret_handling"]
    json.dumps(contract)
