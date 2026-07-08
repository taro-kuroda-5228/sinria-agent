"""Verified-trajectory capture — the distillation loop's data intake (P2).

When a turn ends in ``verified_practical_completion``, the final-turn slice
is snapshotted to ``SINRIA_HOME/training/trajectories/`` as local SFT raw
material. Fail-closed by design (data-use policy: body content is opt-in):

* config ``training.capture_verified_trajectories`` defaults **off**;
* only the final turn is captured — earlier conversation, system prompts
  (they embed memory), and synthetic scaffolding never enter training data;
* every text field is redacted (``redact_sensitive_text(force=True)``) and
  the whole capture is rejected if any field still trips
  ``contains_sensitive_text`` (secrets, PHI/PII-like patterns);
* local-only: nothing here uploads anywhere.

See docs/plans/2026-07-06-architecture-centric-agent-os-p2.md.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_sinria_home

from agent.context_share.safety import contains_sensitive_text
from agent.redact import redact_sensitive_text

_SYNTHETIC_MARKERS = (
    "_verify_after_act_synthetic",
    "_empty_recovery_synthetic",
    "_thinking_prefill",
    "_empty_terminal_sentinel",
)


def trajectories_root(home: Optional[Path] = None) -> Path:
    return (home or get_sinria_home()) / "training" / "trajectories"


def _final_turn_slice(messages: list[Any], user_message: str) -> list[dict[str, Any]]:
    """Messages from the turn's initiating user message onward.

    Walk backwards to the last non-synthetic user message whose content
    matches the initiating request; fall back to the last user message.
    """
    start = None
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        if any(m.get(marker) for marker in _SYNTHETIC_MARKERS):
            continue
        start = i
        if str(m.get("content") or "") == str(user_message or ""):
            break
    if start is None:
        return []
    return [m for m in messages[start:] if isinstance(m, dict)]


def _sanitize_turn(message: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Redact one message; return None when it must be excluded."""
    role = message.get("role")
    if role == "system":
        return None
    if any(message.get(marker) for marker in _SYNTHETIC_MARKERS):
        return None
    turn: dict[str, Any] = {"role": role}
    content = message.get("content")
    if content is not None:
        turn["content"] = redact_sensitive_text(str(content), force=True)
    if message.get("name"):
        turn["name"] = str(message["name"])
    tool_calls = message.get("tool_calls")
    if tool_calls:
        cleaned_calls = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                fn = tc.get("function", {}) or {}
                name, args = fn.get("name", ""), fn.get("arguments", "")
            else:  # normalized ToolCall objects
                name = getattr(getattr(tc, "function", tc), "name", "")
                args = getattr(getattr(tc, "function", tc), "arguments", "")
            cleaned_calls.append(
                {"name": str(name), "arguments": redact_sensitive_text(str(args), force=True)}
            )
        turn["tool_calls"] = cleaned_calls
    return turn


def capture_verified_trajectory(
    *,
    messages: list[Any],
    user_message: str,
    final_response: Optional[str],
    session_id: Optional[str],
    model: Optional[str],
    provider: Optional[str],
    tier: str,
    home: Optional[Path] = None,
) -> Optional[Path]:
    """Snapshot a verified turn for local SFT. Returns the path or None."""
    turns = [
        sanitized
        for sanitized in (_sanitize_turn(m) for m in _final_turn_slice(messages, user_message))
        if sanitized is not None
    ]
    if len(turns) < 2:  # need at least the request and one assistant action
        return None

    # Fail-closed: any residual sensitive content rejects the whole capture.
    for turn in turns:
        fields = [turn.get("content") or ""]
        fields.extend(tc.get("arguments", "") for tc in turn.get("tool_calls", []))
        if any(contains_sensitive_text(field) for field in fields):
            return None

    now = datetime.now(timezone.utc)
    payload = {
        "meta": {
            "timestamp": now.isoformat().replace("+00:00", "Z"),
            "session_id": session_id or "",
            "model": model or "",
            "provider": provider or "",
            "tier": tier,
        },
        "turns": turns,
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]

    root = trajectories_root(home)
    day_dir = root / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{digest}.json"
    path.write_text(body, encoding="utf-8")

    index_row = dict(payload["meta"])
    index_row["path"] = str(path.relative_to(root))
    index_row["turns"] = len(turns)
    with (root / "index.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(index_row, ensure_ascii=False, sort_keys=True) + "\n")
    return path


__all__ = ["capture_verified_trajectory", "trajectories_root"]
