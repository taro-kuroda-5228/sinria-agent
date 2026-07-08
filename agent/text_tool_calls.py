"""Text-mode tool calling for models without native function calling.

Architecture-centric P0 (docs/plans/2026-07-06-architecture-centric-agent-os-p0.md,
Task 5): small local models served through bare OpenAI-compatible endpoints
often lack native tool/function-calling. Instead of requiring the model to be
"smart enough" for a provider-side protocol, the architecture defines a plain
text contract — one fenced ``tool_call`` block per call — and parses it into
the same normalized :class:`~agent.transports.types.ToolCall` objects the rest
of the loop already consumes. Downstream validation, JSON repair, and message
building are unchanged and provider-agnostic.

Enabled per install via ``model.text_tool_calls: true`` in config.yaml
(default off). Parsing is additive: it only runs when the response carries no
native tool calls.

Design choices:

* Deterministic ids (``text_call_<digest>``) are assigned at parse time.
  The transports' ``_deterministic_call_id`` fill runs inside provider
  adapters, which this path bypasses — so ids must be set here for tool
  results to pair unambiguously with their calls.
* Malformed blocks (bad JSON, missing/non-string ``name``, non-dict
  ``arguments``) are left in the content, visible to the model and to
  downstream layers — never silently dropped.
"""

from __future__ import annotations

import hashlib
import json
import re

from agent.transports.types import ToolCall

# The contract the model is asked to follow. Injected into the stable
# system-prompt tier when model.text_tool_calls is enabled (fixed at agent
# init, so the prompt-cache invariant holds).
TEXT_TOOL_CALL_GUIDANCE = (
    "Tool calls (text mode): to call a tool, emit exactly one fenced block "
    "per call in this format:\n"
    "```tool_call\n"
    '{"name": "<tool_name>", "arguments": {"<param>": "<value>"}}\n'
    "```\n"
    "Rules: give arguments as a single JSON object; use only tool names from "
    "the available tools; do not describe the call in prose instead of "
    "emitting the block; after emitting the block(s), stop and wait for the "
    "tool result. When the task is done and no tool is needed, reply "
    "normally without any tool_call block."
)

_TOOL_CALL_BLOCK_RE = re.compile(r"```tool_call\s*\n(.*?)\n?```", re.DOTALL)


def parse_text_tool_calls(content: str) -> tuple[list[ToolCall], str]:
    """Extract ``tool_call`` blocks from ``content``.

    Returns ``(calls, cleaned_content)``. Only successfully parsed blocks
    are removed from the content; malformed blocks stay visible.
    """
    if not content or "```tool_call" not in content:
        return [], content

    calls: list[ToolCall] = []
    removed_spans: list[tuple[int, int]] = []
    for match in _TOOL_CALL_BLOCK_RE.finditer(content):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name")
        arguments = data.get("arguments", {})
        if not name or not isinstance(name, str):
            continue
        if not isinstance(arguments, dict):
            continue
        args_json = json.dumps(arguments, ensure_ascii=False)
        digest = hashlib.sha1(
            f"{name}:{args_json}:{len(calls)}".encode("utf-8")
        ).hexdigest()[:12]
        calls.append(
            ToolCall(
                id=f"text_call_{digest}",
                name=name.strip(),
                arguments=args_json,
                provider_data={"source": "text_tool_call"},
            )
        )
        removed_spans.append(match.span())

    if not calls:
        return [], content

    pieces: list[str] = []
    cursor = 0
    for start, end in removed_spans:
        pieces.append(content[cursor:start])
        cursor = end
    pieces.append(content[cursor:])
    cleaned = "".join(pieces).strip()
    return calls, cleaned


__all__ = ["TEXT_TOOL_CALL_GUIDANCE", "parse_text_tool_calls"]
