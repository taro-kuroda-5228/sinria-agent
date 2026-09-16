import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

from sinria_hybrid_bridge import BridgeDataSensitivity, BridgeTaskEnvelope
from sinria_hybrid_bridge_http import CompanyOsApiCloudEventStore, SupabaseRestCloudEventStore


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


def test_company_os_api_store_treats_approved_task_status_as_durable_approval():
    session = Mock()
    session.get.return_value = FakeResponse(
        {
            "ok": True,
            "tasks": [
                {
                    "id": "task-approved",
                    "workspaceId": "medical_horizon",
                    "targetMemberId": "taro",
                    "status": "approved_for_execution",
                }
            ],
            "claims": [],
            "results": [],
        }
    )
    store = CompanyOsApiCloudEventStore("https://company-os.example", "token", session=session)

    tasks = store.fetch_pending_agent_os_tasks(
        workspace_id="medical_horizon",
        member_id="taro",
        instance_id="taro-local-sinria",
    )

    assert [task["id"] for task in tasks] == ["task-approved"]
    assert store.has_approved_agent_os_review(
        workspace_id="medical_horizon",
        task_id="task-approved",
    ) is True


def test_company_os_api_store_never_reuses_approval_across_workspaces():
    session = Mock()
    session.get.return_value = FakeResponse(
        {
            "ok": True,
            "tasks": [
                {
                    "id": "shared-task",
                    "workspaceId": "workspace-a",
                    "targetMemberId": "taro",
                    "status": "approved_for_execution",
                },
                {
                    "id": "shared-task",
                    "workspaceId": "workspace-b",
                    "targetMemberId": "taro",
                    "status": "queued",
                },
            ],
            "claims": [],
            "results": [
                {
                    "taskId": "shared-result-task",
                    "workspaceId": "workspace-a",
                    "safety": {"humanApprovalRequired": False},
                }
            ],
        }
    )
    store = CompanyOsApiCloudEventStore("https://company-os.example", "token", session=session)
    store.fetch_pending_agent_os_tasks(
        workspace_id="workspace-b",
        member_id="taro",
        instance_id="taro-local-sinria",
    )

    assert store.has_approved_agent_os_review(
        workspace_id="workspace-b", task_id="shared-task"
    ) is False
    assert store.has_approved_agent_os_review(
        workspace_id="workspace-b", task_id="shared-result-task"
    ) is False


def test_company_os_api_store_rejects_unscoped_tasks_and_claim_attempts():
    session = Mock()
    session.get.return_value = FakeResponse(
        {
            "ok": True,
            "tasks": [
                {
                    "id": "unscoped-task",
                    "targetMemberId": "taro",
                    "status": "queued",
                }
            ],
            "claims": [
                {"taskId": "shared-task", "workspaceId": "workspace-a", "attempt": 8},
                {"taskId": "shared-task", "workspaceId": "workspace-b", "attempt": 2},
                {"taskId": "shared-task", "attempt": 99},
            ],
            "results": [],
        }
    )
    store = CompanyOsApiCloudEventStore("https://company-os.example", "token", session=session)

    tasks = store.fetch_pending_agent_os_tasks(workspace_id="workspace-b", member_id="taro")

    assert tasks == []
    assert store.next_agent_os_task_attempt(workspace_id="workspace-b", task_id="shared-task") == 3


def test_company_os_api_store_renews_the_claim_returned_by_claim_route():
    session = Mock()
    session.post.side_effect = [
        FakeResponse({"ok": True, "claim": {"claimId": "claim-1", "workspaceId": "medical_horizon", "taskId": "task-1", "attempt": 1}}),
        FakeResponse({"ok": True, "claim": {"claimId": "claim-1", "workspaceId": "medical_horizon", "taskId": "task-1", "attempt": 1}}),
    ]
    store = CompanyOsApiCloudEventStore("https://company-os.example", "token", session=session)
    store.claim_agent_os_task(
        workspace_id="medical_horizon",
        task_id="task-1",
        member_id="taro",
        instance_id="taro-local-sinria",
        selected_execution_engine="sinria_native",
    )

    renewed = store.renew_agent_os_task_claim_lease(
        workspace_id="medical_horizon",
        task_id="task-1",
        member_id="taro",
        instance_id="taro-local-sinria",
        attempt=1,
        lease_seconds=420,
    )

    assert renewed["claimId"] == "claim-1"
    renew_call = session.post.call_args_list[1]
    assert renew_call.args[0] == "https://company-os.example/api/agent-os/tasks/claim/renew"
    assert renew_call.kwargs["json"] == {
        "workspaceId": "medical_horizon",
        "claimId": "claim-1",
        "memberId": "taro",
        "instanceId": "taro-local-sinria",
        "leaseSeconds": 420,
    }


def test_company_os_api_store_renews_persisted_claim_id_after_worker_restart():
    session = Mock()
    session.post.return_value = FakeResponse(
        {"ok": True, "claim": {"claimId": "claim-persisted", "workspaceId": "medical_horizon", "taskId": "task-1", "attempt": 2}}
    )
    store = CompanyOsApiCloudEventStore("https://company-os.example", "token", session=session)

    renewed = store.renew_agent_os_task_claim_lease(
        workspace_id="medical_horizon",
        task_id="task-1",
        claim_id="claim-persisted",
        member_id="taro",
        instance_id="taro-local-sinria",
        attempt=2,
    )

    assert renewed is not None
    assert renewed["claimId"] == "claim-persisted"
    assert session.post.call_args.kwargs["json"]["claimId"] == "claim-persisted"


def test_company_os_api_store_never_reuses_claim_id_across_workspaces():
    session = Mock()
    session.post.side_effect = [
        FakeResponse({"ok": True, "claim": {"claimId": "claim-a", "workspaceId": "workspace-a", "taskId": "shared-task", "attempt": 1}}),
    ]
    store = CompanyOsApiCloudEventStore("https://company-os.example", "token", session=session)
    store.claim_agent_os_task(
        workspace_id="workspace-a",
        task_id="shared-task",
        member_id="taro",
        instance_id="taro-local-sinria",
    )

    renewed = store.renew_agent_os_task_claim_lease(
        workspace_id="workspace-b",
        task_id="shared-task",
        member_id="taro",
        instance_id="taro-local-sinria",
        attempt=1,
    )

    assert renewed is None
    assert session.post.call_count == 1


def test_company_os_api_store_uses_canonical_task_routes_and_durable_approval():
    session = Mock()
    session.get.return_value = FakeResponse(
        {
            "ok": True,
            "tasks": [
                {
                    "id": "task-approved",
                    "workspaceId": "medical-horizon",
                    "agentOsId": "company_os",
                    "taskKind": "inbox_request",
                    "targetMemberId": "member_taro",
                    "targetInstanceId": "inst_taro_macbook",
                    "status": "failed_recoverable",
                    "policy": {"humanApprovalRequired": True},
                },
                {"id": "task-complete", "targetMemberId": "member_taro", "status": "completed"},
            ],
            "claims": [{"taskId": "task-approved", "workspaceId": "medical-horizon", "attempt": 2}],
            "results": [
                {
                    "taskId": "task-approved",
                    "workspaceId": "medical-horizon",
                    "status": "failed_recoverable",
                    "safety": {"humanApprovalRequired": False},
                }
            ],
        }
    )
    store = CompanyOsApiCloudEventStore(
        "https://medical-horizon-company-os.vercel.app/",
        "bridge-secret",
        session=session,
    )

    tasks = store.fetch_pending_agent_os_tasks(
        workspace_id="medical-horizon", member_id="member_taro", limit=5
    )

    assert [task["id"] for task in tasks] == ["task-approved"]
    session.get.assert_called_once_with(
        "https://medical-horizon-company-os.vercel.app/api/agent-os/tasks",
        headers={"Authorization": "Bearer bridge-secret", "Content-Type": "application/json"},
        params={"targetMemberId": "member_taro"},
        timeout=20.0,
    )
    assert store.has_approved_agent_os_review(
        workspace_id="medical-horizon", task_id="task-approved"
    ) is True
    assert store.next_agent_os_task_attempt(
        workspace_id="medical-horizon", task_id="task-approved"
    ) == 3
    assert "bridge-secret" not in repr(store)


def test_company_os_api_store_claims_and_posts_result_through_company_os_api():
    session = Mock()
    session.post.side_effect = [
        FakeResponse({"ok": True, "claim": {"workspaceId": "medical-horizon", "taskId": "task-1", "attempt": 1}}),
        FakeResponse({"ok": True, "resultId": "result-1", "status": "completed"}),
    ]
    store = CompanyOsApiCloudEventStore(
        "https://medical-horizon-company-os.vercel.app", "bridge-secret", session=session
    )

    claim = store.claim_agent_os_task(
        workspace_id="medical-horizon",
        task_id="task-1",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
        agent_os_id="company_os",
        task_kind="inbox_request",
        target_member_id="member_taro",
        attempt=1,
        selected_execution_engine="sinria_native",
    )
    result = store.post_agent_os_task_result(
        workspace_id="medical-horizon",
        task_id="task-1",
        agent_os_id="company_os",
        task_kind="inbox_request",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
        status="completed",
        sanitized_summary="完了（外部送信なし）",
        result_refs=[],
        external_egress=False,
        human_approval_required=False,
        attempt=1,
    )

    assert claim == {"workspaceId": "medical-horizon", "taskId": "task-1", "attempt": 1}
    assert result["resultId"] == "result-1"
    claim_call, result_call = session.post.call_args_list
    assert claim_call.args[0].endswith("/api/agent-os/tasks/claim")
    assert claim_call.kwargs["json"] == {
        "workspaceId": "medical-horizon",
        "taskId": "task-1",
        "memberId": "member_taro",
        "instanceId": "inst_taro_macbook",
        "selectedExecutionEngine": "sinria_native",
    }
    assert result_call.args[0].endswith("/api/agent-os/tasks/result")
    assert result_call.kwargs["json"]["humanApprovalRequired"] is False
    assert result_call.kwargs["json"]["producedByMemberId"] == "member_taro"


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


def test_supabase_store_next_claim_attempt_preserves_expired_history():
    session = Mock()
    session.get.return_value = FakeResponse([{"attempt": 1}])
    store = SupabaseRestCloudEventStore(
        "https://example.supabase.co", "secret-token", session=session, schema="company_os"
    )

    attempt = store.next_agent_os_task_attempt(workspace_id="workspace_1", task_id="task_1")

    assert attempt == 2
    url = session.get.call_args.args[0]
    expected = (
        "/rest/v1/agent_os_task_claims?workspace_id=eq.workspace_1"
        "&task_id=eq.task_1&select=attempt&order=attempt.desc&limit=1"
    )
    assert url.endswith(expected)


def test_supabase_store_next_claim_attempt_starts_at_one_without_history():
    session = Mock()
    session.get.return_value = FakeResponse([])
    store = SupabaseRestCloudEventStore(
        "https://example.supabase.co", "secret-token", session=session, schema="company_os"
    )

    assert store.next_agent_os_task_attempt(workspace_id="workspace_1", task_id="task_1") == 1


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
    session.post.return_value = FakeResponse([{"result_id": "aotr_task_1_1_taro_taro-local", "execution_contract_version": "sinria.execution.v1"}])
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

    body = session.post.call_args.kwargs["json"]["p_input"]
    assert body["resultRefs"]
    assert body["humanApprovalRequired"] is True
    post_url = session.post.call_args.args[0]
    assert post_url.endswith("/rest/v1/rpc/record_agent_os_task_result_v1")
    assert session.patch.call_count == 0


# ---------------------------------------------------------------------------
# Stage 1「整理と貫通」— 承認再開・review 自動生成・lease 更新（PostgREST 直結面）
# ---------------------------------------------------------------------------


def test_fetch_pending_agent_os_tasks_includes_approved_for_execution():
    session = Mock()
    session.get.return_value = FakeResponse([])
    store = SupabaseRestCloudEventStore("https://example.supabase.co", "secret-token", session=session)

    store.fetch_pending_agent_os_tasks(workspace_id="medical-horizon", member_id="member_taro")

    url = session.get.call_args.args[0]
    assert "status=in.(queued,failed_recoverable,approved_for_execution)" in url


def test_ensure_agent_os_review_request_creates_linked_review_once():
    session = Mock()
    # 1st call: no waiting review exists yet.
    session.get.return_value = FakeResponse([])
    session.post.return_value = FakeResponse([{"review_id": "review_x"}])
    store = SupabaseRestCloudEventStore("https://example.supabase.co", "secret-token", session=session)

    store.ensure_agent_os_review_request(
        workspace_id="medical-horizon",
        task_id="task_1",
        agent_os_id="company_os",
        task_kind="inbox_request",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
        required_authority="physician",
        sanitized_summary="外部送信を含むため承認待ち",
    )

    get_url = session.get.call_args.args[0]
    assert "review_requests" in get_url
    assert "task_id=eq.task_1" in get_url
    assert "status=eq.waiting" not in get_url
    assert "select=review_id,status" in get_url
    body = session.post.call_args.kwargs["json"]
    post_url = session.post.call_args.args[0]
    assert post_url.endswith("/rest/v1/review_requests")
    assert "resolution=ignore-duplicates" in session.post.call_args.kwargs["headers"]["Prefer"]
    assert body["task_id"] == "task_1"
    assert body["status"] == "waiting"
    # physician は Role 語彙に無いので最も厳しい owner へ（TS 側と同じ写像）。
    assert body["required_authority"] == "owner"
    assert body["raw_payload_stored"] is False
    assert body["external_action_performed"] is False


def test_ensure_agent_os_review_request_skips_when_waiting_review_exists():
    session = Mock()
    session.get.return_value = FakeResponse([{"review_id": "review_x"}])
    store = SupabaseRestCloudEventStore("https://example.supabase.co", "secret-token", session=session)

    store.ensure_agent_os_review_request(
        workspace_id="medical-horizon",
        task_id="task_1",
        agent_os_id="company_os",
        task_kind="inbox_request",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
        required_authority="self",
        sanitized_summary="再送",
    )

    session.post.assert_not_called()


def test_ensure_agent_os_review_request_never_overwrites_approved_review():
    session = Mock()
    session.get.return_value = FakeResponse(
        [{"review_id": "review_task_1", "status": "approved"}]
    )
    store = SupabaseRestCloudEventStore(
        "https://example.supabase.co", "secret-token", session=session
    )

    store.ensure_agent_os_review_request(
        workspace_id="medical-horizon",
        task_id="task_1",
        agent_os_id="company_os",
        task_kind="inbox_request",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
        required_authority="self",
        sanitized_summary="再試行で承認待ちと誤判定",
    )

    # Filtering only waiting rows makes the merge-upsert below overwrite an
    # approved row back to waiting. Terminal decisions are immutable.
    assert "status=eq.waiting" not in session.get.call_args.args[0]
    session.post.assert_not_called()


def test_has_approved_agent_os_review_reads_durable_execution_evidence():
    session = Mock()
    session.get.return_value = FakeResponse(
        [{"result_id": "aor_task_1_inst_a1", "human_approval_required": False}]
    )
    store = SupabaseRestCloudEventStore(
        "https://example.supabase.co", "secret-token", session=session
    )

    assert store.has_approved_agent_os_review(
        workspace_id="medical-horizon", task_id="task_1"
    ) is True
    url = session.get.call_args.args[0]
    assert "agent_os_task_results" in url
    assert "task_id=eq.task_1" in url
    assert "human_approval_required=eq.false" in url


def test_post_agent_os_task_result_uses_rpc_and_does_not_bypass_claim_validation():
    session = Mock()
    session.post.return_value = FakeResponse([{"result_id": "aor_1"}])
    session.patch.return_value = FakeResponse([{}])
    store = SupabaseRestCloudEventStore("https://example.supabase.co", "secret-token", session=session)

    store.post_agent_os_task_result(
        workspace_id="medical-horizon",
        task_id="task_1",
        agent_os_id="company_os",
        task_kind="inbox_request",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
        status="waiting_review",
        sanitized_summary="承認待ち",
        result_refs=[],
    )

    assert session.post.call_args.args[0].endswith("/rpc/record_agent_os_task_result_v1")
    assert session.post.call_args.kwargs["json"]["p_input"]["taskId"] == "task_1"
    session.patch.assert_not_called()


def test_renew_agent_os_task_claim_lease_patches_active_claim():
    session = Mock()
    session.patch.return_value = FakeResponse([{}])
    store = SupabaseRestCloudEventStore("https://example.supabase.co", "secret-token", session=session)

    store.renew_agent_os_task_claim_lease(
        workspace_id="medical-horizon",
        task_id="task_1",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
        lease_seconds=600,
    )

    call = session.patch.call_args
    assert "agent_os_task_claims" in call.args[0]
    assert "task_id=eq.task_1" in call.args[0]
    assert "claimed_by_member_id=eq.member_taro" in call.args[0]
    assert "claimed_by_instance_id=eq.inst_taro_macbook" in call.args[0]
    assert "claim_status=eq.active" in call.args[0]
    assert "claim_expires_at" in call.kwargs["json"]


def _load_worker_module():
    worker_path = Path("scripts/sinria-hybrid-bridge-worker.py").resolve()
    spec = importlib.util.spec_from_file_location("sinria_hybrid_bridge_worker_stage1", worker_path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    return worker


def test_worker_company_os_mode_uses_api_store_not_supabase(monkeypatch):
    worker = _load_worker_module()
    store = Mock()
    store.fetch_pending_agent_os_tasks.return_value = []
    factory = Mock(return_value=store)
    monkeypatch.setattr(worker, "CompanyOsApiCloudEventStore", factory)
    monkeypatch.setenv("SINRIA_COMPANY_OS_TRANSPORT_SUBJECT", "discord:member_taro")

    outcome = worker._run_once_company_os(
        "https://medical-horizon-company-os.vercel.app",
        "bridge-secret",
        "inst_taro_macbook",
        workspace_id="medical-horizon",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
    )

    factory.assert_called_once_with(
        "https://medical-horizon-company-os.vercel.app",
        "bridge-secret",
        transport_subject="discord:member_taro",
        workspace_id="medical-horizon",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
    )
    store.record_bridge_status.assert_called_once_with(
        status="online",
        capabilities=["agent-os-worker", "claim-renewal", "review-gate"],
        sanitized_summary="Sinria worker heartbeat",
    )
    assert outcome == {"success": True, "mode": "company_os_api_once", "outcome": "idle"}


def test_worker_exit_code_reflects_task_outcome_not_only_process_completion():
    worker = _load_worker_module()

    assert worker._worker_outcome_exit_code({"success": True, "outcome": "completed"}) == 0
    assert worker._worker_outcome_exit_code({"success": True, "outcome": "waiting_review"}) == 0
    assert worker._worker_outcome_exit_code({"success": True, "outcome": "idle"}) == 0
    assert worker._worker_outcome_exit_code({"success": True, "outcome": "failed_recoverable"}) != 0
    assert worker._worker_outcome_exit_code({"success": False, "outcome": "idle"}) != 0


def test_worker_explicit_supabase_url_keeps_legacy_adapter_reachable(monkeypatch, capsys):
    worker = _load_worker_module()
    legacy_run = Mock(return_value={"success": True, "mode": "supabase_once", "outcome": "idle"})
    company_os_run = Mock()
    monkeypatch.setattr(worker, "_run_once_supabase", legacy_run)
    monkeypatch.setattr(worker, "_run_once_company_os", company_os_run)
    monkeypatch.setattr(worker, "_wire_inbox_runner", Mock())
    monkeypatch.setenv("SINRIA_BRIDGE_TOKEN", "legacy-secret")
    monkeypatch.delenv("COMPANY_OS_BASE_URL", raising=False)
    monkeypatch.delenv("SINRIA_COMPANY_OS_BRIDGE_TOKEN", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["sinria-hybrid-bridge-worker.py", "--once", "--supabase-url", "https://legacy.example"],
    )

    assert worker.main() == 0
    legacy_run.assert_called_once()
    company_os_run.assert_not_called()
    assert json.loads(capsys.readouterr().out)["mode"] == "supabase_once"


def test_worker_company_os_mode_uses_shared_outcome_exit_policy(monkeypatch, capsys):
    worker = _load_worker_module()
    monkeypatch.setattr(
        worker,
        "_run_once_company_os",
        Mock(return_value={"success": True, "mode": "company_os_api_once", "outcome": "failed_recoverable"}),
    )
    monkeypatch.setattr(worker, "_wire_inbox_runner", Mock())
    monkeypatch.setenv("SINRIA_COMPANY_OS_BRIDGE_TOKEN", "company-os-secret")
    monkeypatch.setenv("SINRIA_COMPANY_OS_TRANSPORT_SUBJECT", "discord:member_taro")
    monkeypatch.delenv("COMPANY_OS_BASE_URL", raising=False)
    monkeypatch.delenv("SINRIA_BRIDGE_SUPABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["sinria-hybrid-bridge-worker.py", "--once"])

    assert worker.main() != 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "failed_recoverable"


def test_worker_company_os_mode_uses_subject_scoped_transport_token(monkeypatch, capsys):
    worker = _load_worker_module()
    run_once = Mock(return_value={"success": True, "mode": "company_os_api_once", "outcome": "idle"})
    monkeypatch.setattr(worker, "_run_once_company_os", run_once)
    monkeypatch.setattr(worker, "_wire_inbox_runner", Mock())
    monkeypatch.setenv("SINRIA_COMPANY_OS_TRANSPORT_TOKEN", "subject-scoped-secret")
    monkeypatch.setenv("SINRIA_COMPANY_OS_BRIDGE_TOKEN", "legacy-global-secret")
    monkeypatch.setenv("SINRIA_COMPANY_OS_TRANSPORT_SUBJECT", "discord:member_taro")
    monkeypatch.delenv("COMPANY_OS_BASE_URL", raising=False)
    monkeypatch.delenv("SINRIA_BRIDGE_SUPABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["sinria-hybrid-bridge-worker.py", "--once"])

    assert worker.main() == 0
    assert run_once.call_args.args[1] == "subject-scoped-secret"
    assert json.loads(capsys.readouterr().out)["outcome"] == "idle"


class _Stage1FakeStore:
    """PostgREST 面の fake: Stage 1 の worker 変更が呼ぶ操作を記録する。"""

    def __init__(self, tasks, *, approved_task_ids=()):
        self._tasks = tasks
        self._approved_task_ids = set(approved_task_ids)
        self.claims = []
        self.results = []
        self.reviews = []
        self.renews = []

    def fetch_pending_agent_os_tasks(self, *, workspace_id, member_id, limit=1):
        return list(self._tasks)

    def claim_agent_os_task(self, **kwargs):
        self.claims.append(kwargs)
        return {"claim_id": f"claim_{kwargs['task_id']}"}

    def post_agent_os_task_result(self, **kwargs):
        self.results.append(kwargs)

    def ensure_agent_os_review_request(self, **kwargs):
        self.reviews.append(kwargs)

    def has_approved_agent_os_review(self, *, workspace_id, task_id):
        return task_id in self._approved_task_ids

    def renew_agent_os_task_claim_lease(self, **kwargs):
        self.renews.append(kwargs)


def test_worker_task_filter_selects_only_exact_target(monkeypatch, tmp_path):
    worker = _load_worker_module()
    older = {
        "task_id": "task_older",
        "workspace_id": "medical-horizon",
        "agent_os_id": "company_os",
        "task_kind": "inbox_request",
        "target_member_id": "member_taro",
        "instruction_summary": "older task",
        "status": "queued",
        "payload": {},
        "policy": {"humanApprovalRequired": False},
        "raw_context_allowed_in_cloud": False,
    }
    target = {**older, "task_id": "task_target", "instruction_summary": "target task", "status": "approved_for_execution"}
    store = _Stage1FakeStore([older, target])
    monkeypatch.setenv("SINRIA_COMPANY_OS_TASK_ID", "task_target")
    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", lambda url, auth: store)
    monkeypatch.setattr(worker, "INBOX_INFLIGHT_STATE_PATH", tmp_path / "inflight.json")
    seen = []
    monkeypatch.setattr(worker, "dispatch_agentos_task", lambda task, identity: seen.append(task["task_id"]) or {"status": "completed", "sanitizedSummary": "done"})

    outcome = worker._run_once_supabase(
        "https://example.supabase.co", "secret-token", "inst_taro_macbook",
        workspace_id="medical-horizon", member_id="member_taro", instance_id="inst_taro_macbook",
    )

    assert outcome["outcome"] == "completed"
    assert seen == ["task_target"]
    assert store.claims[0]["task_id"] == "task_target"


def test_worker_task_filter_does_not_resume_unrelated_inflight(monkeypatch, tmp_path):
    worker = _load_worker_module()
    inflight_path = tmp_path / "inflight.json"
    inflight_path.write_text(json.dumps({"task_other": {"task": {"task_id": "task_other"}, "approval": True}}))
    store = _Stage1FakeStore([])
    monkeypatch.setenv("SINRIA_COMPANY_OS_TASK_ID", "task_target")
    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", lambda url, auth: store)
    monkeypatch.setattr(worker, "INBOX_INFLIGHT_STATE_PATH", inflight_path)
    def fail_if_dispatched(task, identity):
        raise AssertionError("unrelated in-flight task must not resume")

    monkeypatch.setattr(worker, "dispatch_agentos_task", fail_if_dispatched)

    outcome = worker._run_once_supabase(
        "https://example.supabase.co", "secret-token", "inst_taro_macbook",
        workspace_id="medical-horizon", member_id="member_taro", instance_id="inst_taro_macbook",
    )

    assert outcome["outcome"] == "idle"
    assert json.loads(inflight_path.read_text())["task_other"]
    assert store.renews == []


def test_worker_executes_approved_task_with_approval_granted(monkeypatch, tmp_path):
    worker = _load_worker_module()
    store = _Stage1FakeStore(
        [
            {
                "task_id": "task_approved",
                "workspace_id": "medical-horizon",
                "agent_os_id": "company_os",
                "task_kind": "inbox_request",
                "target_member_id": "member_taro",
                "instruction_summary": "承認済みの外部送信を実行",
                "status": "approved_for_execution",
                "payload": {},
                "policy": {"humanApprovalRequired": True},
                "raw_context_allowed_in_cloud": False,
            }
        ]
    )
    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", lambda url, auth: store)
    monkeypatch.setattr(worker, "INBOX_INFLIGHT_STATE_PATH", tmp_path / "inflight.json")

    seen = {}

    def fake_dispatch(task, identity):
        seen["payload"] = dict(task.get("payload") or {})
        return {"status": "completed", "sanitizedSummary": "実行完了"}

    monkeypatch.setattr(worker, "dispatch_agentos_task", fake_dispatch)

    outcome = worker._run_once_supabase(
        "https://example.supabase.co",
        "secret-token",
        "inst_taro_macbook",
        workspace_id="medical-horizon",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
    )

    assert outcome["outcome"] == "completed"
    # 承認済みフラグが handler へローカル注入される（cloud には書かない）。
    assert seen["payload"].get("humanApprovalGranted") is True
    # 承認は既に記録済みなので結果は human_approval_required=False。
    assert store.results[0]["human_approval_required"] is False


def test_worker_retry_keeps_durable_approval_after_recoverable_failure(monkeypatch, tmp_path):
    worker = _load_worker_module()
    store = _Stage1FakeStore(
        [
            {
                "task_id": "task_approved_retry",
                "workspace_id": "medical-horizon",
                "agent_os_id": "company_os",
                "task_kind": "inbox_request",
                "target_member_id": "member_taro",
                "instruction_summary": "承認済み案件を再実行",
                "status": "failed_recoverable",
                "payload": {},
                "policy": {"humanApprovalRequired": True},
                "raw_context_allowed_in_cloud": False,
            }
        ],
        approved_task_ids={"task_approved_retry"},
    )
    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", lambda url, auth: store)
    monkeypatch.setattr(worker, "INBOX_INFLIGHT_STATE_PATH", tmp_path / "inflight.json")
    seen = {}

    def fake_dispatch(task, identity):
        seen["payload"] = dict(task.get("payload") or {})
        return {"status": "completed", "sanitizedSummary": "再実行完了"}

    monkeypatch.setattr(worker, "dispatch_agentos_task", fake_dispatch)

    outcome = worker._run_once_supabase(
        "https://example.supabase.co",
        "secret-token",
        "inst_taro_macbook",
        workspace_id="medical-horizon",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
    )

    assert outcome["outcome"] == "completed"
    assert seen["payload"]["humanApprovalGranted"] is True
    assert store.results[0]["human_approval_required"] is False


def test_worker_never_reopens_approval_after_durable_approval(monkeypatch, tmp_path):
    worker = _load_worker_module()
    store = _Stage1FakeStore(
        [
            {
                "task_id": "task_already_approved",
                "workspace_id": "medical-horizon",
                "agent_os_id": "company_os",
                "task_kind": "inbox_request",
                "target_member_id": "member_taro",
                "instruction_summary": "承認済み案件を実行",
                "status": "approved_for_execution",
                "payload": {},
                "policy": {"humanApprovalRequired": True},
                "raw_context_allowed_in_cloud": False,
            }
        ]
    )
    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", lambda url, auth: store)
    monkeypatch.setattr(worker, "INBOX_INFLIGHT_STATE_PATH", tmp_path / "inflight.json")
    monkeypatch.setattr(
        worker,
        "dispatch_agentos_task",
        lambda task, identity: {
            "status": "waiting_review",
            "sanitizedSummary": "追加前提が不足",
            "reviewRequested": True,
        },
    )

    outcome = worker._run_once_supabase(
        "https://example.supabase.co",
        "secret-token",
        "inst_taro_macbook",
        workspace_id="medical-horizon",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
    )

    assert outcome["outcome"] == "failed_recoverable"
    assert store.results[0]["status"] == "failed_recoverable"
    assert store.results[0]["human_approval_required"] is False
    assert store.reviews == []


def test_worker_waiting_review_result_ensures_linked_review(monkeypatch, tmp_path):
    worker = _load_worker_module()
    store = _Stage1FakeStore(
        [
            {
                "task_id": "task_gate",
                "workspace_id": "medical-horizon",
                "agent_os_id": "company_os",
                "task_kind": "inbox_request",
                "target_member_id": "member_taro",
                "instruction_summary": "外部送信を含む依頼",
                "status": "queued",
                "payload": {},
                "policy": {"humanApprovalRequired": True, "requiredAuthority": "admin"},
                "raw_context_allowed_in_cloud": False,
            }
        ]
    )
    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", lambda url, auth: store)
    monkeypatch.setattr(worker, "INBOX_INFLIGHT_STATE_PATH", tmp_path / "inflight.json")
    monkeypatch.setattr(
        worker,
        "dispatch_agentos_task",
        lambda task, identity: {"status": "waiting_review", "sanitizedSummary": "計画を作成"},
    )

    outcome = worker._run_once_supabase(
        "https://example.supabase.co",
        "secret-token",
        "inst_taro_macbook",
        workspace_id="medical-horizon",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
    )

    assert outcome["outcome"] == "waiting_review"
    assert store.reviews, "waiting_review must ensure a linked ReviewRequest"
    review = store.reviews[0]
    assert review["task_id"] == "task_gate"
    assert review["required_authority"] == "admin"


def test_worker_waiting_review_preserves_stricter_handler_authority(monkeypatch, tmp_path):
    worker = _load_worker_module()
    store = _Stage1FakeStore(
        [
            {
                "task_id": "task_clinical_placeholder",
                "workspace_id": "medical-horizon",
                "agent_os_id": "company_os",
                "task_kind": "command_center_task",
                "target_member_id": "member_taro",
                "instruction_summary": "fixed non-identifying clinical placeholder",
                "status": "queued",
                "payload": {"surface": "command_center"},
                "policy": {"humanApprovalRequired": True, "requiredAuthority": "self"},
                "raw_context_allowed_in_cloud": False,
            }
        ]
    )
    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", lambda url, auth: store)
    monkeypatch.setattr(worker, "INBOX_INFLIGHT_STATE_PATH", tmp_path / "inflight.json")
    monkeypatch.setattr(
        worker,
        "dispatch_agentos_task",
        lambda task, identity: {
            "status": "waiting_review",
            "sanitizedSummary": "local re-entry required; no model call",
            "requiredAuthority": "owner",
            "humanApprovalRequired": True,
            "externalActionPerformed": False,
            "rawLocalContextStored": False,
        },
    )

    outcome = worker._run_once_supabase(
        "https://example.supabase.co",
        "secret-token",
        "inst_taro_macbook",
        workspace_id="medical-horizon",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
    )

    assert outcome["outcome"] == "waiting_review"
    assert store.reviews[0]["required_authority"] == "owner"


def test_worker_in_progress_result_persists_and_resumes_across_ticks(monkeypatch, tmp_path):
    worker = _load_worker_module()
    inflight_path = tmp_path / "inflight.json"
    task_row = {
        "task_id": "task_async",
        "workspace_id": "medical-horizon",
        "agent_os_id": "company_os",
        "task_kind": "inbox_request",
        "target_member_id": "member_taro",
        "instruction_summary": "時間のかかる調査",
        "status": "queued",
        "payload": {},
        "policy": {"humanApprovalRequired": False},
        "raw_context_allowed_in_cloud": False,
    }
    store = _Stage1FakeStore([task_row])
    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", lambda url, auth: store)
    monkeypatch.setattr(worker, "INBOX_INFLIGHT_STATE_PATH", inflight_path)

    calls = {"n": 0}

    def fake_dispatch(task, identity):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": "in_progress", "runRef": "run_1"}
        return {"status": "completed", "sanitizedSummary": "調査完了"}

    monkeypatch.setattr(worker, "dispatch_agentos_task", fake_dispatch)

    kwargs = dict(
        workspace_id="medical-horizon",
        member_id="member_taro",
        instance_id="inst_taro_macbook",
    )

    # tick 1: claim → in_progress → result は投稿されない・in-flight 保存。
    first = worker._run_once_supabase(
        "https://example.supabase.co", "secret-token", "inst_taro_macbook", **kwargs
    )
    assert first["outcome"] == "in_progress"
    assert store.results == []
    persisted = json.loads(inflight_path.read_text())["task_async"]
    assert persisted["claim_id"] == "claim_task_async"

    # tick 2: fetch を経由せず in-flight から再ディスパッチ → 完了投稿・state クリア。
    store._tasks = []  # cloud 上では claimed なので fetch には出ない
    second = worker._run_once_supabase(
        "https://example.supabase.co", "secret-token", "inst_taro_macbook", **kwargs
    )
    assert second["outcome"] == "completed"
    assert store.results and store.results[0]["task_id"] == "task_async"
    assert json.loads(inflight_path.read_text()) == {}
    # in-flight 中は lease が更新される。
    assert store.renews, "in-flight tick must renew the claim lease"
    assert store.renews[0]["claim_id"] == "claim_task_async"


def test_postgrest_post_agent_os_task_result_uses_contract_input_not_direct_row_id():
    """Attempt-scoped result_id: without it, retry outcomes silently merge into
    the first attempt's row (created_at stays stale), which hid every failure
    after attempt 1 of the chronic inbox_request task."""
    session = Mock()
    session.post.return_value = FakeResponse([{"result_id": "x"}])
    session.patch.return_value = FakeResponse([{"task_id": "task_1"}])
    store = SupabaseRestCloudEventStore("https://example.supabase.co", "secret-token", session=session)

    store.post_agent_os_task_result(
        workspace_id="medical_horizon",
        task_id="task_1",
        agent_os_id="company_os",
        task_kind="inbox_request",
        member_id="taro",
        instance_id="taro-local",
        status="failed_recoverable",
        sanitized_summary="attempt 3 failed",
        attempt=3,
    )
    body = session.post.call_args.kwargs["json"]["p_input"]
    assert body["taskId"] == "task_1"
    assert "result_id" not in body

    store.post_agent_os_task_result(
        workspace_id="medical_horizon",
        task_id="task_1",
        agent_os_id="company_os",
        task_kind="inbox_request",
        member_id="taro",
        instance_id="taro-local",
        status="completed",
        sanitized_summary="legacy caller without attempt",
    )
    body = session.post.call_args.kwargs["json"]["p_input"]
    assert body["taskId"] == "task_1"
    assert session.patch.call_count == 0


def test_worker_passes_claim_attempt_through_parking_and_result(monkeypatch, tmp_path):
    """The worker must carry the monotonic claim attempt into BOTH the parked
    in-flight entry and the posted result, so per-attempt results stay visible."""
    worker = _load_worker_module()
    monkeypatch.setattr(worker, "INBOX_INFLIGHT_STATE_PATH", tmp_path / "inflight.json")

    class FakeStore:
        def __init__(self, url, auth_value):
            self.claims = []
            self.results = []

        def fetch_pending_agent_os_tasks(self, *, workspace_id, member_id, limit=1):
            return [
                {
                    "task_id": "task_attempt",
                    "workspace_id": workspace_id,
                    "agent_os_id": "company_os",
                    "task_kind": "inbox_request",
                    "target_member_id": member_id,
                    "payload": {},
                    "policy": {"humanApprovalRequired": False},
                }
            ]

        def next_agent_os_task_attempt(self, *, workspace_id, task_id):
            return 3

        def claim_agent_os_task(self, **kwargs):
            self.claims.append(kwargs)
            return {"claim_id": "claim_attempt", "attempt": kwargs.get("attempt")}

        def post_agent_os_task_result(self, **kwargs):
            self.results.append(kwargs)

    stores = []

    def factory(url, auth_value):
        store = FakeStore(url, auth_value)
        stores.append(store)
        return store

    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", factory)

    # Phase 1: handler parks the task in_progress → attempt must be recorded.
    monkeypatch.setattr(
        worker, "dispatch_agentos_task", lambda task, identity: {"status": "in_progress", "runRef": "run_9"}
    )
    outcome = worker._run_once_supabase(
        "https://example.supabase.co",
        "secret-token",
        "onprem-a",
        workspace_id="org-med",
        member_id="taro",
        instance_id="onprem-a",
    )
    assert outcome["outcome"] == "in_progress"
    assert stores[0].claims[0]["attempt"] == 3
    inflight = json.loads((tmp_path / "inflight.json").read_text())
    assert inflight["task_attempt"]["attempt"] == 3

    # Phase 2: the resumed in-flight task reaches a terminal state → the result
    # row must carry the SAME attempt (no reset to the legacy shared id).
    monkeypatch.setattr(
        worker,
        "dispatch_agentos_task",
        lambda task, identity: {"status": "failed_recoverable", "sanitizedSummary": "boom"},
    )
    outcome2 = worker._run_once_supabase(
        "https://example.supabase.co",
        "secret-token",
        "onprem-a",
        workspace_id="org-med",
        member_id="taro",
        instance_id="onprem-a",
    )
    assert outcome2["outcome"] == "failed_recoverable"
    assert stores[1].results[0]["attempt"] == 3
