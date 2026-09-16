#!/usr/bin/env python3
"""Worker for Sinria Hybrid Agent Bridge.

Default mode is still safe dry-run.  `--once --mock-cloud` executes one local
in-memory iteration for CI/development.  Real execution uses the canonical
Company OS HTTP API; the direct Supabase adapter remains an explicit legacy
compatibility option only.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import socket
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable

import requests

# Allow running directly from a checkout without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sinria_hybrid_bridge import BridgeTaskStatus, BridgeTransport, plan_task, worker_contract  # noqa: E402
from sinria_hybrid_bridge_transports import InMemoryCloudEventStore, PollingBridgeRunner  # noqa: E402
from sinria_hybrid_bridge_http import CompanyOsApiCloudEventStore, SupabaseRestCloudEventStore  # noqa: E402
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
from agent.line_task_completion import enqueue_line_task_completion  # noqa: E402


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


_REQUIRED_AUTHORITY_RANK = {
    "self": 0,
    "reviewer": 1,
    "admin": 2,
    "owner": 3,
    "physician": 4,
}


def _strictest_required_authority(*authorities: object) -> str:
    """Return the strictest known review authority; unknown values fail closed."""
    values = [str(value) for value in authorities if value not in (None, "")]
    if not values:
        return "self"
    if any(value not in _REQUIRED_AUTHORITY_RANK for value in values):
        return "owner"
    return max(values, key=_REQUIRED_AUTHORITY_RANK.__getitem__)


def _worker_outcome_exit_code(outcome: dict) -> int:
    """Make cron health reflect task outcome, not merely Python process survival."""
    if outcome.get("success") is False:
        return 2
    if str(outcome.get("outcome") or "") in {"failed_recoverable", "blocked_retry_storm"}:
        return 4
    return 0


def _classify_adapter_failure(exc: Exception) -> str:
    """Classify transport failures without retaining exception text or URLs."""
    if isinstance(exc, (requests.Timeout, TimeoutError)):
        return "adapter_timeout"
    if isinstance(exc, (requests.ConnectionError, ConnectionError)):
        return "adapter_connection_error"
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status in {401, 403}:
            return "adapter_auth_rejected"
        if status == 429:
            return "adapter_rate_limited"
        if isinstance(status, int) and status >= 500:
            return "adapter_server_error"
        return "adapter_http_error"
    if isinstance(exc, json.JSONDecodeError):
        return "adapter_invalid_response"
    return "adapter_unexpected_error"


def _record_safe_failure(*, mode: str, failure_stage: str, error_kind: str) -> None:
    """Append metadata-only diagnostics locally; logging failure never masks the contract."""
    try:
        home = Path(os.environ.get("SINRIA_HOME") or os.environ.get("HERMES_HOME") or (Path.home() / ".sinria"))
        log_dir = home / "company_os"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "bridge-worker-errors.jsonl"
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "failure_stage": failure_stage,
            "error_kind": error_kind,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        log_path.chmod(0o600)
    except Exception:
        pass


def _recoverable_adapter_failure(mode: str, *, failure_stage: str, error_kind: str) -> dict:
    """Return and locally record a sanitized recoverable-failure contract.

    Exception text can contain endpoint paths, task data, or credentials, so the
    cron-facing payload intentionally exposes only a stable failure class.
    """
    outcome = {
        "success": False,
        "mode": mode,
        "outcome": "failed_recoverable",
        "failure_stage": failure_stage,
        "error_kind": error_kind,
    }
    _record_safe_failure(mode=mode, failure_stage=failure_stage, error_kind=error_kind)
    return outcome


def _safe_call_store(store, method_name: str, **kwargs):
    method = getattr(store, method_name, None)
    if not callable(method):
        return None
    try:
        return method(**kwargs)
    except Exception:
        # Learning-loop writes are best-effort and must never break task result posting.
        return None


# ---------------------------------------------------------------------------
# In-flight state — async runs span multiple cron ticks (Stage 1 貫通)
# ---------------------------------------------------------------------------

# A generic inbox task executes as an async gateway run that outlives one cron
# tick (cron.script_timeout_seconds is ~120s while agent work takes minutes).
# When a handler reports ``in_progress`` the claimed task envelope is parked
# here; later ticks re-dispatch it FROM THIS FILE (the cloud fetch no longer
# returns it — its status is ``claimed``) until the handler yields a terminal
# result. One in-flight task per instance keeps Stage 1 simple and auditable.


def _default_inflight_path() -> Path:
    try:
        from hermes_constants import get_sinria_home

        return get_sinria_home() / "company_os" / "inbox_inflight.json"
    except Exception:
        return Path.home() / ".sinria" / "company_os" / "inbox_inflight.json"


INBOX_INFLIGHT_STATE_PATH = _default_inflight_path()


def _load_inflight() -> dict:
    try:
        return json.loads(Path(INBOX_INFLIGHT_STATE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_inflight(state: dict) -> None:
    path = Path(INBOX_INFLIGHT_STATE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


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
    _store=None,
    _mode: str = "supabase_agent_os_once",
) -> dict:
    store = _store
    if store is None:
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
    target_task_id = os.environ.get("SINRIA_COMPANY_OS_TASK_ID", "").strip()

    inflight = _load_inflight()
    if inflight:
        matching_inflight = [
            (key, value)
            for key, value in inflight.items()
            if not target_task_id
            or str(_row_field(value.get("task", {}), "task_id", "id", default="")) == target_task_id
        ]
        if target_task_id and not matching_inflight:
            return {"success": True, "mode": _mode, "outcome": "idle"}
        # Resume exactly one previously claimed task.  Do not poll for another
        # task while any local execution is still in flight.
        task_id, entry = matching_inflight[0]
        task = _normalize_agent_os_task_policy(dict(entry.get("task") or {}))

        _safe_call_store(
            store,
            "renew_agent_os_task_claim_lease",
            workspace_id=workspace_id,
            task_id=task_id,
            member_id=member_id,
            instance_id=effective_instance_id,
            claim_id=entry.get("claim_id"),
            attempt=entry.get("attempt"),
        )
        outcome = _execute_claimed_agent_os_task(
            store,
            task,
            identity,
            workspace_id=workspace_id,
            member_id=member_id,
            effective_instance_id=effective_instance_id,
            approval_granted=bool(entry.get("approval")),
            selected_engine=entry.get("selected_engine") or select_execution_engine(task, identity),
            claim=None,
            attempt=entry.get("attempt"),
            mode=_mode,
        )
        return outcome

    fetch_tasks = store.fetch_pending_agent_os_tasks
    try:
        tasks = fetch_tasks(
            workspace_id=workspace_id,
            member_id=member_id,
            instance_id=effective_instance_id,
            limit=5,
        )
    except TypeError as exc:
        if "instance_id" not in str(exc):
            raise
        tasks = fetch_tasks(workspace_id=workspace_id, member_id=member_id, limit=5)
    tasks = [
        task
        for task in tasks
        if (
            not _row_field(task, "target_instance_id", "targetInstanceId")
            or _row_field(task, "target_instance_id", "targetInstanceId") == effective_instance_id
        )
        and (
            not target_task_id
            or str(_row_field(task, "task_id", "id", default="")) == target_task_id
        )
    ]
    if not tasks:
        return {"success": True, "mode": _mode, "outcome": "idle"}
    task = _normalize_agent_os_task_policy(tasks[0])
    task_id = _row_field(task, "task_id", "id")
    agent_os_id = _row_field(task, "agent_os_id", "agentOsId")
    task_kind = _row_field(task, "task_kind", "taskKind")
    target_member_id = _row_field(task, "target_member_id", "targetMemberId", default=member_id)
    # Consent is durable review state, not a transient task status. A recoverable
    # execution failure changes the task status, but must not erase the human
    # decision on the next retry.
    approval_granted = str(_row_field(task, "status", default="")) == "approved_for_execution"
    read_durable_approval = getattr(store, "has_approved_agent_os_review", None)
    if not approval_granted and callable(read_durable_approval):
        approval_granted = bool(
            read_durable_approval(workspace_id=workspace_id, task_id=task_id)
        )
    selected_engine = select_execution_engine(task, identity)
    next_attempt = getattr(store, "next_agent_os_task_attempt", None)
    attempt_value = (
        next_attempt(workspace_id=workspace_id, task_id=task_id)
        if callable(next_attempt)
        else 1
    )
    attempt = attempt_value if isinstance(attempt_value, int) and attempt_value > 0 else 1
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
            "mode": _mode,
            "outcome": "claim_rejected",
            "task_id": task_id,
        }

    return _execute_claimed_agent_os_task(
        store,
        task,
        identity,
        workspace_id=workspace_id,
        member_id=member_id,
        effective_instance_id=effective_instance_id,
        approval_granted=approval_granted,
        selected_engine=selected_engine,
        claim=claim,
        attempt=attempt,
        mode=_mode,
    )


def _execute_claimed_agent_os_task(
    store,
    task: dict,
    identity: "LocalExecutionIdentity",
    *,
    workspace_id: str,
    member_id: str,
    effective_instance_id: str,
    approval_granted: bool,
    selected_engine: str,
    claim: dict | None,
    attempt: int | None = None,
    mode: str = "supabase_agent_os_once",
) -> dict:
    """Dispatch a claimed task and post its (terminal) result.

    ``in_progress`` results are parked in the in-flight state file instead of
    being posted; later ticks resume them until the handler yields a terminal
    status. Human approval consent is injected LOCALLY into the task payload —
    it never travels back to the cloud.
    """
    task_id = _row_field(task, "task_id", "id")
    agent_os_id = _row_field(task, "agent_os_id", "agentOsId")
    task_kind = _row_field(task, "task_kind", "taskKind")

    local_task = dict(task)
    policy = task.get("policy") or {}
    approval_required = bool(policy.get("humanApprovalRequired"))
    if approval_granted:
        payload = dict(local_task.get("payload") or {})
        payload["humanApprovalGranted"] = True
        local_task["payload"] = payload

    if selected_engine == NATIVE_ENGINE:
        result = dispatch_agentos_task(local_task, identity)
    else:
        result = invoke_local_execution_adapter(
            engine_id=selected_engine,
            task=local_task,
            identity=identity,
            dry_run=False,
        )

    status = str(result.get("status") or "waiting_review")
    if approval_required and not approval_granted and status == "completed":
        # A local handler may run a safe, side-effect-free pre-flight before
        # approval and even self-report a stricter requiredAuthority, but it must
        # not reach a terminal completion without a recorded human decision.
        # in_progress (async run parked for a later tick) is allowed; only a
        # premature "completed" is downgraded, preserving other sanitized signals.
        result = dict(result)
        result["status"] = "waiting_review"
        result.setdefault("reviewRequested", True)
        status = "waiting_review"
    if approval_granted and status == "waiting_review":
        # A completed human decision is monotonic. If execution discovers an
        # unmet prerequisite or an out-of-scope action, stop recoverably rather
        # than mutating the original approval back to waiting.
        status = "failed_recoverable"

    if status == "in_progress":
        inflight = _load_inflight()
        inflight[str(task_id)] = {
            "task": task,
            "approval": bool(approval_granted),
            "selected_engine": selected_engine,
            "run_ref": result.get("runRef"),
            "claim_id": _row_field(claim or {}, "claim_id", "claimId", "id", default=None),
            "attempt": attempt,
        }
        _save_inflight(inflight)
        return {
            "success": True,
            "mode": mode,
            "outcome": "in_progress",
            "task_id": task_id,
            "run_ref": result.get("runRef"),
            "selected_execution_engine": selected_engine,
        }

    sanitized_summary = str(
        result.get("sanitizedSummary")
        or result.get("sanitizedCommandSummary")
        or "Sinria local execution completed with a sanitized metadata-only result."
    )
    policy = task.get("policy") or {}
    human_approval_required = bool(policy.get("humanApprovalRequired", status != "completed"))
    if approval_granted:
        # Persist the already-granted consent independently of execution
        # outcome so recoverable retries do not lose it.
        human_approval_required = False
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
        attempt=attempt,
    )
    # A LINE-origin task is acknowledged silently at intake. After Company OS
    # accepts the terminal result, stage only its sanitized summary in the local
    # durable outbox. The connected LINE adapter owns the actual reply and
    # idempotent delivery state; raw source text never enters the outbox.
    try:
        enqueue_line_task_completion(
            task,
            task_id=str(task_id),
            status=status,
            sanitized_summary=sanitized_summary,
        )
    except (OSError, sqlite3.Error, ValueError):
        _record_safe_failure(
            mode=mode,
            failure_stage="line_completion_outbox",
            error_kind="local_outbox_unavailable",
        )
    review = None
    if status == "waiting_review":
        # Keep the paused task resumable. A new approval lifecycle gets a new
        # review id; a still-open review is reused.
        review = _safe_call_store(
            store,
            "ensure_agent_os_review_request",
            workspace_id=workspace_id,
            task_id=task_id,
            agent_os_id=agent_os_id,
            task_kind=task_kind,
            member_id=member_id,
            instance_id=effective_instance_id,
            required_authority=_strictest_required_authority(
                policy.get("requiredAuthority"), result.get("requiredAuthority")
            ),
            sanitized_summary=sanitized_summary,
        )
    inflight = _load_inflight()
    if str(task_id) in inflight:
        inflight.pop(str(task_id), None)
        _save_inflight(inflight)
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
        "success": status != "failed_recoverable",
        "mode": mode,
        "outcome": status,
        "task_kind": task_kind,
        "sanitized_summary": sanitized_summary,
        "side_effect": str(result.get("sideEffect") or result.get("side_effect") or ""),
        "external_egress": bool(result.get("externalEgress", False)),
        "task_id": task_id,
        "claim_id": claim.get("claim_id") if isinstance(claim, dict) else None,
        "review_id": review.get("review_id") if isinstance(review, dict) else None,
        "selected_execution_engine": selected_engine,
    }


def _run_once_company_os(
    url: str,
    auth_value: str,
    sinria_instance_id: str,
    *,
    workspace_id: str = "medical-horizon",
    member_id: str = "member_taro",
    instance_id: str | None = None,
) -> dict:
    transport_subject = os.environ.get("SINRIA_COMPANY_OS_TRANSPORT_SUBJECT", "").strip()
    if not transport_subject:
        raise RuntimeError("SINRIA_COMPANY_OS_TRANSPORT_SUBJECT is required for Company OS API mode")
    effective_instance_id = instance_id or sinria_instance_id
    store = CompanyOsApiCloudEventStore(
        url,
        auth_value,
        transport_subject=transport_subject,
        workspace_id=workspace_id,
        member_id=member_id,
        instance_id=effective_instance_id,
    )
    store.record_bridge_status(
        status="online",
        capabilities=["agent-os-worker", "claim-renewal", "review-gate"],
        sanitized_summary="Sinria worker heartbeat",
    )
    return _run_once_supabase(
        url,
        auth_value,
        sinria_instance_id,
        workspace_id=workspace_id,
        member_id=member_id,
        instance_id=instance_id,
        _store=store,
        _mode="company_os_api_once",
    )


def _wire_inbox_runner() -> None:
    """Register the async gateway runner for generic company_os:inbox_request tasks.

    Opt-out via SINRIA_INBOX_RUNNER=off. Registration is cheap and network-free;
    the runner only touches the gateway when an inbox task is actually dispatched.
    """
    mode = os.environ.get("SINRIA_INBOX_RUNNER", "gateway").strip().lower()
    if mode in ("off", "none", "0"):
        return
    try:
        from sinria_agentos_handlers import set_inbox_request_runner

        if mode == "synthetic":
            # Offline backend E2E smoke: no gateway, no model call, sanitized only.
            from sinria_company_os_inbox_runner import build_synthetic_inbox_runner

            set_inbox_request_runner(build_synthetic_inbox_runner())
            return

        from sinria_company_os_inbox_runner import build_gateway_runs_runner

        set_inbox_request_runner(build_gateway_runs_runner())
    except Exception:
        # A worker without the runner still handles vertical tasks; inbox tasks
        # report failed_recoverable ("no inbox runner") instead of crashing.
        return


def _run_company_os_loop(
    company_os_url: str,
    auth_value: Any,
    sinria_instance_id: str,
    *,
    workspace_id: str,
    member_id: str,
    instance_id: str,
    poll_interval: float,
    max_iterations: int | None = None,
    run_once_fn: Callable[..., dict[str, Any]] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    emit_fn: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the canonical Company OS worker until stopped.

    The loop emits only sanitized worker outcomes. Exceptions are reduced to
    their class name so credentials or upstream response bodies cannot leak to
    logs. ``max_iterations`` and the injected callables keep the daemon path
    deterministic under test.
    """

    run_once = run_once_fn or _run_once_company_os
    emit = emit_fn or (lambda payload: print(json.dumps(payload, ensure_ascii=False), flush=True))
    iterations = 0
    last_outcome = "idle"
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        try:
            outcome = run_once(
                company_os_url,
                auth_value,
                sinria_instance_id,
                workspace_id=workspace_id,
                member_id=member_id,
                instance_id=instance_id,
            )
            last_outcome = str(outcome.get("outcome") or "unknown")
            if last_outcome != "idle":
                emit(outcome)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            last_outcome = "retrying"
            emit(
                {
                    "success": False,
                    "mode": "company_os_api",
                    "outcome": "retrying",
                    "error_type": type(exc).__name__,
                }
            )
        if max_iterations is None or iterations < max_iterations:
            sleep_fn(max(0.1, float(poll_interval)))
    return {
        "success": True,
        "mode": "company_os_api",
        "iterations": iterations,
        "last_outcome": last_outcome,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sinria Hybrid Agent Bridge worker")
    parser.add_argument("--dry-run", action="store_true", help="Print outbound-only bridge contract and exit")
    parser.add_argument("--once", action="store_true", help="Run one polling/claim/result iteration and exit")
    parser.add_argument("--mock-cloud", action="store_true", help="Use SINRIA_BRIDGE_MOCK_TASK_JSON instead of a network adapter")
    parser.add_argument("--company-os-url", default=os.environ.get("COMPANY_OS_BASE_URL"))
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
    if not args.company_os_url and not args.supabase_url:
        args.company_os_url = "https://medical-horizon-company-os.vercel.app"

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
            "SINRIA_COMPANY_OS_TRANSPORT_TOKEN": _env_present("SINRIA_COMPANY_OS_TRANSPORT_TOKEN"),
            "SINRIA_COMPANY_OS_BRIDGE_TOKEN": _env_present("SINRIA_COMPANY_OS_BRIDGE_TOKEN"),
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
        # Keep the cron result contract identical for local no-network smokes.
        payload["outcome"] = "dry_run_ready"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.once and args.mock_cloud:
        print(json.dumps(_run_once_mock(args.sinria_instance_id), ensure_ascii=False, indent=2))
        return 0

    if args.company_os_url:
        # Subject-scoped transport auth is the current Company OS contract.
        # Keep the bridge tokens only as explicit legacy compatibility fallbacks.
        auth_value = (
            os.environ.get("SINRIA_COMPANY_OS_TRANSPORT_TOKEN")
            or os.environ.get("SINRIA_COMPANY_OS_BRIDGE_TOKEN")
            or os.environ.get("SINRIA_BRIDGE_TOKEN")
        )
        transport_subject = os.environ.get("SINRIA_COMPANY_OS_TRANSPORT_SUBJECT", "").strip()
        if not auth_value:
            print(json.dumps({"success": False, "error": "SINRIA_COMPANY_OS_TRANSPORT_TOKEN is required for Company OS API mode (legacy bridge tokens remain supported)"}, ensure_ascii=False), file=sys.stderr)
            return 2
        if not transport_subject:
            print(json.dumps({"success": False, "error": "SINRIA_COMPANY_OS_TRANSPORT_SUBJECT is required for Company OS API mode"}, ensure_ascii=False), file=sys.stderr)
            return 2
        if args.once:
            try:
                _wire_inbox_runner()
            except Exception:
                outcome = _recoverable_adapter_failure(
                    "company_os_agent_os_once",
                    failure_stage="runtime_initialization",
                    error_kind="local_runtime_initialization_error",
                )
            else:
                try:
                    outcome = _run_once_company_os(
                        args.company_os_url,
                        auth_value,
                        args.sinria_instance_id,
                        workspace_id=args.workspace_id,
                        member_id=args.member_id,
                        instance_id=args.instance_id,
                    )
                except Exception as exc:
                    outcome = _recoverable_adapter_failure(
                        "company_os_agent_os_once",
                        failure_stage="cloud_adapter",
                        error_kind=_classify_adapter_failure(exc),
                    )
            print(json.dumps(outcome, ensure_ascii=False, indent=2))
            return _worker_outcome_exit_code(outcome)
        _wire_inbox_runner()
        summary = _run_company_os_loop(
            args.company_os_url,
            auth_value,
            args.sinria_instance_id,
            workspace_id=args.workspace_id,
            member_id=args.member_id,
            instance_id=args.instance_id,
            poll_interval=args.poll_interval,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.once and args.supabase_url:
        auth_value = os.environ.get("SINRIA_BRIDGE_TOKEN")
        if not auth_value:
            print(json.dumps({"success": False, "error": "SINRIA_BRIDGE_TOKEN is required for Supabase mode"}, ensure_ascii=False), file=sys.stderr)
            return 2
        try:
            _wire_inbox_runner()
        except Exception:
            outcome = _recoverable_adapter_failure(
                "supabase_agent_os_once",
                failure_stage="runtime_initialization",
                error_kind="local_runtime_initialization_error",
            )
        else:
            try:
                outcome = _run_once_supabase(
                    args.supabase_url,
                    auth_value,
                    args.sinria_instance_id,
                    workspace_id=args.workspace_id,
                    member_id=args.member_id,
                    instance_id=args.instance_id,
                )
            except Exception as exc:
                outcome = _recoverable_adapter_failure(
                    "supabase_agent_os_once",
                    failure_stage="cloud_adapter",
                    error_kind=_classify_adapter_failure(exc),
                )
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return _worker_outcome_exit_code(outcome)

    print(
        json.dumps(
            {
                "success": False,
                "error": "Choose --dry-run, --once --mock-cloud, --once [--company-os-url <url>], or --once --supabase-url <url>. Long-running daemon loop is intentionally not enabled until the cloud adapter is approved.",
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
