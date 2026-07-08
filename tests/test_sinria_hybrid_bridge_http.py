import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

from sinria_hybrid_bridge import BridgeDataSensitivity, BridgeTaskEnvelope
from sinria_hybrid_bridge_http import SupabaseRestCloudEventStore


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")

    def json(self):
        return self._payload


def test_supabase_store_fetches_pending_task_with_secret_safe_headers():
    session = Mock()
    session.get.return_value = FakeResponse(
        [
            {
                "id": "task_1",
                "app_id": "chatops_crm",
                "tenant_id": "medical_horizon",
                "requested_by": "kikuchi",
                "task_text": "Draft follow-up",
                "side_effect": "draft",
                "sensitivity": "internal",
                "status": "pending",
            }
        ]
    )
    store = SupabaseRestCloudEventStore("https://example.supabase.co", "secret-token", session=session)

    tasks = store.fetch_pending_tasks(limit=1)

    assert tasks[0].task_id == "task_1"
    headers = session.get.call_args.kwargs["headers"]
    assert headers["apikey"] == "secret-token"
    assert headers["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in repr(store)


def test_supabase_store_maps_cloud_policy_gate_columns_into_task_envelope():
    session = Mock()
    session.get.return_value = FakeResponse(
        [
            {
                "id": "task_policy_1",
                "app_id": "chatops_crm",
                "tenant_id": "medical_horizon",
                "requested_by": "admin_policy",
                "task_text": "Draft follow-up",
                "side_effect": "draft",
                "sensitivity": "internal",
                "status": "pending",
                "allowed_to_run_on_prem": False,
                "autonomous_execution_allowed": False,
                "review_required": True,
                "required_review_role": "compliance",
            }
        ]
    )
    store = SupabaseRestCloudEventStore("https://example.supabase.co", "secret-token", session=session)

    task = store.fetch_pending_tasks(limit=1)[0]

    assert task.allowed_to_run_on_prem is False
    assert task.autonomous_execution_allowed is False
    assert task.review_required is True
    assert task.required_review_role == "compliance"


def test_supabase_store_coerces_string_policy_booleans_without_truthy_false_bug():
    session = Mock()
    session.get.return_value = FakeResponse(
        [
            {
                "id": "task_policy_strings",
                "app_id": "sierra_service",
                "tenant_id": "org-med",
                "requested_by": "patient-hash",
                "task_text": "Classify support request from sanitized metadata",
                "side_effect": "draft",
                "sensitivity": "internal",
                "status": "pending",
                "allowed_to_run_on_prem": "false",
                "autonomous_execution_allowed": "false",
                "review_required": "true",
                "required_review_role": "compliance",
                "external_egress": "false",
                "clinical_context": "true",
            }
        ]
    )
    store = SupabaseRestCloudEventStore("https://example.supabase.co", "secret-token", session=session)

    task = store.fetch_pending_tasks(limit=1)[0]

    assert task.allowed_to_run_on_prem is False
    assert task.autonomous_execution_allowed is False
    assert task.review_required is True
    assert task.required_review_role == "compliance"
    assert task.external_egress is False
    assert task.clinical_context is True


def test_supabase_store_redacts_postgrest_task_text_and_metadata_before_runner():
    session = Mock()
    session.get.return_value = FakeResponse(
        [
            {
                "id": "task_sensitive_summary",
                "app_id": "sierra_service",
                "tenant_id": "org-med",
                "requested_by": "patient-hash",
                "task_text": "Prepare draft for MRN-123456 山田太郎, phone 090-1234-5678, card 4111-1111-1111-1111",
                "side_effect": "draft",
                "sensitivity": "patient",
                "status": "pending",
                "metadata": {
                    "sanitized_summary": "contact taro.patient@example.com and postal 150-0001",
                    "citation_ids": ["SAFE-RESULT-001", "MRN-654321"],
                    "raw_body": "山田花子 raw payload must be dropped",
                },
            }
        ]
    )
    store = SupabaseRestCloudEventStore("https://example.supabase.co", "secret-token", session=session)

    task = store.fetch_pending_tasks(limit=1)[0]
    serialized = f"{task.task_text_summary} {task.metadata}"

    assert "[REDACTED_ID]" in serialized
    assert "[REDACTED_NAME]" in serialized
    assert "[REDACTED_PHONE]" in serialized
    assert "[REDACTED_CARD]" in serialized
    assert "[REDACTED_EMAIL]" in serialized
    assert "[REDACTED_POSTAL]" in serialized
    assert "raw_body" not in task.metadata
    assert "MRN-123456" not in serialized
    assert "MRN-654321" not in serialized
    assert "山田太郎" not in serialized
    assert "山田花子" not in serialized
    assert "090-1234-5678" not in serialized
    assert "4111-1111-1111-1111" not in serialized
    assert "taro.patient@example.com" not in serialized
    assert "150-0001" not in serialized


def test_supabase_store_claim_and_post_result_use_expected_tables():
    session = Mock()
    session.patch.return_value = FakeResponse([{"id": "task_1"}])
    session.post.return_value = FakeResponse([{"id": "row"}])
    store = SupabaseRestCloudEventStore("https://example.supabase.co", "secret-token", session=session)

    store.claim_task("task_1", run_id="run_1", sinria_instance_id="onprem-a", attempt=1)
    store.post_result(run_id="run_1", task_id="task_1", result_text="done", requires_review=False)

    patch_urls = [call.args[0] for call in session.patch.call_args_list]
    post_urls = [call.args[0] for call in session.post.call_args_list]
    assert any(url.endswith("/rest/v1/agent_tasks?id=eq.task_1") for url in patch_urls)
    assert any(url.endswith("/rest/v1/agent_runs") for url in post_urls)
    assert any(url.endswith("/rest/v1/agent_results") for url in post_urls)


def test_worker_once_supabase_routes_review_required_task_to_review_without_result(monkeypatch):
    worker_path = Path("scripts/sinria-hybrid-bridge-worker.py").resolve()
    spec = importlib.util.spec_from_file_location("sinria_hybrid_bridge_worker_for_test", worker_path)
    assert spec is not None
    worker = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(worker)

    class FakeSupabaseStore:
        def __init__(self, url, auth_value):
            self.url = url
            self.auth_value = auth_value
            self.claims = []
            self.results = []
            self.review_requests = []

        def fetch_pending_agent_os_tasks(self, *, workspace_id, member_id, limit=1):
            return [
                {
                    "task_id": "task_review_supabase",
                    "workspace_id": workspace_id,
                    "agent_os_id": "service_agent_os",
                    "task_kind": "service_triage",
                    "requested_by_member_id": "patient-hash",
                    "target_member_id": member_id,
                    "payload": {"summary": "Prepare lab_result_disclosure draft only"},
                    "policy": {"humanApprovalRequired": True},
                    "raw_context_allowed_in_cloud": False,
                }
            ]

        def claim_agent_os_task(self, **kwargs):
            self.claims.append(kwargs)
            return {"claim_id": "claim_task_review_supabase"}

        def post_agent_os_task_result(self, **kwargs):
            self.results.append(kwargs)

    stores = []

    def fake_store_factory(url, auth_value):
        store = FakeSupabaseStore(url, auth_value)
        stores.append(store)
        return store

    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", fake_store_factory)

    outcome = worker._run_once_supabase(
        "https://example.supabase.co",
        "secret-token",
        "onprem-a",
        workspace_id="org-med",
        member_id="patient-hash",
        instance_id="onprem-a",
    )

    assert outcome["outcome"] == "waiting_review"
    assert outcome["task_id"] == "task_review_supabase"
    assert stores[0].claims[0]["task_id"] == "task_review_supabase"
    assert stores[0].claims[0]["member_id"] == "patient-hash"
    assert stores[0].claims[0]["instance_id"] == "onprem-a"
    assert stores[0].results[0]["status"] == "waiting_review"
    assert stores[0].results[0]["human_approval_required"] is True
    assert "secret-token" not in json.dumps(outcome)


def test_worker_once_non_dry_run_uses_mock_processor_and_never_prints_token():
    env = {
        **os.environ,
        "SINRIA_BRIDGE_TOKEN": "super-secret-token",
        "SINRIA_BRIDGE_MOCK_TASK_JSON": json.dumps(
            {
                "id": "task_1",
                "app_id": "chatops_crm",
                "tenant_id": "medical_horizon",
                "requested_by": "kikuchi",
                "task_text": "Draft follow-up",
                "side_effect": "draft",
                "sensitivity": "internal",
                "status": "pending",
            }
        ),
    }
    proc = subprocess.run(
        [sys.executable, "scripts/sinria-hybrid-bridge-worker.py", "--once", "--mock-cloud"],
        cwd=os.getcwd(),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert "super-secret-token" not in proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["outcome"] == "completed"
    assert payload["results"][0]["result_text"].startswith("Sinria mock processed")


def test_worker_dry_run_exposes_team_mode_identity():
    env = {
        **os.environ,
        "SINRIA_WORKSPACE_ID": "medical_horizon",
        "SINRIA_MEMBER_ID": "taro",
        "SINRIA_INSTANCE_ID": "taro-local-sinria",
    }
    proc = subprocess.run(
        [sys.executable, "scripts/sinria-hybrid-bridge-worker.py", "--dry-run"],
        cwd=os.getcwd(),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["identity"]["workspace_id"] == "medical_horizon"
    assert payload["identity"]["member_id"] == "taro"
    assert payload["identity"]["instance_id"] == "taro-local-sinria"
    assert payload["safety"]["credential_stored_in_cloud"] is False
    assert payload["safety"]["raw_context_stored"] is False
    # Agent OS routing surface is advertised (handlers + local adapters), no secrets.
    assert "sales_agent_os:sales_outreach_plan" in payload["agent_os_handlers"]
    assert "sinria_native" in payload["local_execution_adapters"]
    assert "SINRIA_BRIDGE_TOKEN" not in proc.stdout or "false" in proc.stdout.lower()


def test_postgrest_claim_agent_os_task_preserves_identity_and_no_raw_context():
    session = Mock()
    session.post.return_value = FakeResponse([{"claim_id": "aotc_task_1_taro-local_1"}])
    session.patch.return_value = FakeResponse([{"task_id": "task_1"}])
    store = SupabaseRestCloudEventStore("https://example.supabase.co", "secret-token", session=session)

    store.claim_agent_os_task(
        workspace_id="medical_horizon",
        task_id="task_1",
        member_id="taro",
        instance_id="taro-local",
        agent_os_id="sales_agent_os",
        task_kind="sales_outreach_plan",
        target_member_id="taro",
        attempt=1,
        lease_seconds=300,
    )

    post_call = session.post.call_args
    url = post_call.args[0]
    body = post_call.kwargs["json"]
    assert url.endswith("/rest/v1/agent_os_task_claims")
    assert body["claimed_by_member_id"] == "taro"
    assert body["claimed_by_instance_id"] == "taro-local"
    assert body["idempotency_key"] == "claim:task_1:taro:taro-local"
    assert body["raw_local_context_stored"] is False
    assert body["external_action_performed"] is False
    patch_urls = [c.args[0] for c in session.patch.call_args_list]
    assert any("agent_os_tasks?task_id=eq.task_1" in u for u in patch_urls)
    assert "secret-token" not in json.dumps(body)


def test_postgrest_post_agent_os_task_result_is_sanitized_only():
    session = Mock()
    session.post.return_value = FakeResponse([{"result_id": "aor_task_1_taro-local"}])
    session.patch.return_value = FakeResponse([{"task_id": "task_1"}])
    store = SupabaseRestCloudEventStore("https://example.supabase.co", "secret-token", session=session)

    store.post_agent_os_task_result(
        workspace_id="medical_horizon",
        task_id="task_1",
        agent_os_id="sales_agent_os",
        task_kind="sales_outreach_plan",
        member_id="taro",
        instance_id="taro-local",
        status="waiting_review",
        sanitized_summary="候補10件・下書き7件を作成（外部送信なし）",
        result_refs=[{"kind": "draft", "refId": "d1", "title": "x"}],
    )

    body = session.post.call_args.kwargs["json"]
    assert body["raw_result_body_stored"] is False
    assert body["credential_stored_in_cloud"] is False
    assert body["external_action_performed"] is False
    assert body["sanitized_summary"]
    post_url = session.post.call_args.args[0]
    assert post_url.endswith("/rest/v1/agent_os_task_results")
