"""Local Sinria Agent OS task handler registry.

A claimed Agent OS task is dispatched by ``(agentOsId, taskKind)`` to a registered
LOCAL handler. The cloud only routes sanitized metadata; handlers run on the
employee's own local/on-prem Sinria with local context, credentials and tools.

Invariants every handler must keep:
  * No external send/write/delete unless the task policy AND human review allow it.
  * Return SANITIZED result metadata only — never raw email/clinical/customer
    bodies, raw drafts, raw diffs, credentials, or local memory.
  * ``externalActionPerformed`` / ``rawLocalContextStored`` stay False unless the
    handler legitimately (and with approval) did otherwise.

This module is import-safe and has no network/DB dependencies; the daemon injects
the real Sales execution runner via :func:`set_sales_outreach_runner`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sinria_constants import get_sinria_home

logger = logging.getLogger(__name__)

__all__ = [
    "SANDBOX_REQUIRED_AGENT_OS_IDS",
    "LocalExecutionIdentity",
    "register_handler",
    "get_handler",
    "registered_handler_keys",
    "dispatch_agentos_task",
    "execute_sales_outreach_plan_task",
    "set_sales_outreach_runner",
    "execute_company_os_inbox_request_task",
    "execute_line_request_task",
    "execute_company_os_command_center_task",
    "execute_medspot_ops_tick_task",
    "set_inbox_request_runner",
    "register_builtin_agentos_handlers",
]


@dataclass(frozen=True)
class LocalExecutionIdentity:
    """Who is executing: workspace + member + instance (never a cloud actor)."""

    workspace_id: str
    member_id: str
    instance_id: str


Handler = Callable[[dict[str, Any], "LocalExecutionIdentity"], dict[str, Any]]

_HANDLERS: dict[tuple[str, str], Handler] = {}
_AGENT_OS_ALIASES = {
    "sales": "sales_agent_os",
    "service": "service_agent_os",
    "application": "application_agent_os",
}


def _canonical_agent_os_id(agent_os_id: str) -> str:
    return _AGENT_OS_ALIASES.get(agent_os_id, agent_os_id)


def register_handler(agent_os_id: str, task_kind: str, handler: Handler) -> None:
    _HANDLERS[(agent_os_id, task_kind)] = handler


def get_handler(agent_os_id: str, task_kind: str) -> Optional[Handler]:
    return _HANDLERS.get((agent_os_id, task_kind)) or _HANDLERS.get((_canonical_agent_os_id(agent_os_id), task_kind))


def registered_handler_keys() -> list[tuple[str, str]]:
    return sorted(_HANDLERS.keys())


def _field(task: dict[str, Any], *names: str, default: Any = "") -> Any:
    for n in names:
        v = task.get(n)
        if v not in (None, ""):
            return v
    return default


def _pin_safety(result: dict[str, Any]) -> dict[str, Any]:
    """Defense in depth: never let a result silently assert a cloud-side leak."""
    result.setdefault("externalActionPerformed", False)
    result.setdefault("rawLocalContextStored", False)
    result.setdefault("resultRefs", [])
    return result


# ---------------------------------------------------------------------------
# Execution environment (sandbox) policy
# ---------------------------------------------------------------------------

# Agent OS ids whose tasks must execute inside the Workshop (LXD) sandbox on
# the claiming node. Mirrors SANDBOX_REQUIRED_AGENT_OS_IDS in the cloud
# boundary (apps/company-os/lib/cloud-boundary.mjs): the local plane enforces
# the same hard invariant even for envelopes that predate the policy field.
SANDBOX_REQUIRED_AGENT_OS_IDS = ("medevidence", "consent_agent")


def _resolve_execution_environment(task: dict[str, Any]) -> dict[str, Any]:
    """Resolve the task's sandbox requirement with healthcare hard-pinning."""
    policy = task.get("policy") or {}
    env = policy.get("executionEnvironment") if isinstance(policy, dict) else None
    if not isinstance(env, dict):
        env = {}
    agent_os_id = _canonical_agent_os_id(str(_field(task, "agentOsId", "agent_os_id")))
    mandatory = agent_os_id in SANDBOX_REQUIRED_AGENT_OS_IDS

    sandbox = str(env.get("sandbox") or ("workshop" if mandatory else "none"))
    fallback = env.get("unsandboxedFallbackAllowed")
    fallback = fallback if isinstance(fallback, bool) else not mandatory
    if mandatory:
        sandbox = "workshop"
        fallback = False

    resolved: dict[str, Any] = {
        "sandbox": sandbox,
        "unsandboxedFallbackAllowed": fallback,
    }
    name = env.get("workshopName")
    if name:
        resolved["workshopName"] = str(name)
    return resolved


def _workshop_available() -> bool:
    """Best-effort check that this node can execute inside Workshop."""
    try:
        from tools.environments.workshop import find_workshop

        return find_workshop() is not None
    except Exception:
        import shutil

        return bool(shutil.which("workshop"))


def _resolve_workshop_name(env_req: dict[str, Any]) -> str:
    """Resolve the target workshop: task policy first, then node defaults."""
    return str(
        env_req.get("workshopName")
        or os.environ.get("SINRIA_WORKSHOP_NAME")
        or os.environ.get("TERMINAL_WORKSHOP_NAME")
        or ""
    )


@contextmanager
def _sandboxed_terminal_env(env_req: dict[str, Any]):
    """Point terminal execution at the Workshop sandbox for this task.

    The terminal tool re-reads TERMINAL_ENV / TERMINAL_WORKSHOP_NAME on every
    call, so scoping the env vars to the handler run routes any command the
    handler executes through the sandbox. Restored on exit even on failure.
    """
    if env_req.get("sandbox") != "workshop":
        yield
        return
    saved = {
        key: os.environ.get(key)
        for key in ("TERMINAL_ENV", "TERMINAL_WORKSHOP_NAME")
    }
    os.environ["TERMINAL_ENV"] = "workshop"
    name = _resolve_workshop_name(env_req)
    if name:
        os.environ["TERMINAL_WORKSHOP_NAME"] = name
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def dispatch_agentos_task(
    task: dict[str, Any], identity: "LocalExecutionIdentity"
) -> dict[str, Any]:
    """Route a claimed task to its local handler, or report a recoverable miss."""
    agent_os_id = _field(task, "agentOsId", "agent_os_id")
    task_kind = _field(task, "taskKind", "task_kind")
    handler = get_handler(agent_os_id, task_kind)
    if not handler:
        return _pin_safety(
            {
                "status": "failed_recoverable",
                "sanitizedSummary": f"No local handler registered for {agent_os_id}:{task_kind}",
            }
        )

    env_req = _resolve_execution_environment(task)
    if env_req["sandbox"] == "workshop":
        blocked_reason = None
        if not _workshop_available():
            blocked_reason = "the workshop CLI is unavailable on this node"
        elif not _resolve_workshop_name(env_req):
            # Fail closed BEFORE the handler runs — otherwise its first
            # terminal call errors out mid-task with a confusing message.
            blocked_reason = (
                "no workshop name is configured (set the task's workshopName "
                "or SINRIA_WORKSHOP_NAME on this node)"
            )
        if blocked_reason:
            if not env_req["unsandboxedFallbackAllowed"]:
                return _pin_safety(
                    {
                        "status": "failed_recoverable",
                        "sanitizedSummary": (
                            f"workshop sandbox required for {agent_os_id}:{task_kind} "
                            f"but {blocked_reason}"
                        ),
                    }
                )
            logger.warning(
                "Workshop sandbox requested for %s:%s but %s; policy permits "
                "unsandboxed fallback on this node",
                agent_os_id,
                task_kind,
                blocked_reason,
            )
            env_req = {"sandbox": "none", "unsandboxedFallbackAllowed": True}

    with _sandboxed_terminal_env(env_req):
        return _pin_safety(handler(task, identity))


# ---------------------------------------------------------------------------
# Sales Agent OS — outreach plan (the first concrete vertical handler)
# ---------------------------------------------------------------------------

# The daemon owns the real, DB-backed discover→draft execution. It injects that
# runner here so the registry stays import-safe and unit-testable without hitting
# Gmail / Google / Search / Supabase.
_SALES_RUNNER: Optional[Callable[[dict[str, Any], "LocalExecutionIdentity"], dict[str, Any]]] = None


def set_sales_outreach_runner(
    runner: Optional[Callable[[dict[str, Any], "LocalExecutionIdentity"], dict[str, Any]]],
) -> None:
    global _SALES_RUNNER
    _SALES_RUNNER = runner


def execute_sales_outreach_plan_task(
    task: dict[str, Any], identity: "LocalExecutionIdentity"
) -> dict[str, Any]:
    """Research targets and create review-gated drafts. No external send.

    Uses local credentials/context only and respects ``maxTotal`` / ``offer`` /
    ``instruction``. Already-contacted targets are excluded by the injected
    runner (it has the local Sales DB). Without a runner (unit tests / dry runs)
    it returns a sanitized PLAN with no side effects.
    """
    payload = task.get("payload") or {}
    instruction = str(_field(payload, "instruction") or _field(task, "instruction")).strip()
    if not instruction:
        return _pin_safety(
            {"status": "failed_recoverable", "sanitizedSummary": "missing instruction"}
        )
    try:
        max_total = int(payload.get("maxTotal") or 10)
    except (TypeError, ValueError):
        max_total = 10

    if _SALES_RUNNER is None:
        return _pin_safety(
            {
                "status": "waiting_review",
                "sanitizedSummary": (
                    f"営業候補リサーチ＋下書き作成を計画（最大{max_total}件・外部送信なし・要レビュー）"
                ),
                "engine": "sinria_native",
            }
        )

    raw = _SALES_RUNNER(payload, identity) or {}
    draft_ids = [d for d in (raw.get("draft_ids") or []) if d]
    summary = raw.get("answer_summary") or "営業下書きを作成しました（外部送信なし・要レビュー）"
    return _pin_safety(
        {
            "status": "waiting_review",
            "sanitizedSummary": summary,
            "resultRefs": [
                {"kind": "draft", "refId": str(d), "title": "営業下書き"} for d in draft_ids
            ],
        }
    )


# ---------------------------------------------------------------------------
# Service Agent OS — triage stub (proves routing is not Sales-only)
# ---------------------------------------------------------------------------


def execute_service_triage_task(
    task: dict[str, Any], identity: "LocalExecutionIdentity"
) -> dict[str, Any]:
    payload = task.get("payload") or {}
    summary = str(payload.get("summary") or "未対応の問い合わせを安全に要約しました")
    return _pin_safety(
        {
            "status": "waiting_review",
            "sanitizedSummary": f"トリアージ案を作成（外部送信なし・要レビュー）: {summary[:120]}",
            # No raw customer body ever leaves the local plane.
        }
    )


# ---------------------------------------------------------------------------
# MedSpot — metadata-only operational tick
# ---------------------------------------------------------------------------

_MEDSPOT_OPS_TICK_KIND = "medspot.ops.tick"
_MEDSPOT_OPS_CAPABILITY = "medspot.ops.metadata_tick"
_MEDSPOT_UNSAFE_METADATA_PARTS = (
    "patient", "clinical", "diagnos", "medical", "health", "email", "phone",
    "address", "body", "content", "draft", "secret", "credential", "password",
    "token", "api_key", "api-key", "raw",
)


def execute_medspot_ops_tick_task(
    task: dict[str, Any], identity: "LocalExecutionIdentity"
) -> dict[str, Any]:
    """Dispatch a sanitized MedSpot tick; never performs approval or side effects."""
    policy = task.get("policy") or {}
    if task.get("taskKind", task.get("task_kind")) != _MEDSPOT_OPS_TICK_KIND:
        return _pin_safety({"status": "failed_recoverable", "sanitizedSummary": "MedSpot task kind mismatch"})
    if policy.get("requiredCapability") != _MEDSPOT_OPS_CAPABILITY:
        return _pin_safety({"status": "failed_recoverable", "sanitizedSummary": "MedSpot capability mismatch"})
    metadata = task.get("payload") or {}
    if not isinstance(metadata, dict):
        return _pin_safety({"status": "failed_recoverable", "sanitizedSummary": "metadata-only payload required"})
    for key, value in metadata.items():
        if any(part in str(key).lower() for part in _MEDSPOT_UNSAFE_METADATA_PARTS):
            return _pin_safety({"status": "failed_recoverable", "sanitizedSummary": "unsafe MedSpot metadata rejected"})
        if isinstance(value, (dict, tuple, set)) or not isinstance(value, (str, int, float, bool, type(None), list)):
            return _pin_safety({"status": "failed_recoverable", "sanitizedSummary": "metadata-only scalar payload required"})
        if isinstance(value, list) and not all(isinstance(item, (str, int, float, bool, type(None))) for item in value):
            return _pin_safety({"status": "failed_recoverable", "sanitizedSummary": "metadata-only scalar payload required"})
    signal_count = metadata.get("signalCount", 0)
    # bool is an int subclass, so reject it explicitly. Bounds are deliberately
    # finite at the local boundary to prevent malformed adapter payloads from
    # becoming unbounded work or a handler exception.
    if isinstance(signal_count, bool) or not isinstance(signal_count, int) or signal_count < 0 or signal_count > 100_000:
        return _pin_safety({"status": "failed_recoverable", "sanitizedSummary": "signalCount must be an integer between 0 and 100000"})
    return _pin_safety({
        "status": "completed",
        "sanitizedSummary": "MedSpot metadata tick dispatched locally; no external action performed",
        "observedEventCount": signal_count,
        "proposals": [],
        "humanApprovalRequired": True,
    })


# ---------------------------------------------------------------------------
# MedEvidence / Consent — clinical stubs with stricter authority
# ---------------------------------------------------------------------------


def execute_medevidence_research_task(
    task: dict[str, Any], identity: "LocalExecutionIdentity"
) -> dict[str, Any]:
    return _pin_safety(
        {
            "status": "waiting_review",
            "sanitizedSummary": "エビデンス調査の要約案を作成（患者識別子なし・physicianレビュー必須）",
            "requiredAuthority": "physician",
            "humanApprovalRequired": True,
        }
    )


def execute_consent_draft_review_task(
    task: dict[str, Any], identity: "LocalExecutionIdentity"
) -> dict[str, Any]:
    return _pin_safety(
        {
            "status": "waiting_review",
            "sanitizedSummary": "同意文書ドラフトのレビュー観点を整理（患者識別子なし・physicianレビュー必須）",
            "requiredAuthority": "physician",
            "humanApprovalRequired": True,
        }
    )


# ---------------------------------------------------------------------------
# Company OS — generic inbox request (Stage 1「タスク依頼→自律完了」の実行面)
# ---------------------------------------------------------------------------

# The bridge worker injects the real runner (an async-across-ticks gateway
# runner — see sinria_company_os_inbox_runner). The registry stays import-safe:
# without a runner the handler reports a recoverable miss instead of guessing.
_INBOX_REQUEST_RUNNER: Optional[Callable[[dict[str, Any], "LocalExecutionIdentity"], dict[str, Any]]] = None

# Keep this fixed envelope in lockstep with
# apps/company-os/lib/command-center.ts. A generic sanitizer cannot prove that
# names, patient IDs, dates, or clinical details were removed, so this exact
# placeholder is the only clinical Command Center instruction permitted in the
# shared metadata plane. It is never sent to a model/runner.
COMMAND_CENTER_CLINICAL_SUMMARY_PLACEHOLDER = (
    "(臨床・患者情報を含む可能性があるため、具体的内容は保存しません。"
    "ローカルSinriaで再入力・承認してください)"
)


def set_inbox_request_runner(
    runner: Optional[Callable[[dict[str, Any], "LocalExecutionIdentity"], dict[str, Any]]],
) -> None:
    global _INBOX_REQUEST_RUNNER
    _INBOX_REQUEST_RUNNER = runner


def execute_company_os_inbox_request_task(
    task: dict[str, Any], identity: "LocalExecutionIdentity"
) -> dict[str, Any]:
    """Execute a free-text Sheets-Inbox request via the injected runner.

    Approval semantics (the Stage 1 gate):
      - ``in_progress`` passes through untouched (the run spans multiple ticks).
      - A runner ``completed`` stays completed when the local run reached a safe
        terminal result without requesting a side effect. The task-level
        ``humanApprovalRequired`` policy gates side effects; it must not turn
        every safe local completion into an approval loop.
      - ``reviewRequested`` from the runner (tool safety gate or the final
        ``SINRIA_TASK_STATUS: waiting_review`` marker) escalates to review.
    """
    instruction = str(
        _field(task, "instruction", "instruction_summary") or _field(task, "title")
    ).strip()
    if not instruction:
        return _pin_safety(
            {"status": "failed_recoverable", "sanitizedSummary": "missing instruction"}
        )
    if _INBOX_REQUEST_RUNNER is None:
        return _pin_safety(
            {
                "status": "failed_recoverable",
                "sanitizedSummary": "no inbox runner configured on this node",
            }
        )

    raw = _INBOX_REQUEST_RUNNER(task, identity) or {}
    status = str(raw.get("status") or "failed_recoverable")
    result: dict[str, Any] = {
        "status": status,
        "sanitizedSummary": str(raw.get("sanitizedSummary") or "inbox request processed"),
        "externalActionPerformed": raw.get("externalActionPerformed") is True,
        "rawLocalContextStored": raw.get("rawLocalContextStored") is True,
    }
    if raw.get("runRef"):
        result["runRef"] = raw["runRef"]
    if raw.get("resultRefs"):
        result["resultRefs"] = raw["resultRefs"]
    if raw.get("reviewRequested") is True:
        result["reviewRequested"] = True

    policy = task.get("policy") or {}
    payload = task.get("payload") or {}
    approval_required = policy.get("humanApprovalRequired", True) is not False
    approval_granted = payload.get("humanApprovalGranted") is True
    external_action_allowed = policy.get("externalActionAllowed") is True

    # Preserve a runner's side-effect signal and fail closed instead of letting
    # a nominally completed result hide an action lacking policy or approval.
    if result["externalActionPerformed"] and (
        not external_action_allowed or (approval_required and not approval_granted)
    ):
        result.update(
            status="failed_recoverable",
            sanitizedSummary="Runner reported an external action without valid policy and approval",
            reviewRequested=True,
        )
        return _pin_safety(result)

    if status == "in_progress":
        return _pin_safety(result)

    if status == "completed" and raw.get("reviewRequested") is True:
        result["status"] = "waiting_review"
    return _pin_safety(result)


_LINE_TASK_REF = re.compile(r"^local://line/task-intake/([0-9a-f]{64})$")


def execute_line_request_task(
    task: dict[str, Any], identity: "LocalExecutionIdentity"
) -> dict[str, Any]:
    """Resolve private LINE evidence on the addressed node, then run locally.

    The cloud task carries only a sanitized summary and an opaque ``local://``
    reference. The original request never enters the cloud result envelope.
    """
    raw_payload = task.get("payload")
    payload: dict[str, Any] = dict(raw_payload) if isinstance(raw_payload, dict) else {}
    source_ref = str(payload.get("sourceRef") or "")
    match = _LINE_TASK_REF.fullmatch(source_ref)
    if match is None:
        return _pin_safety({
            "status": "failed_recoverable",
            "sanitizedSummary": "LINE task evidence reference is invalid",
        })

    root = get_sinria_home() / "private" / "line" / "task-intake"
    evidence_path = root / f"{match.group(1)}.json"
    try:
        if evidence_path.is_symlink() or not evidence_path.is_file():
            raise OSError("evidence unavailable")
        if evidence_path.resolve().parent != root.resolve():
            raise OSError("evidence escaped private root")
        if evidence_path.stat().st_mode & 0o077:
            raise OSError("evidence permissions are not private")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        raw_text = evidence.get("text") if isinstance(evidence, dict) else None
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ValueError("evidence text unavailable")
    except (OSError, ValueError, json.JSONDecodeError):
        return _pin_safety({
            "status": "failed_recoverable",
            "sanitizedSummary": "LINE task evidence is unavailable on this node",
        })

    local_task = dict(task)
    local_task["instruction"] = raw_text.strip()
    local_task["payload"] = dict(payload)
    return execute_company_os_inbox_request_task(local_task, identity)


def execute_company_os_command_center_task(
    task: dict[str, Any], identity: "LocalExecutionIdentity"
) -> dict[str, Any]:
    """Dispatch sanitized Command Center work; fail closed for clinical input.

    Safe metadata-only instructions reuse the existing local inbox runner.
    Clinical requests carry only a fixed placeholder; that placeholder cannot
    be executed meaningfully and must never be sent to a model. Park it at an
    owner review/local re-entry handoff instead of retrying indefinitely.
    """
    instruction = str(
        _field(task, "instruction", "instruction_summary") or _field(task, "title")
    ).strip()
    if instruction == COMMAND_CENTER_CLINICAL_SUMMARY_PLACEHOLDER:
        return _pin_safety(
            {
                "status": "waiting_review",
                "sanitizedSummary": (
                    "臨床・患者情報の具体的内容は共有面に保存せず、"
                    "ローカルSinriaで再入力してowner承認してください（モデル呼び出し・外部送信なし）"
                ),
                "requiredAuthority": "owner",
                "humanApprovalRequired": True,
                "externalEgress": False,
            }
        )
    return execute_company_os_inbox_request_task(task, identity)


def register_builtin_agentos_handlers() -> None:
    """Register the built-in vertical handlers (idempotent)."""
    register_handler("sales_agent_os", "sales_outreach_plan", execute_sales_outreach_plan_task)
    register_handler("sales_agent_os", "medspot.ops.tick", execute_medspot_ops_tick_task)
    register_handler("service_agent_os", "service_triage", execute_service_triage_task)
    register_handler("medevidence", "evidence_research", execute_medevidence_research_task)
    register_handler("consent_agent", "consent_draft_review", execute_consent_draft_review_task)
    register_handler("company_os", "inbox_request", execute_company_os_inbox_request_task)
    # ``inbox_execution`` is the task-kind spelling used by the Command Center /
    # Agent OS transport route when it routes a claimed inbox task for local
    # execution. It is the same local vertical as ``inbox_request``; register the
    # alias so an approved task does not fail closed with "no local handler".
    register_handler("company_os", "inbox_execution", execute_company_os_inbox_request_task)
    register_handler("company_os", "command_center_task", execute_company_os_command_center_task)
    register_handler("application_agent_os", "line_request", execute_line_request_task)


# Auto-register on import so dispatch/get_handler work out of the box.
register_builtin_agentos_handlers()
