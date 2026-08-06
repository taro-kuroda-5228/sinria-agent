"""Hybrid strategist routing — a large model plans, the executor acts.

Design: docs/plans/2026-07-07-hybrid-strategist-routing.md. The main-loop
model never changes; the strategist (``model.strategist_model``, e.g.
claude-fable-5) is side-called at two checkpoints — one plan for complex
practical tasks, corrective guidance when verify-after-act fires. Packets
are token-lean and sanitized (task statement, todo state, capability
metadata, tool names — never full transcripts). Strategist failures never
block the turn: every public entry point degrades to None.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

from hermes_constants import get_sinria_home

logger = logging.getLogger(__name__)

DEFAULT_MAX_CALLS_PER_TASK = 3
PACKET_CHAR_CAP = 6000
PLAN_MAX_TOKENS = 700
CORRECTION_MAX_TOKENS = 500
_PLAN_LENGTH_THRESHOLD = 240
# Provider assumed when ``model.strategist_provider`` is unset.  Shared by the
# client resolver and the same-route check so the two cannot drift apart.
DEFAULT_STRATEGIST_PROVIDER = "anthropic"

_PLAN_SYSTEM_PROMPT = (
    "You are the strategist for a smaller executor model that will do the "
    "actual work with tools. Produce a concise, concrete plan: numbered "
    "steps (at most 8), the main risk, and how the executor should verify "
    "completion. Plain text only. Do not do the task yourself."
)
_CORRECTION_SYSTEM_PROMPT = (
    "You are the strategist for a smaller executor model. It claimed the "
    "task was complete without citing verification. From the evidence, "
    "give short corrective guidance: what to verify with which tool, and "
    "the most likely gap to fix. At most 6 sentences. Plain text only."
)

_MULTI_STEP_MARKERS = (
    " then ", " and then ", "after that", " steps", "step 1", "step 2",
    "1.", "2.", "- [ ]",
    "そして", "してから", "した後", "したあと", "次に", "最後に",
    "手順", "それぞれ", "すべて", "全部",
)


def strategist_events_path(home: Optional[Path] = None) -> Path:
    return (home or get_sinria_home()) / "corrections" / "strategist_events.jsonl"


def record_strategist_event(
    *,
    event: str,
    model: Optional[str],
    cause_kind: Optional[str] = None,
    session_id: Optional[str] = None,
    path: Optional[Path] = None,
) -> Optional[Path]:
    """Append one sanitized metadata row. Best-effort: never raises."""
    try:
        row: dict[str, Any] = {
            "event": event,
            "model": model or "",
            "timestamp": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        if cause_kind:
            row["cause_kind"] = cause_kind
        if session_id:
            row["session_id"] = session_id
        target = path or strategist_events_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return target
    except Exception as exc:
        logger.warning("strategist telemetry failed: %s", exc)
        return None


def configure_strategist(agent: Any, model_cfg: Any, behavior_cfg: Any) -> None:
    """Read strategist config onto the agent. Unset model ⇒ feature off."""
    _model = model_cfg if isinstance(model_cfg, dict) else {}
    _behavior = behavior_cfg if isinstance(behavior_cfg, dict) else {}
    agent.strategist_model = str(_model.get("strategist_model") or "").strip() or None
    agent.strategist_provider = (
        str(_model.get("strategist_provider") or "").strip() or None
    )
    _strategist = _behavior.get("strategist")
    _strategist = _strategist if isinstance(_strategist, dict) else {}
    try:
        agent.strategist_max_calls = max(
            0, int(_strategist.get("max_calls_per_task", DEFAULT_MAX_CALLS_PER_TASK))
        )
    except (TypeError, ValueError):
        agent.strategist_max_calls = DEFAULT_MAX_CALLS_PER_TASK
    agent._strategist_calls_used = 0
    agent._strategist_warned = False


def _effective_strategist_provider(agent: Any) -> str:
    return str(getattr(agent, "strategist_provider", None) or DEFAULT_STRATEGIST_PROVIDER)


def strategist_enabled(agent: Any) -> bool:
    """False when unconfigured, or when it would just re-ask the executor.

    Pointing the strategist at the main runtime doubles that provider's usage
    per turn to get a second opinion from the same model on the same account
    — no planning diversity, twice the quota burn.  The comparison uses the
    *live* runtime, so a mid-session failover that lands the executor on the
    strategist's own route stops the side-call too.
    """
    model = str(getattr(agent, "strategist_model", None) or "")
    if not model:
        return False
    same_model = model == str(getattr(agent, "model", None) or "")
    same_provider = _effective_strategist_provider(agent) == str(
        getattr(agent, "provider", None) or ""
    )
    return not (same_model and same_provider)


def should_request_plan(user_message: Any, *, tools_available: bool) -> bool:
    """Code-only complexity heuristic. Chit-chat and Q&A never plan."""
    if not tools_available:
        return False
    from agent.correction_loop.outcome_gap import classify_goal
    from agent.correction_loop.outcome_gap import _text as _extract_message_text

    task_text = _clip_task_text(_extract_message_text(user_message), 2000)
    lowered = task_text.lower()
    has_multi_step = any(marker in lowered for marker in _MULTI_STEP_MARKERS)
    if classify_goal(task_text) != "practical_action" and not has_multi_step:
        return False
    if len(task_text) >= _PLAN_LENGTH_THRESHOLD:
        return True
    return has_multi_step


def consume_strategist_budget(agent: Any) -> bool:
    used = int(getattr(agent, "_strategist_calls_used", 0) or 0)
    cap = int(
        getattr(agent, "strategist_max_calls", DEFAULT_MAX_CALLS_PER_TASK) or 0
    )
    if used >= cap:
        return False
    agent._strategist_calls_used = used + 1
    return True


def _clip(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + "\n…[truncated]"


def _clip_task_text(text: str, cap: int) -> str:
    """Bound gateway-expanded task text while retaining the real request tail.

    Auto-loaded skill instructions precede the user's text in legacy gateway
    payloads. A head-only clip therefore hid the request from the strategist.
    """
    if len(text) <= cap:
        return text
    marker = "\n…[auto-loaded context truncated; user request tail follows]\n"
    remaining = max(0, cap - len(marker))
    head = remaining // 2
    tail = remaining - head
    return text[:head] + marker + (text[-tail:] if tail else "")


def _tool_names(agent: Any) -> List[str]:
    names: List[str] = []
    for tool in getattr(agent, "tools", None) or []:
        if isinstance(tool, dict):
            name = (tool.get("function") or {}).get("name")
            if name:
                names.append(str(name))
    return sorted(set(names))


def _shared_packet_parts(agent: Any, user_message: Any) -> List[str]:
    from agent.correction_loop.outcome_gap import _text as _extract_message_text

    _extracted = _extract_message_text(user_message)
    _task_text = _extracted if _extracted else str(user_message or "")
    parts = [f"Task from user:\n{_clip_task_text(_task_text, 2000)}"]
    todo = getattr(agent, "_todo_store", None)
    try:
        if todo is not None and todo.has_items():
            block = todo.format_for_injection()
            if block:
                parts.append(f"Current todo state:\n{_clip(block, 1200)}")
    except Exception:
        pass
    profile = getattr(agent, "capability_profile", None)
    parts.append(
        "Executor: model={} tier={} max_iterations={}".format(
            getattr(agent, "model", "") or "?",
            getattr(profile, "tier", "?"),
            getattr(profile, "max_iterations_cap", "?"),
        )
    )
    names = _tool_names(agent)
    if names:
        parts.append("Available tools: " + ", ".join(names[:60]))
    return parts


def _build_plan_packet(agent: Any, user_message: Any) -> str:
    return _clip("\n\n".join(_shared_packet_parts(agent, user_message)), PACKET_CHAR_CAP)


def _build_correction_packet(
    agent: Any, user_message: Any, final_response: Any, cause_kind: str
) -> str:
    parts = _shared_packet_parts(agent, user_message)
    parts.append(f"Detected gap: {cause_kind}")
    parts.append(
        "Executor's unverified final answer (tail):\n"
        + _clip(str(final_response or "")[-1200:], 1200)
    )
    return _clip("\n\n".join(parts), PACKET_CHAR_CAP)


def _resolve_strategist_client(agent: Any):
    """Secondary client, auxiliary-client pattern. Raises on failure.

    Returns (client, resolved_model) where resolved_model is the
    provider-normalized model name from resolve_provider_client.
    """
    from agent.auxiliary_client import resolve_provider_client

    provider = _effective_strategist_provider(agent)
    client, resolved_model = resolve_provider_client(
        provider, model=getattr(agent, "strategist_model", None)
    )
    if client is None:
        raise RuntimeError(f"no strategist client for provider {provider}")
    return client, resolved_model


def _strategist_boundary_agent(
    agent: Any, *, client: Any, provider: str, model: str
) -> SimpleNamespace:
    """Create an egress-policy view for the actual strategist destination."""

    attributes = {
        "provider": provider,
        "model": model,
        "base_url": str(getattr(client, "base_url", "") or ""),
        "session_id": str(getattr(agent, "session_id", "") or "strategist"),
    }
    for name in (
        "sinria_egress_config",
        "sinria_boundary_config",
        "sinria_egress_audit_path",
    ):
        if hasattr(agent, name):
            attributes[name] = getattr(agent, name)
    return SimpleNamespace(**attributes)


def _call_strategist(
    agent: Any, system_prompt: str, packet: str, max_tokens: int
) -> Optional[str]:
    model = getattr(agent, "strategist_model", None)
    if not model:
        return None
    try:
        client, resolved_model = _resolve_strategist_client(agent)
        transport_model = resolved_model or model
        provider = _effective_strategist_provider(agent)
        payload = {
            "model": transport_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": packet},
            ],
            "max_tokens": max_tokens,
        }
        from agent.sinria_egress import prepare_model_provider_payload

        boundary_agent = _strategist_boundary_agent(
            agent,
            client=client,
            provider=provider,
            model=transport_model,
        )
        prepared_payload = prepare_model_provider_payload(boundary_agent, payload)
        response = client.chat.completions.create(**prepared_payload)
        text = (response.choices[0].message.content or "").strip()
        return text or None
    except Exception as exc:
        if not getattr(agent, "_strategist_warned", False):
            agent._strategist_warned = True
            logger.warning(
                "Strategist side-call failed — continuing single-model: %s", exc
            )
        record_strategist_event(
            event="error", model=model, session_id=getattr(agent, "session_id", None)
        )
        return None


def maybe_request_plan(agent: Any, user_message: Any) -> Optional[str]:
    """One plan side-call for complex practical tasks. Never raises."""
    try:
        if not strategist_enabled(agent):
            return None
        if not should_request_plan(
            user_message, tools_available=bool(getattr(agent, "tools", None))
        ):
            return None
        if not consume_strategist_budget(agent):
            record_strategist_event(
                event="budget_exhausted",
                model=agent.strategist_model,
                session_id=getattr(agent, "session_id", None),
            )
            return None
        plan = _call_strategist(
            agent, _PLAN_SYSTEM_PROMPT, _build_plan_packet(agent, user_message),
            PLAN_MAX_TOKENS,
        )
        if plan:
            record_strategist_event(
                event="plan",
                model=agent.strategist_model,
                session_id=getattr(agent, "session_id", None),
            )
        return plan
    except Exception as exc:
        logger.warning("maybe_request_plan failed: %s", exc)
        return None


def maybe_request_correction(
    agent: Any,
    *,
    user_message: Any,
    final_response: Any,
    cause_kind: str = "verification_gap",
) -> Optional[str]:
    """Corrective-guidance side-call for the verify-after-act nudge. Never raises."""
    try:
        if not strategist_enabled(agent):
            return None
        if not consume_strategist_budget(agent):
            record_strategist_event(
                event="budget_exhausted",
                model=agent.strategist_model,
                session_id=getattr(agent, "session_id", None),
            )
            return None
        guidance = _call_strategist(
            agent,
            _CORRECTION_SYSTEM_PROMPT,
            _build_correction_packet(agent, user_message, final_response, cause_kind),
            CORRECTION_MAX_TOKENS,
        )
        if guidance:
            record_strategist_event(
                event="escalate",
                model=agent.strategist_model,
                cause_kind=cause_kind,
                session_id=getattr(agent, "session_id", None),
            )
        return guidance
    except Exception as exc:
        logger.warning("maybe_request_correction failed: %s", exc)
        return None


__all__ = [
    "DEFAULT_MAX_CALLS_PER_TASK",
    "CORRECTION_MAX_TOKENS",
    "PACKET_CHAR_CAP",
    "PLAN_MAX_TOKENS",
    "configure_strategist",
    "consume_strategist_budget",
    "maybe_request_correction",
    "maybe_request_plan",
    "record_strategist_event",
    "should_request_plan",
    "strategist_enabled",
    "strategist_events_path",
]
