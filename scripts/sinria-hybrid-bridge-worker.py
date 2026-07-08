#!/usr/bin/env python3
"""Worker for Sinria Hybrid Agent Bridge.

Default mode is still safe dry-run.  `--once --mock-cloud` executes one local
in-memory iteration for CI/development.  A real Supabase/PostgREST adapter can be
used with `--once --supabase-url ...` once approved credentials are provided.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

# Allow running directly from a checkout without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sinria_hybrid_bridge import BridgeTaskStatus, BridgeTransport, plan_task, worker_contract  # noqa: E402
from sinria_hybrid_bridge_transports import InMemoryCloudEventStore, PollingBridgeRunner  # noqa: E402
from sinria_hybrid_bridge_http import SupabaseRestCloudEventStore  # noqa: E402
from sinria_agentos_handlers import (  # noqa: E402
    LocalExecutionIdentity,
    dispatch_agentos_task,
    registered_handler_keys,
)
from sinria_local_execution_adapters import (  # noqa: E402
    NATIVE_ENGINE,
    adapter_availability,
    invoke_local_execution_adapter,
    select_execution_engine,
)


def _env_present(name: str) -> bool:
    return bool(os.environ.get(name))


def _load_mock_store_from_env() -> InMemoryCloudEventStore:
    from sinria_hybrid_bridge_http import SupabaseRestCloudEventStore as Mapper

    raw = os.environ.get("SINRIA_BRIDGE_MOCK_TASK_JSON")
    store = InMemoryCloudEventStore()
    if raw:
        row = json.loads(raw)
        store.add_task(Mapper._task_from_row(row))
    return store


def _run_once_mock(sinria_instance_id: str) -> dict:
    store = _load_mock_store_from_env()
    runner = PollingBridgeRunner(store=store, sinria_instance_id=sinria_instance_id)
    outcome = runner.run_once(lambda task: f"Sinria mock processed {task.task_id}: {task.task_text_summary}")
    return {
        "success": True,
        "mode": "mock_cloud_once",
        "outcome": outcome,
        "results": [result.__dict__ for result in store.results],
        "review_requests": [request.__dict__ for request in store.review_requests],
    }


def _row_field(row: dict, *names: str, default=None):
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return default


def _safe_call_store(store, method_name: str, **kwargs) -> None:
    method = getattr(store, method_name, None)
    if not callable(method):
        return
    try:
        method(**kwargs)
    except Exception:
        # Learning-loop writes are best-effort and must never break task result posting.
        return


def _record_sales_learning_loop(
    *,
    store,
    task: dict,
    workspace_id: str,
    task_id: str,
    agent_os_id: str,
    task_kind: str,
    member_id: str,
    instance_id: str,
    status: str,
    sanitized_summary: str,
    result_refs: list,
    selected_engine: str,
) -> None:
    """Record sanitized Sales Agent OS learning metadata from local executions.

    This connects Kikuchi's local Claude Code Sales Agent OS work to the Company OS
    Learning OS without exporting raw prompts, outputs, diffs, contacts, or drafts.
    Candidates are review-gated and never auto-promoted into shared skills.
    """
    if agent_os_id != "sales_agent_os":
        return
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    outcome_signal = payload.get("outcomeSignal") or payload.get("outcome_signal")
    if outcome_signal not in {
        "positive_reply",
        "conversion",
        "time_saved",
        "quality_improved",
        "failure",
        "near_miss",
        "manual_rework",
        "unknown",
    }:
        outcome_signal = "failure" if status == "failed_recoverable" else "quality_improved" if status == "completed" else "unknown"
    source_refs = [str(ref) for ref in (result_refs or []) if str(ref).startswith("local://")]
    observation_id = f"kao_{task_id}_{instance_id}"[:120]
    _safe_call_store(
        store,
        "record_knowledge_asset_observation",
        observation_id=observation_id,
        workspace_id=workspace_id,
        observed_by_member_id=member_id,
        observed_by_instance_id=instance_id,
        source_kind="outcome",
        domain="sales",
        sanitized_summary=(
            f"Sales Agent OS task {task_kind} via {selected_engine}: {status}. "
            f"{sanitized_summary}"
        )[:500],
        outcome_signal=outcome_signal,
        source_refs=source_refs,
        raw_source_stored=False,
        raw_media_stored=False,
        patient_data_stored=False,
        external_action_performed=False,
    )
    if status == "failed_recoverable":
        _safe_call_store(
            store,
            "record_improvement_candidate",
            candidate_id=f"ic_{task_id}_{instance_id}"[:120],
            workspace_id=workspace_id,
            proposed_by_member_id=member_id,
            proposed_by_instance_id=instance_id,
            title=f"sales:{task_kind} local execution failure",
            sanitized_summary=(
                f"goal=complete Sales Agent OS task via {selected_engine} / actual=failed_recoverable. "
                "Review local adapter policy, prompt, or runtime setup. Raw artifacts remain local."
            ),
            category="process",
            status="proposed",
            human_approval_required=True,
            raw_evidence_stored=False,
            skill_body_stored=False,
            external_action_performed=False,
        )
        return
    if status == "completed":
        _safe_call_store(
            store,
            "record_knowledge_asset_candidate",
            asset_id=f"kac_{task_id}_{instance_id}"[:120],
            workspace_id=workspace_id,
            proposed_by_member_id=member_id,
            proposed_by_instance_id=instance_id,
            asset_kind="playbook_candidate",
            title=f"Sales Agent OS Claude Code execution pattern: {task_kind}",
            sanitized_pattern=(
                "Local Sinria claimed a Sales Agent OS task, delegated execution to the employee's "
                "approved Claude Code adapter, kept raw artifacts local, and returned only sanitized metadata."
            ),
            evidence_summary=(
                f"source={observation_id}; task_kind={task_kind}; engine={selected_engine}; "
                "raw_evidence_stored=false"
            ),
            confidence="medium",
            status="candidate",
            reuse_targets=["sales_agent_os", "company_os"],
            source_observation_ids=[observation_id],
            human_approval_required=True,
            raw_evidence_stored=False,
            raw_source_stored=False,
            raw_procedure_body_stored=False,
            external_action_performed=False,
        )


def _normalize_agent_os_task_policy(row: dict) -> dict:
    """Return a row copy with a camelCase policy object for runtime gates.

    PostgREST returns ``agent_os_tasks`` as snake_case columns, while the local
    execution adapter intentionally consumes the same camelCase policy shape as
    the browser/API route.  Normalize at the worker boundary so production rows
    created through Supabase select the same engine and approval gates that tests
    using in-memory envelopes exercise.

    The optional payload.policy.localAdapterExecutionApproved boolean is metadata
    only; raw task bodies still stay out of cloud and the env-side approval gate
    remains required before any developer adapter is launched.
    """
    normalized = dict(row)
    maybe_existing = row.get("policy")
    existing = maybe_existing if isinstance(maybe_existing, dict) else {}
    maybe_payload = row.get("payload")
    payload = maybe_payload if isinstance(maybe_payload, dict) else {}
    maybe_payload_policy = payload.get("policy")
    payload_policy = maybe_payload_policy if isinstance(maybe_payload_policy, dict) else {}
    normalized["policy"] = {
        "humanApprovalRequired": _row_field(row, "human_approval_required", "humanApprovalRequired", default=True),
        "externalActionAllowed": _row_field(row, "external_action_allowed", "externalActionAllowed", default=False),
        "externalEgressAllowed": _row_field(row, "external_egress_allowed", "externalEgressAllowed", default=False),
        "requiredAuthority": _row_field(row, "required_authority", "requiredAuthority", default="self"),
        "preferredExecutionEngine": _row_field(row, "preferred_execution_engine", "preferredExecutionEngine", default=NATIVE_ENGINE),
        "allowedExecutionEngines": _row_field(row, "allowed_execution_engines", "allowedExecutionEngines", default=[NATIVE_ENGINE]),
        "adapterRawContextAllowed": _row_field(row, "adapter_raw_context_allowed", "adapterRawContextAllowed", default=False),
        "rawContextAllowedInCloud": _row_field(row, "raw_context_allowed_in_cloud", "rawContextAllowedInCloud", default=False),
        "localAdapterExecutionApproved": payload_policy.get(
            "localAdapterExecutionApproved",
            existing.get("localAdapterExecutionApproved", False),
        ) is True,
        **{k: v for k, v in existing.items() if k not in {"localAdapterExecutionApproved"}},
    }
    if "execution_environment" in row and row["execution_environment"] not in (None, ""):
        normalized["policy"]["executionEnvironment"] = row["execution_environment"]
    elif "executionEnvironment" in row and row["executionEnvironment"] not in (None, ""):
        normalized["policy"]["executionEnvironment"] = row["executionEnvironment"]
    return normalized


def _run_once_supabase(
    url: str,
    auth_value: str,
    sinria_instance_id: str,
    *,
    workspace_id: str = "personal",
    member_id: str = "local_user",
    instance_id: str | None = None,
) -> dict:
    try:
        store = SupabaseRestCloudEventStore(url, auth_value, schema="company_os")
    except TypeError:
        # Test doubles / older adapter constructors may not accept the schema
        # keyword. The real live Agent OS path above uses the company_os schema;
        # doubles keep exercising the same worker flow without network headers.
        store = SupabaseRestCloudEventStore(url, auth_value)
    effective_instance_id = instance_id or sinria_instance_id
    identity = LocalExecutionIdentity(
        workspace_id=workspace_id,
        member_id=member_id,
        instance_id=effective_instance_id,
    )
    tasks = store.fetch_pending_agent_os_tasks(workspace_id=workspace_id, member_id=member_id, limit=5)
    tasks = [
        task
        for task in tasks
        if not _row_field(task, "target_instance_id", "targetInstanceId")
        or _row_field(task, "target_instance_id", "targetInstanceId") == effective_instance_id
    ]
    if not tasks:
        return {"success": True, "mode": "supabase_agent_os_once", "outcome": "idle"}
    task = _normalize_agent_os_task_policy(tasks[0])
    task_id = _row_field(task, "task_id", "id")
    agent_os_id = _row_field(task, "agent_os_id", "agentOsId")
    task_kind = _row_field(task, "task_kind", "taskKind")
    target_member_id = _row_field(task, "target_member_id", "targetMemberId", default=member_id)
    selected_engine = select_execution_engine(task, identity)
    attempt = 1
    claim = store.claim_agent_os_task(
        workspace_id=workspace_id,
        task_id=task_id,
        member_id=member_id,
        instance_id=effective_instance_id,
        agent_os_id=agent_os_id,
        task_kind=task_kind,
        target_member_id=target_member_id,
        attempt=attempt,
        selected_execution_engine=selected_engine,
    )
    if claim is None:
        return {
            "success": True,
            "mode": "supabase_agent_os_once",
            "outcome": "claim_rejected",
            "task_id": task_id,
        }

    if selected_engine == NATIVE_ENGINE:
        result = dispatch_agentos_task(task, identity)
    else:
        result = invoke_local_execution_adapter(
            engine_id=selected_engine,
            task=task,
            identity=identity,
            dry_run=False,
        )

    status = str(result.get("status") or "waiting_review")
    sanitized_summary = str(
        result.get("sanitizedSummary")
        or result.get("sanitizedCommandSummary")
        or "Sinria local execution completed with a sanitized metadata-only result."
    )
    policy = task.get("policy") or {}
    human_approval_required = bool(policy.get("humanApprovalRequired", status != "completed"))
    result_refs = result.get("resultRefs") or result.get("localArtifactRefs") or []
    store.post_agent_os_task_result(
        workspace_id=workspace_id,
        task_id=task_id,
        agent_os_id=agent_os_id,
        task_kind=task_kind,
        member_id=member_id,
        instance_id=effective_instance_id,
        status=status,
        sanitized_summary=sanitized_summary,
        result_refs=result_refs,
        external_egress=bool(result.get("externalEgress", False)),
        human_approval_required=human_approval_required,
    )
    _record_sales_learning_loop(
        store=store,
        task=task,
        workspace_id=workspace_id,
        task_id=task_id,
        agent_os_id=agent_os_id,
        task_kind=task_kind,
        member_id=member_id,
        instance_id=effective_instance_id,
        status=status,
        sanitized_summary=sanitized_summary,
        result_refs=result_refs,
        selected_engine=selected_engine,
    )
    return {
        "success": True,
        "mode": "supabase_agent_os_once",
        "outcome": status,
        "task_id": task_id,
        "claim_id": claim.get("claim_id") if isinstance(claim, dict) else None,
        "selected_execution_engine": selected_engine,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sinria Hybrid Agent Bridge worker")
    parser.add_argument("--dry-run", action="store_true", help="Print outbound-only bridge contract and exit")
    parser.add_argument("--once", action="store_true", help="Run one polling/claim/result iteration and exit")
    parser.add_argument("--mock-cloud", action="store_true", help="Use SINRIA_BRIDGE_MOCK_TASK_JSON instead of a network adapter")
    parser.add_argument("--supabase-url", default=os.environ.get("SINRIA_BRIDGE_SUPABASE_URL"))
    parser.add_argument("--transport", default=os.environ.get("SINRIA_BRIDGE_TRANSPORT", "polling"), choices=["polling", "realtime", "queue", "secure_tunnel"])
    parser.add_argument("--app-id", default=os.environ.get("SINRIA_BRIDGE_APP_ID", "chatops_crm"))
    parser.add_argument("--tenant-id", default=os.environ.get("SINRIA_BRIDGE_TENANT_ID", "medical_horizon"))
    parser.add_argument("--sinria-instance-id", default=os.environ.get("SINRIA_INSTANCE_ID", "onprem-local"))
    # Team Mode execution identity: this worker only claims tasks addressed to its
    # workspace/member/instance. Defaults are env-driven so each employee's local
    # Sinria identifies itself without baking identity into the code.
    parser.add_argument("--workspace-id", default=os.environ.get("SINRIA_WORKSPACE_ID", "personal"))
    parser.add_argument("--member-id", default=os.environ.get("SINRIA_MEMBER_ID", "local_user"))
    parser.add_argument(
        "--instance-id",
        default=os.environ.get("SINRIA_INSTANCE_ID") or socket.gethostname(),
    )
    parser.add_argument("--poll-interval", type=float, default=float(os.environ.get("SINRIA_BRIDGE_POLL_INTERVAL_SECONDS", "5")))
    args = parser.parse_args()

    contract = worker_contract(BridgeTransport(args.transport))
    identity = {
        "workspace_id": args.workspace_id,
        "member_id": args.member_id,
        "instance_id": args.instance_id,
    }
    payload = {
        **contract,
        "app_id": args.app_id,
        "tenant_id": args.tenant_id,
        "identity": identity,
        "poll_interval_seconds": args.poll_interval,
        "required_secret_env_present": {
            "SINRIA_BRIDGE_TOKEN": _env_present("SINRIA_BRIDGE_TOKEN"),
        },
        # Agent OS routing: which (agentOsId, taskKind) handlers this local Sinria
        # can run, and which local execution adapters are available/allowlisted.
        # Raw context, credentials and tokens are NEVER part of this payload.
        "agent_os_handlers": [f"{a}:{k}" for (a, k) in registered_handler_keys()],
        "local_execution_adapters": adapter_availability(args.member_id, args.instance_id),
        "safety": {
            "credential_stored_in_cloud": False,
            "raw_context_stored": False,
            "local_memory_synced_to_cloud": False,
            "external_action_performed": False,
            "outbound_only": True,
        },
        "status": "dry_run_ready" if args.dry_run else "ready",
    }

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.once and args.mock_cloud:
        print(json.dumps(_run_once_mock(args.sinria_instance_id), ensure_ascii=False, indent=2))
        return 0

    if args.once and args.supabase_url:
        auth_value = os.environ.get("SINRIA_BRIDGE_TOKEN")
        if not auth_value:
            print(json.dumps({"success": False, "error": "SINRIA_BRIDGE_TOKEN is required for Supabase mode"}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(_run_once_supabase(
            args.supabase_url,
            auth_value,
            args.sinria_instance_id,
            workspace_id=args.workspace_id,
            member_id=args.member_id,
            instance_id=args.instance_id,
        ), ensure_ascii=False, indent=2))
        return 0

    print(
        json.dumps(
            {
                "success": False,
                "error": "Choose --dry-run, --once --mock-cloud, or --once --supabase-url <url>. Long-running daemon loop is intentionally not enabled until the cloud adapter is approved.",
                "contract": payload,
            },
            ensure_ascii=False,
            indent=2,
        ),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
