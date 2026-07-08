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

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Optional

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


def register_builtin_agentos_handlers() -> None:
    """Register the built-in vertical handlers (idempotent)."""
    register_handler("sales_agent_os", "sales_outreach_plan", execute_sales_outreach_plan_task)
    register_handler("service_agent_os", "service_triage", execute_service_triage_task)
    register_handler("medevidence", "evidence_research", execute_medevidence_research_task)
    register_handler("consent_agent", "consent_draft_review", execute_consent_draft_review_task)


# Auto-register on import so dispatch/get_handler work out of the box.
register_builtin_agentos_handlers()
