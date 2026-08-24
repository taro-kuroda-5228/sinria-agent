import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not (ROOT / "scripts/sinria-sales-bridge-daemon-v2.py").exists(),
    reason="Sales bridge production overlay is not included in this distribution",
)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sinria_local_execution_adapters  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_sales_outreach_runner():
    """Keep the global sales-outreach runner registration hermetic.

    sinria_agentos_handlers._SALES_RUNNER is a module-level global. The Sales
    bridge daemon (scripts/sinria-sales-bridge-daemon-v2.py) registers its
    real DB-backed runner at *import time* via set_sales_outreach_runner(), and
    several sibling test modules import that daemon. Under xdist, whichever of
    those modules shares a worker with this file leaks the real runner into the
    process, so worker._run_once_supabase()'s dispatch reaches the live Sales
    executor instead of the draft-safe ``_SALES_RUNNER is None`` path — and the
    routing-guard assertions below (status == "waiting_review",
    human_approval_required) flake depending on collection order. Save/None/
    restore makes this file independent of that ordering.
    """
    import sinria_agentos_handlers as _handlers

    previous = _handlers._SALES_RUNNER
    _handlers.set_sales_outreach_runner(None)
    try:
        yield
    finally:
        _handlers.set_sales_outreach_runner(previous)


def _load_worker_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "sinria-hybrid-bridge-worker.py"
    spec = importlib.util.spec_from_file_location("sinria_hybrid_bridge_worker_production_guard", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeAgentOsStore:
    def __init__(self, url, auth_value):
        self.url = url
        self.auth_value = auth_value
        self.fetch_legacy_called = False
        self.claims = []
        self.results = []

    def fetch_pending_tasks(self, *, limit=1):  # legacy path must not be used for production Team Mode
        self.fetch_legacy_called = True
        return []

    def fetch_pending_agent_os_tasks(self, *, workspace_id, member_id, limit=1):
        self.fetched_identity = (workspace_id, member_id, limit)
        return [
            {
                "task_id": "aot_sales_1",
                "workspace_id": "medical_horizon",
                "agent_os_id": "sales_agent_os",
                "task_kind": "sales_outreach_plan",
                "requested_by_member_id": "taro",
                "target_member_id": "taro",
                "target_instance_id": "taro-local-sinria",
                "payload": {"instruction": "訪問看護候補を調べる", "maxTotal": 3},
                "policy": {
                    "allowedExecutionEngines": ["sinria_native"],
                    "preferredExecutionEngine": "sinria_native",
                    "humanApprovalRequired": True,
                    "externalActionAllowed": False,
                },
                "raw_context_allowed_in_cloud": False,
            }
        ]

    def claim_agent_os_task(self, **kwargs):
        self.claims.append(kwargs)
        return {"claim_id": "claim_aot_sales_1"}

    def post_agent_os_task_result(self, **kwargs):
        self.results.append(kwargs)


def test_worker_supabase_once_uses_generic_agent_os_routing_not_legacy_tasks(monkeypatch):
    worker = _load_worker_module()
    stores = []

    def fake_factory(url, auth_value):
        store = _FakeAgentOsStore(url, auth_value)
        stores.append(store)
        return store

    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", fake_factory)

    outcome = worker._run_once_supabase(
        "https://example.supabase.co",
        "secret-token",
        "taro-local-sinria",
        workspace_id="medical_horizon",
        member_id="taro",
        instance_id="taro-local-sinria",
    )

    store = stores[0]
    assert outcome["mode"] == "supabase_agent_os_once"
    assert outcome["task_id"] == "aot_sales_1"
    assert store.fetch_legacy_called is False
    assert store.fetched_identity == ("medical_horizon", "taro", 5)
    assert store.claims[0]["member_id"] == "taro"
    assert store.claims[0]["instance_id"] == "taro-local-sinria"
    assert store.claims[0]["selected_execution_engine"] == "sinria_native"
    assert store.results[0]["status"] == "waiting_review"
    assert store.results[0]["human_approval_required"] is True
    serialized = json.dumps({"outcome": outcome, "result": store.results[0]}, ensure_ascii=False)
    assert "secret-token" not in serialized
    assert "raw" not in serialized.lower() or "raw_context_allowed_in_cloud" not in serialized


def test_sales_bridge_daemon_claim_filters_task_text_routing_identity():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "sinria-sales-bridge-daemon-v2.py").read_text()
    assert "jsonb_extract_path_text(task_text::jsonb, 'routing', 'targetMemberId')" in source
    assert "jsonb_extract_path_text(task_text::jsonb, 'routing', 'targetInstanceId')" in source
    assert "SINRIA_MEMBER_ID" in source
    assert "SINRIA_INSTANCE_ID" in source
    claim_body = source[source.index("def _claim_one") : source.index("def _start_run")]
    assert "where app_id = 'chatops_crm' and status = 'pending'" in claim_body
    assert "targetMemberId" in claim_body
    assert "targetInstanceId" in claim_body
    assert "allow_unrouted_legacy" in claim_body
    assert "target_member_id = %s" in claim_body
    assert "or (%s and target_member_id is null)" in claim_body
    assert "SINRIA_ALLOW_UNROUTED_LEGACY_TASKS" in source


def test_sales_bridge_scheduler_enqueues_routed_non_ambiguous_tasks():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "sinria-sales-bridge-daemon-v2.py").read_text()
    enqueue_body = source[source.index("def _enqueue_task") : source.index("# -------------------- Main loop")]
    assert "target_member_id: str" in enqueue_body
    assert '"targetMemberId": target_member_id' in enqueue_body
    assert '"targetInstanceId": target_instance_id' in enqueue_body
    assert '"executionExecutorMode": execution_executor_mode' in enqueue_body
    assert '"claimRequired": execution_executor_mode == "local_sinria"' in enqueue_body


# ── Task 4: Worker integration test — claude_code engine via generic Agent OS path ──


# Sentinel value embedded in the fake subprocess stdout so we can assert it
# never escapes to the cloud-visible store result.
_FAKE_STDOUT_SENTINEL = "RAW_CLAUDE_OUTPUT_SENTINEL_KIKUCHI_99999"

# Sentinel for the fake auth token — must never appear in stored claim/result.
_FAKE_AUTH_TOKEN = "fake-secret-token-kikuchi-xxxyyy"


class _FakeKikuchiStore:
    """Fake store returning a claude_code-targeted task for member_kikuchi / inst_kikuchi_local."""

    def __init__(self, url, auth_value):
        self.url = url
        self.auth_value = auth_value
        self.fetch_legacy_called = False
        self.fetched_identity: tuple | None = None
        self.claims: list[dict] = []
        self.results: list[dict] = []

    def fetch_pending_tasks(self, *, limit=1):
        # Legacy path must never be invoked for generic Agent OS routing.
        self.fetch_legacy_called = True
        return []

    def fetch_pending_agent_os_tasks(self, *, workspace_id, member_id, limit=1):
        self.fetched_identity = (workspace_id, member_id, limit)
        return [
            {
                "task_id": "aot_kikuchi_cc_1",
                "workspace_id": "workspace_medical_horizon",
                "agent_os_id": "sales_agent_os",
                "task_kind": "implementation",
                "requested_by_member_id": "taro",
                "target_member_id": "member_kikuchi",
                "target_instance_id": "inst_kikuchi_local",
                "payload": {"instruction": "implement the Kikuchi adapter"},
                "policy": {
                    "preferredExecutionEngine": "claude_code",
                    "allowedExecutionEngines": ["sinria_native", "claude_code"],
                    "humanApprovalRequired": False,
                    "externalActionAllowed": False,
                    "externalEgressAllowed": False,
                    "localAdapterExecutionApproved": True,
                },
                "raw_context_allowed_in_cloud": False,
            }
        ]

    def claim_agent_os_task(self, **kwargs):
        self.claims.append(kwargs)
        return {"claim_id": "claim_kikuchi_cc_1"}

    def post_agent_os_task_result(self, **kwargs):
        self.results.append(kwargs)


class _FakeKikuchiStorePostgrestSnake(_FakeKikuchiStore):
    """Fake production-shaped PostgREST row: snake_case columns, no policy object."""

    def fetch_pending_agent_os_tasks(self, *, workspace_id, member_id, limit=1):
        self.fetched_identity = (workspace_id, member_id, limit)
        return [
            {
                "task_id": "aot_kikuchi_cc_snake_1",
                "workspace_id": "medical-horizon",
                "agent_os_id": "sales_agent_os",
                "task_kind": "implementation",
                "requested_by_member_id": "member_taro",
                "target_member_id": "member_kikuchi",
                "target_instance_id": "inst_kikuchi_local",
                "payload": {
                    "acceptanceCriteria": "synthetic no-secret smoke only",
                    "policy": {"localAdapterExecutionApproved": True},
                },
                "human_approval_required": False,
                "external_action_allowed": False,
                "external_egress_allowed": False,
                "preferred_execution_engine": "claude_code",
                "allowed_execution_engines": ["sinria_native", "claude_code"],
                "adapter_raw_context_allowed": False,
                "raw_context_allowed_in_cloud": False,
            }
        ]



class _FakeKikuchiSalesLearningStore(_FakeKikuchiStorePostgrestSnake):
    def __init__(self, url, auth_value):
        super().__init__(url, auth_value)
        self.knowledge_observations = []
        self.knowledge_candidates = []
        self.improvement_candidates = []

    def fetch_pending_agent_os_tasks(self, *, workspace_id, member_id, limit=1):
        rows = super().fetch_pending_agent_os_tasks(workspace_id=workspace_id, member_id=member_id, limit=limit)
        rows[0]["task_id"] = "aot_kikuchi_sales_cc_learning_1"
        rows[0]["task_kind"] = "sales_outreach_plan"
        rows[0]["payload"]["outcomeSignal"] = "quality_improved"
        return rows

    def record_knowledge_asset_observation(self, **kwargs):
        self.knowledge_observations.append(kwargs)
        return kwargs

    def record_knowledge_asset_candidate(self, **kwargs):
        self.knowledge_candidates.append(kwargs)
        return kwargs

    def record_improvement_candidate(self, **kwargs):
        self.improvement_candidates.append(kwargs)
        return kwargs


def _setup_claude_code_mocks(monkeypatch, tmp_path):
    """Apply all mocks required so the claude_code path runs without touching
    real subprocess, real ~/.sinria, or real binaries.
    """
    # Make `claude` appear installed so select_execution_engine picks claude_code.
    monkeypatch.setattr(sinria_local_execution_adapters, "_installed", lambda _bin: True)

    # Redirect artifact writes to tmp_path (never real ~/.sinria).
    monkeypatch.setattr(sinria_local_execution_adapters, "get_sinria_home", lambda: tmp_path)

    # Replace subprocess.run with a fake that returns a well-formed success payload.
    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=json.dumps({"num_turns": 1, "subtype": "success", "sentinel": _FAKE_STDOUT_SENTINEL}),
            stderr="",
        )

    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", _fake_run)

    # Enable the env-side approval gate.
    monkeypatch.setenv("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", "claude_code")
    monkeypatch.setenv("SINRIA_LOCAL_ADAPTER_EXECUTION_APPROVED", "1")


def test_worker_records_selected_execution_engine_claude_code_via_generic_agent_os_path(
    monkeypatch, tmp_path
):
    """End-to-end integration: the worker must select claude_code, record it at claim
    time, route through invoke_local_execution_adapter (NOT dispatch_agentos_task),
    and post a result that contains zero raw content.

    Assertions:
    1. selected_execution_engine == "claude_code" in the claim kwargs (recorded in the store).
    2. The legacy fetch_pending_tasks path was never called (generic Agent OS path used).
    3. No raw content (stdout sentinel, auth token) appears in the serialized store state.
    4. All result_refs are local:// (or empty) — no cloud-raw refs.
    5. The outcome dict also carries selected_execution_engine == "claude_code".
    """
    worker = _load_worker_module()
    stores: list[_FakeKikuchiStore] = []
    _setup_claude_code_mocks(monkeypatch, tmp_path)

    def _fake_factory(url, auth_value):
        store = _FakeKikuchiStore(url, auth_value)
        stores.append(store)
        return store

    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", _fake_factory)

    outcome = worker._run_once_supabase(
        "https://example.supabase.co",
        _FAKE_AUTH_TOKEN,
        "inst_kikuchi_local",
        workspace_id="workspace_medical_horizon",
        member_id="member_kikuchi",
        instance_id="inst_kikuchi_local",
    )

    assert stores, "store factory was never called"
    store = stores[0]

    # 0. The generic fetch received the worker's routing identity verbatim.
    assert store.fetched_identity == ("workspace_medical_horizon", "member_kikuchi", 5)

    # 1. Engine was selected AND recorded at claim time.
    assert store.claims, "no claim was made"
    assert store.claims[0]["selected_execution_engine"] == "claude_code", (
        f"expected claude_code in claim, got: {store.claims[0].get('selected_execution_engine')!r}"
    )

    # 2. Generic Agent OS path was used (legacy path must NOT have been called).
    assert store.fetch_legacy_called is False, "legacy fetch_pending_tasks was called — routing bug"

    # 3. No raw content in the serialized store state.
    serialized = json.dumps(
        {"outcome": outcome, "claims": store.claims, "results": store.results},
        ensure_ascii=False,
    )
    assert _FAKE_AUTH_TOKEN not in serialized, "auth token must never reach the cloud store"
    assert _FAKE_STDOUT_SENTINEL not in serialized, (
        "raw stdout sentinel must not appear in the serialized store state"
    )

    # 4. All result_refs are local:// or empty.
    assert store.results, "no result was posted"
    result_refs = store.results[0].get("result_refs") or []
    for ref in result_refs:
        assert ref.startswith("local://"), (
            f"result_ref {ref!r} is not a local:// ref — raw content may have leaked"
        )

    # 5. outcome carries selected_execution_engine == "claude_code".
    assert outcome.get("selected_execution_engine") == "claude_code", (
        f"outcome missing claude_code engine selection: {outcome}"
    )
    assert outcome["mode"] == "supabase_agent_os_once"
    assert outcome["task_id"] == "aot_kikuchi_cc_1"


def test_worker_records_sales_claude_code_result_into_learning_loop(monkeypatch, tmp_path):
    worker = _load_worker_module()
    stores: list[_FakeKikuchiSalesLearningStore] = []
    _setup_claude_code_mocks(monkeypatch, tmp_path)

    def _fake_factory(url, auth_value, **_kwargs):
        store = _FakeKikuchiSalesLearningStore(url, auth_value)
        stores.append(store)
        return store

    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", _fake_factory)

    outcome = worker._run_once_supabase(
        "https://example.supabase.co",
        _FAKE_AUTH_TOKEN,
        "inst_kikuchi_local",
        workspace_id="medical-horizon",
        member_id="member_kikuchi",
        instance_id="inst_kikuchi_local",
    )

    store = stores[0]
    assert outcome["task_id"] == "aot_kikuchi_sales_cc_learning_1"
    assert outcome["selected_execution_engine"] == "claude_code"
    assert store.results[0]["status"] == "completed"
    assert store.knowledge_observations, "sales Claude Code result should create a knowledge observation"
    assert store.knowledge_candidates, "sales Claude Code result should create a review-gated knowledge candidate"
    observation = store.knowledge_observations[0]
    candidate = store.knowledge_candidates[0]
    assert observation["domain"] == "sales"
    assert observation["outcome_signal"] == "quality_improved"
    assert observation["raw_source_stored"] is False
    assert observation["external_action_performed"] is False
    assert candidate["status"] == "candidate"
    assert candidate["human_approval_required"] is True
    assert candidate["raw_evidence_stored"] is False
    assert "sales_agent_os" in candidate["reuse_targets"]
    serialized = json.dumps(
        {
            "outcome": outcome,
            "results": store.results,
            "observations": store.knowledge_observations,
            "candidates": store.knowledge_candidates,
            "improvements": store.improvement_candidates,
        },
        ensure_ascii=False,
    )
    assert _FAKE_AUTH_TOKEN not in serialized
    assert _FAKE_STDOUT_SENTINEL not in serialized
    assert "RAW" not in serialized


def test_worker_normalizes_production_snake_case_policy_for_claude_code(monkeypatch, tmp_path):
    """Real Supabase rows are snake_case columns, not in-memory policy objects.

    The worker must normalize those columns before engine selection/invocation;
    otherwise production would silently fall back to sinria_native even though the
    Company OS task selected claude_code.
    """
    worker = _load_worker_module()
    stores: list[_FakeKikuchiStorePostgrestSnake] = []
    _setup_claude_code_mocks(monkeypatch, tmp_path)

    def _fake_factory(url, auth_value, **_kwargs):
        store = _FakeKikuchiStorePostgrestSnake(url, auth_value)
        stores.append(store)
        return store

    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", _fake_factory)

    outcome = worker._run_once_supabase(
        "https://example.supabase.co",
        _FAKE_AUTH_TOKEN,
        "inst_kikuchi_local",
        workspace_id="medical-horizon",
        member_id="member_kikuchi",
        instance_id="inst_kikuchi_local",
    )

    assert stores
    store = stores[0]
    assert store.claims[0]["selected_execution_engine"] == "claude_code"
    assert outcome.get("selected_execution_engine") == "claude_code"
    assert outcome["task_id"] == "aot_kikuchi_cc_snake_1"
    serialized = json.dumps({"outcome": outcome, "claims": store.claims, "results": store.results})
    assert _FAKE_AUTH_TOKEN not in serialized
    assert _FAKE_STDOUT_SENTINEL not in serialized


def test_worker_filters_out_task_addressed_to_different_instance(monkeypatch, tmp_path):
    """A task whose target_instance_id does NOT match the running worker's instance_id
    must be filtered out before claiming. The outcome must be 'idle' and no claim
    must be recorded, proving mis-addressed tasks can never be claimed by the wrong worker.
    """
    worker = _load_worker_module()
    stores: list[_FakeKikuchiStore] = []
    _setup_claude_code_mocks(monkeypatch, tmp_path)

    def _fake_factory(url, auth_value):
        store = _FakeKikuchiStore(url, auth_value)
        stores.append(store)
        return store

    monkeypatch.setattr(worker, "SupabaseRestCloudEventStore", _fake_factory)

    # Run with a DIFFERENT instance_id — the task targets "inst_kikuchi_local"
    # but this worker identifies as "inst_OTHER_worker".
    outcome = worker._run_once_supabase(
        "https://example.supabase.co",
        _FAKE_AUTH_TOKEN,
        "inst_OTHER_worker",
        workspace_id="workspace_medical_horizon",
        member_id="member_kikuchi",
        instance_id="inst_OTHER_worker",
    )

    assert outcome["outcome"] == "idle", (
        f"expected 'idle' when instance_id does not match, got: {outcome['outcome']!r}"
    )

    # No claim must have been made (task was filtered before reaching claim step).
    assert stores, "store factory was never called"
    store = stores[0]
    assert store.claims == [], (
        "claim was made for a task addressed to a different instance — routing bug"
    )
