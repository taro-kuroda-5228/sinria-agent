from sinria_hybrid_bridge import BridgeSideEffect, BridgeTaskEnvelope
from sinria_hybrid_bridge_transports import (
    InMemoryCloudEventStore,
    PollingBridgeRunner,
    bridge_task_from_postgrest_row,
    postgrest_patch_for_status,
)


def test_in_memory_store_claims_pending_task_once_with_idempotency_key():
    store = InMemoryCloudEventStore()
    task = BridgeTaskEnvelope(
        task_id="task_1",
        app_id="chatops_crm",
        tenant_id="medical_horizon",
        requested_by="kikuchi",
        task_text_summary="Draft follow-up",
    )
    store.add_task(task)

    claimed = store.claim_next_pending(sinria_instance_id="onprem-a")
    duplicate = store.claim_next_pending(sinria_instance_id="onprem-a")

    assert claimed is not None
    assert claimed.idempotency_key == "task_1:1:onprem-a"
    assert duplicate is None
    assert store.runs[0].status == "claimed"


def test_runner_posts_result_for_low_risk_task():
    store = InMemoryCloudEventStore()
    store.add_task(
        BridgeTaskEnvelope(
            task_id="task_2",
            app_id="chatops_crm",
            tenant_id="medical_horizon",
            requested_by="kikuchi",
            task_text_summary="Summarize lead",
        )
    )
    runner = PollingBridgeRunner(store=store, sinria_instance_id="onprem-a")

    outcome = runner.run_once(lambda task: f"processed:{task.task_id}")

    assert outcome == "completed"
    assert store.results[0].result_text == "processed:task_2"
    assert store.tasks["task_2"].status.value == "completed"


def test_runner_creates_review_request_for_send_task_without_running_processor():
    store = InMemoryCloudEventStore()
    store.add_task(
        BridgeTaskEnvelope(
            task_id="task_3",
            app_id="chatops_crm",
            tenant_id="medical_horizon",
            requested_by="kikuchi",
            task_text_summary="Send proposal",
            side_effect=BridgeSideEffect.SEND,
            external_egress=True,
        )
    )
    runner = PollingBridgeRunner(store=store, sinria_instance_id="onprem-a")

    outcome = runner.run_once(lambda task: "should-not-run")

    assert outcome == "waiting_review"
    assert store.results == []
    assert store.review_requests[0].task_id == "task_3"
    assert store.review_requests[0].required_role == "admin"


def test_runner_blocks_unknown_app_before_review_or_processor():
    store = InMemoryCloudEventStore()
    store.add_task(
        BridgeTaskEnvelope(
            task_id="task_unknown",
            app_id="unknown_cloud_app",
            tenant_id="medical_horizon",
            requested_by="untrusted-cloud-row",
            task_text_summary="Run arbitrary unregistered app task",
        )
    )
    runner = PollingBridgeRunner(store=store, sinria_instance_id="onprem-a")

    outcome = runner.run_once(lambda task: "should-not-run")

    assert outcome == "failed_recoverable"
    assert store.tasks["task_unknown"].status.value == "failed_recoverable"
    assert store.results == []
    assert store.review_requests == []
    assert store.runs[0].status == "failed_recoverable"
    assert store.runs[0].error_summary is not None
    assert "unknown app_id" in store.runs[0].error_summary


def test_runner_handles_cancel_requested_before_processing():
    store = InMemoryCloudEventStore()
    store.add_task(
        BridgeTaskEnvelope(
            task_id="task_4",
            app_id="chatops_crm",
            tenant_id="medical_horizon",
            requested_by="taro",
            task_text_summary="Cancelled task",
        )
    )
    store.request_cancel("task_4")
    runner = PollingBridgeRunner(store=store, sinria_instance_id="onprem-a")

    outcome = runner.run_once(lambda task: "should-not-run")

    assert outcome == "cancelled"
    assert store.tasks["task_4"].status.value == "cancelled"


def test_postgrest_agent_task_row_maps_policy_overrides_without_raw_payload():
    task = bridge_task_from_postgrest_row(
        {
            "id": "task_pg_1",
            "app_id": "sierra_service",
            "tenant_id": "org-med",
            "requested_by": "patient-hash-1",
            "task_text": "Classify lab_result_disclosure and prepare scoped draft only",
            "side_effect": "draft",
            "sensitivity": "patient",
            "status": "pending",
            "allowed_to_run_on_prem": True,
            "autonomous_execution_allowed": False,
            "review_required": True,
            "required_review_role": "physician",
            "external_egress": True,
            "metadata": {"citation_ids": ["SAFE-RESULT-001"], "raw_body": "MRN-123456 山田太郎"},
        }
    )

    assert task.task_id == "task_pg_1"
    assert task.app_id == "sierra_service"
    assert task.side_effect.value == "draft"
    assert task.sensitivity.value == "patient"
    assert task.allowed_to_run_on_prem is True
    assert task.autonomous_execution_allowed is False
    assert task.review_required is True
    assert task.required_review_role == "physician"
    assert task.external_egress is True
    assert task.metadata == {"citation_ids": ["SAFE-RESULT-001"]}


def test_postgrest_agent_task_row_redacts_task_text_summary_before_local_runner():
    task = bridge_task_from_postgrest_row(
        {
            "id": "task_pg_text_redact",
            "app_id": "sierra_service",
            "tenant_id": "org-med",
            "requested_by": "patient-hash-3",
            "task_text": "Prepare approval draft for MRN-123456 山田太郎 with card 4111-1111-1111-1111",
            "side_effect": "draft",
            "sensitivity": "patient",
            "status": "pending",
        }
    )

    assert "[REDACTED_ID]" in task.task_text_summary
    assert "[REDACTED_NAME]" in task.task_text_summary
    assert "[REDACTED_CARD]" in task.task_text_summary
    assert "MRN-123456" not in task.task_text_summary
    assert "山田太郎" not in task.task_text_summary
    assert "4111-1111-1111-1111" not in task.task_text_summary


def test_postgrest_agent_task_row_redacts_allowlisted_metadata_values():
    task = bridge_task_from_postgrest_row(
        {
            "id": "task_pg_redact",
            "app_id": "sierra_service",
            "tenant_id": "org-med",
            "requested_by": "patient-hash-2",
            "task_text": "Prepare sanitized approval metadata",
            "side_effect": "draft",
            "sensitivity": "patient",
            "status": "pending",
            "metadata": {
                "sanitized_summary": "Patient MRN-123456 山田太郎 needs review; card 4111-1111-1111-1111",
                "workflow": "family_proxy_disclosure MRN-654321",
                "citation_ids": ["SAFE-PROXY-001", "MRN-777888"],
                "raw_body": "MRN-999999 山田花子 must be dropped entirely",
            },
        }
    )

    serialized = str(task.metadata)
    assert task.metadata["sanitized_summary"].startswith("Patient [REDACTED_ID]")
    assert task.metadata["citation_ids"] == ["SAFE-PROXY-001", "[REDACTED_ID]"]
    assert "raw_body" not in task.metadata
    assert "[REDACTED" in serialized
    assert "MRN-123456" not in serialized
    assert "MRN-654321" not in serialized
    assert "MRN-777888" not in serialized
    assert "MRN-999999" not in serialized
    assert "山田太郎" not in serialized
    assert "山田花子" not in serialized
    assert "4111-1111-1111-1111" not in serialized


def test_postgrest_agent_task_row_redacts_contact_identifiers_in_task_text_and_metadata():
    task = bridge_task_from_postgrest_row(
        {
            "id": "task_pg_contact_redact",
            "app_id": "sierra_service",
            "tenant_id": "org-med",
            "requested_by": "patient-hash-4",
            "task_text": "Update contact for patient email taro.patient@example.com and phone 090-1234-5678",
            "side_effect": "draft",
            "sensitivity": "patient",
            "status": "pending",
            "metadata": {
                "sanitized_summary": "contact_update email taro.patient@example.com phone 03-1234-5678 postal 150-0001",
                "workflow": {"handoff": "call +81-90-1234-5678 before sending"},
            },
        }
    )

    serialized = f"{task.task_text_summary} {task.metadata}"
    assert "[REDACTED_EMAIL]" in serialized
    assert "[REDACTED_PHONE]" in serialized
    assert "[REDACTED_POSTAL]" in serialized
    assert "taro.patient@example.com" not in serialized
    assert "090-1234-5678" not in serialized
    assert "03-1234-5678" not in serialized
    assert "+81-90-1234-5678" not in serialized
    assert "150-0001" not in serialized


def test_postgrest_agent_task_row_coerces_stringified_policy_booleans_safely():
    task = bridge_task_from_postgrest_row(
        {
            "id": "task_pg_bool",
            "app_id": "chatops_crm",
            "tenant_id": "medical_horizon",
            "requested_by": "operator-hash-1",
            "task_text": "Low-looking task explicitly denied by cloud policy gate",
            "side_effect": "read",
            "sensitivity": "internal",
            "status": "pending",
            "allowed_to_run_on_prem": "false",
            "autonomous_execution_allowed": "0",
            "review_required": "yes",
            "required_review_role": "admin",
            "external_egress": "no",
            "clinical_context": "true",
        }
    )

    assert task.allowed_to_run_on_prem is False
    assert task.autonomous_execution_allowed is False
    assert task.review_required is True
    assert task.external_egress is False
    assert task.clinical_context is True


def test_postgrest_status_patch_serializes_bridge_status_without_sensitive_fields():
    patch = postgrest_patch_for_status(
        status="failed_recoverable",
        error_summary="unknown app_id='rogue_app'; register the cloud app before on-prem execution",
    )

    assert patch == {
        "status": "failed_recoverable",
        "error_summary": "unknown app_id='rogue_app'; register the cloud app before on-prem execution",
    }
    assert "task_text" not in patch
    assert "result_text" not in patch


def test_runner_redacts_processor_exception_before_cloud_visible_recoverable_failure():
    store = InMemoryCloudEventStore()
    store.add_task(
        BridgeTaskEnvelope(
            task_id="task_exception_redact",
            app_id="chatops_crm",
            tenant_id="medical_horizon",
            requested_by="operator-hash-2",
            task_text_summary="Draft sanitized internal summary",
        )
    )
    runner = PollingBridgeRunner(store=store, sinria_instance_id="onprem-a")

    def processor(_task):
        raise RuntimeError(
            "Tool failed while handling MRN-123456 山田太郎, email taro.patient@example.com, "
            "phone 090-1234-5678, card 4111-1111-1111-1111"
        )

    outcome = runner.run_once(processor)

    assert outcome == "failed_recoverable"
    assert store.results == []
    assert store.runs[0].error_summary is not None
    assert "[REDACTED_ID]" in store.runs[0].error_summary
    assert "[REDACTED_NAME]" in store.runs[0].error_summary
    assert "[REDACTED_EMAIL]" in store.runs[0].error_summary
    assert "[REDACTED_PHONE]" in store.runs[0].error_summary
    assert "[REDACTED_CARD]" in store.runs[0].error_summary
    assert "MRN-123456" not in store.runs[0].error_summary
    assert "山田太郎" not in store.runs[0].error_summary
    assert "taro.patient@example.com" not in store.runs[0].error_summary
    assert "090-1234-5678" not in store.runs[0].error_summary
    assert "4111-1111-1111-1111" not in store.runs[0].error_summary
