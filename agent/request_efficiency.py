"""Pure request-efficiency policies shared by streaming transports.

The model-provider payload itself is never logged or persisted here.  We only
walk already-built request containers and count text characters up to a small
ceiling so retry policy can avoid multiplying very large uploads.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any


# Roughly 30K and 75K input tokens at the common ~4 chars/token heuristic.
# These are deliberately conservative: a repeated request also re-sends tool
# schemas and provider framing that the estimator does not attempt to price.
LARGE_REQUEST_CHAR_THRESHOLD = 120_000
VERY_LARGE_REQUEST_CHAR_THRESHOLD = 300_000


def _dual_env(suffix: str):
    """Read ``SINRIA_<suffix>`` with a legacy ``HERMES_<suffix>`` fallback.

    Matches the repo-wide ``SINRIA_* or HERMES_*`` convention (SINRIA_CLI_NAME,
    SINRIA_ACCEPT_HOOKS, ...) so new operator knobs are Sinria-branded while
    old HERMES_ overrides keep working during the rename.
    """
    value = os.getenv(f"SINRIA_{suffix}")
    if value not in (None, ""):
        return value
    return os.getenv(f"HERMES_{suffix}")


def approximate_request_chars(
    value: Any, *, limit: int = VERY_LARGE_REQUEST_CHAR_THRESHOLD
) -> int:
    """Count nested text characters without serializing or retaining payloads.

    Only request-shaped containers and string/byte leaves are counted.  The
    walk is iterative, cycle-safe, and capped because retry selection only
    needs to know which threshold was crossed.
    """

    cap = max(1, int(limit))
    total = 0
    stack = [value]
    seen: set[int] = set()

    while stack and total < cap:
        item = stack.pop()
        if isinstance(item, str):
            total += min(len(item), cap - total)
            continue
        if isinstance(item, (bytes, bytearray, memoryview)):
            total += min(len(item), cap - total)
            continue

        if isinstance(item, Mapping):
            item_id = id(item)
            if item_id in seen:
                continue
            seen.add(item_id)
            stack.extend(item.values())
            continue

        if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            item_id = id(item)
            if item_id in seen:
                continue
            seen.add(item_id)
            stack.extend(item)

    return total


def resolve_stream_retry_budget(
    api_kwargs: Mapping[str, Any],
    *,
    default_retries: int = 2,
    large_retries: int = 1,
    very_large_retries: int = 0,
    env_var: str = "HERMES_STREAM_RETRIES",
) -> int:
    """Return retries after the first stream attempt.

    Operator overrides remain valid for ordinary and large requests, but the
    very-large cap is absolute. Re-uploading a very-large payload after an
    ambiguous timeout is the dominant latency source and may duplicate work.
    """

    default = max(0, int(default_retries))
    large = max(0, int(large_retries))
    very_large = max(0, int(very_large_retries))
    estimated = approximate_request_chars(api_kwargs)

    raw_override = (
        _dual_env(env_var.removeprefix("SINRIA_").removeprefix("HERMES_"))
        if env_var.startswith(("SINRIA_", "HERMES_"))
        else os.getenv(env_var)
    )
    if raw_override not in (None, ""):
        try:
            override = max(0, int(raw_override))
        except (TypeError, ValueError):
            override = default
        if estimated >= VERY_LARGE_REQUEST_CHAR_THRESHOLD:
            return min(override, very_large)
        return override

    if estimated >= VERY_LARGE_REQUEST_CHAR_THRESHOLD:
        return min(default, very_large)
    if estimated >= LARGE_REQUEST_CHAR_THRESHOLD:
        return min(default, large)
    return default


def cap_oversized_history_tool_results(
    messages,
    *,
    protect_last_n: int = 20,
    max_chars: int = 12_000,
    head_chars: int = 3_000,
    tail_chars: int = 1_500,
):
    """Deterministically shrink oversized OLD tool-result contents for the wire.

    Long agentic sessions accumulate large tool results (file reads, searches,
    skill views) that are re-sent verbatim on every turn — in a sampled
    session the tool results were ~66% of the history. LLM compression is the
    intended mitigation, but it depends on an external auxiliary model, so it
    does not run when that model is unavailable or when the confidentiality
    egress boundary blocks sending confidential context out. With nothing to
    bound history in those cases, requests grow into the very-large band and
    stall the provider.

    This is a no-egress safety net: it rewrites only the *wire copy* of
    tool-result messages older than the protected tail, keeping a head+tail
    excerpt and an explicit marker. The full result stays in the local session
    (only the transmitted payload is trimmed). Recent turns, non-tool messages,
    structured (non-string) content, and already-small results are left
    untouched, and no messages are dropped, so tool_call/result pairing is
    preserved.

    ``SINRIA_WIRE_TOOL_RESULT_CAP`` (legacy ``HERMES_WIRE_TOOL_RESULT_CAP``)
    overrides ``max_chars`` at runtime; set it to ``0`` to disable. Returns the
    input list unchanged (same object) when nothing is trimmed.
    """
    raw = _dual_env("WIRE_TOOL_RESULT_CAP")
    if raw not in (None, ""):
        try:
            max_chars = max(0, int(raw))
        except (TypeError, ValueError):
            pass
    if max_chars <= 0:
        return messages

    head_chars = max(0, int(head_chars))
    tail_chars = max(0, int(tail_chars))
    if head_chars + tail_chars >= max_chars:
        # Degenerate config would not actually shrink anything.
        return messages

    n = len(messages)
    if n <= protect_last_n:
        return messages
    cutoff = n - max(0, protect_last_n)

    out = None
    for i in range(cutoff):
        m = messages[i]
        if not isinstance(m, Mapping) or m.get("role") != "tool":
            continue
        content = m.get("content")
        if not isinstance(content, str) or len(content) <= max_chars:
            continue
        elided = len(content) - head_chars - tail_chars
        if elided <= 0:
            continue
        new_content = (
            content[:head_chars]
            + f"\n\n[… {elided:,} chars elided by the history size cap; "
            "the full tool result is retained in the local session …]\n\n"
            + content[len(content) - tail_chars:]
        )
        if out is None:
            out = list(messages)
        out[i] = {**m, "content": new_content}
    return out if out is not None else messages


def enforce_total_wire_budget(
    messages,
    *,
    budget_chars: int = 200_000,
    protect_last_n: int = 4,
    head_chars: int = 1_000,
    tail_chars: int = 500,
):
    """Bound the TOTAL wire size of a request by trimming old tool results.

    ``cap_oversized_history_tool_results`` only shrinks *individual* tool
    results larger than its per-message ceiling. A history can still reach the
    very-large band that stalls providers without any single oversized result:
    many moderate tool results (each under the per-message cap) accumulate, or
    large results sit inside that cap's protected tail. Neither is touched, so
    the full payload is re-sent every turn and eventually stalls the primary;
    provider fallback then re-uploads the same oversized payload to the next
    provider (which may itself reject it, e.g. an out-of-usage billing 400).

    This is the total-size backstop. When the wire copy exceeds
    ``budget_chars`` it rewrites old ``role == "tool"`` message contents
    (oldest first, keeping a head+tail excerpt plus a marker) until the total
    is back under budget, then stops — so it trims as little as necessary and
    always oldest-first. Only a small absolute tail (``protect_last_n``) is
    shielded, so the immediate turn keeps its most recent context even under
    budget pressure.

    The ``200_000``-char default (~50K tokens) is tuned for Sinria's default
    reasoning models (gpt-5.6-sol / gpt-5.6-terra). Their binding constraint is
    latency and cost, not capacity: the Codex-OAuth context window is ~272K
    tokens, but a reasoning model silently reasons over the whole input, so a
    request in the very-large band (>=300K chars ~= 75K tokens) drives long
    first-token latency (the dominant stall cause) and heavy reasoning output
    (billed at a premium). ~50K tokens keeps the working set comfortably in the
    fast/cheap zone (~67% of the stall band) while leaving the recent turns and
    recent tool results fully intact for answer quality.

    No-egress: only the transmitted copy is trimmed; the full tool result
    stays in the local session. Messages are never dropped (tool_use/
    tool_result pairing preserved), and non-tool or non-string content is
    never touched. Returns the input list unchanged (same object) when nothing
    is trimmed. ``SINRIA_WIRE_TOTAL_BUDGET`` (legacy
    ``HERMES_WIRE_TOTAL_BUDGET``) overrides ``budget_chars``; set it to ``0``
    to disable.
    """
    raw = _dual_env("WIRE_TOTAL_BUDGET")
    if raw not in (None, ""):
        try:
            budget_chars = max(0, int(raw))
        except (TypeError, ValueError):
            pass
    if budget_chars <= 0:
        return messages

    head_chars = max(0, int(head_chars))
    tail_chars = max(0, int(tail_chars))

    # Real total is needed to decide and to decrement as we trim, so count
    # with a limit far above any realistic request rather than the small
    # threshold used for retry banding.
    total = approximate_request_chars({"messages": messages}, limit=50_000_000)
    if total <= budget_chars:
        return messages

    n = len(messages)
    cutoff = n - max(0, int(protect_last_n))
    if cutoff <= 0:
        return messages

    out = None
    for i in range(cutoff):
        if total <= budget_chars:
            break
        m = messages[i]
        if not isinstance(m, Mapping) or m.get("role") != "tool":
            continue
        content = m.get("content")
        if not isinstance(content, str) or len(content) <= head_chars + tail_chars:
            continue
        elided = len(content) - head_chars - tail_chars
        new_content = (
            content[:head_chars]
            + f"\n\n[… {elided:,} chars elided by the history size budget; "
            "the full tool result is retained in the local session …]\n\n"
            + content[len(content) - tail_chars:]
        )
        saved = len(content) - len(new_content)
        if saved <= 0:
            continue
        if out is None:
            out = list(messages)
        out[i] = {**m, "content": new_content}
        total -= saved
    return out if out is not None else messages


def resolve_watchdog_timeouts(
    base_timeout: float,
    *,
    default_first_token_mult: float = 1.5,
):
    """Return ``(first_token_timeout, stall_timeout)`` for the codex watchdog.

    Reasoning models emit nothing until server-side reasoning finishes, so the
    time to the *first* stream event can legitimately far exceed a mid-stream
    gap. Killing on the base budget before the first event turns a slow-but-
    healthy reasoning request into a false "no protocol progress" timeout.

    Give the first event a more generous budget (``base × mult``) while keeping
    mid-stream inactivity on the base budget, so a real stall *after* streaming
    has started still fails quickly. ``SINRIA_FIRST_TOKEN_MULT`` (legacy
    ``HERMES_FIRST_TOKEN_MULT``; clamped to >=1.0) tunes the first-token
    multiplier; ``SINRIA_STREAM_STALL_TIMEOUT`` (legacy
    ``HERMES_STREAM_STALL_TIMEOUT``), if set to a positive number, overrides
    the mid-stream budget.
    """
    try:
        mult = float(_dual_env("FIRST_TOKEN_MULT") or default_first_token_mult)
    except (TypeError, ValueError):
        mult = default_first_token_mult
    mult = max(1.0, mult)
    first_token = base_timeout * mult

    stall = base_timeout
    raw_stall = _dual_env("STREAM_STALL_TIMEOUT")
    if raw_stall not in (None, ""):
        try:
            override = float(raw_stall)
            if override > 0:
                stall = override
        except (TypeError, ValueError):
            pass
    return first_token, stall


_REASONING_EFFORT_STEP_DOWN = {
    "max": "high",
    "xhigh": "high",
    "high": "medium",
    "medium": "low",
}


def clamp_reasoning_effort_for_request_size(
    effort,
    approx_chars,
    *,
    threshold: int = VERY_LARGE_REQUEST_CHAR_THRESHOLD,
):
    """Step reasoning effort down one level for very-large requests.

    High reasoning effort over a very-large input is the dominant driver of
    first-token latency (long silent server-side reasoning) that trips the
    watchdog. For requests at or above the very-large threshold, drop effort
    one level (max/xhigh->high, high->medium, medium->low) so the provider
    starts responding sooner. ``low``/``minimal``/unknown values are left
    unchanged. Disabled when ``SINRIA_ADAPTIVE_REASONING`` (legacy
    ``HERMES_ADAPTIVE_REASONING``) is ``0``/``false``/``no``.
    """
    if (_dual_env("ADAPTIVE_REASONING") or "").strip().lower() in ("0", "false", "no"):
        return effort
    if not isinstance(approx_chars, (int, float)) or approx_chars < threshold:
        return effort
    key = (effort or "").strip().lower()
    return _REASONING_EFFORT_STEP_DOWN.get(key, effort)


__all__ = [
    "LARGE_REQUEST_CHAR_THRESHOLD",
    "VERY_LARGE_REQUEST_CHAR_THRESHOLD",
    "approximate_request_chars",
    "cap_oversized_history_tool_results",
    "clamp_reasoning_effort_for_request_size",
    "enforce_total_wire_budget",
    "resolve_stream_retry_budget",
    "resolve_watchdog_timeouts",
]
