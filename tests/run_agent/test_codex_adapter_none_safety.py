"""RED tests for `agent.codex_responses_adapter._normalize_codex_response`.

Reproduces the production crash observed across multiple sessions on 5/27 and
5/28 2026: when `claude-opus-4-7` was Overloaded (HTTP 529) and the fallback
chain switched to `openai-codex/gpt-5.5`, the Codex Responses adapter raised
``TypeError: 'NoneType' object is not iterable`` and was marked
``Non-retryable client error`` — breaking the fallback chain.

GPT-5.5 (and other Codex models on degraded paths) can return response shapes
where output items contain ``None`` instead of the expected list — usually
``content=None`` on a message item, ``summary=None`` on a reasoning item, or
``output=None`` on a custom_tool_call / function_call item. The fix is
defensive defaults so iteration always sees a list.

These tests assert the normalizer:
1. Doesn't raise on any of the documented degraded shapes.
2. Returns the expected SimpleNamespace fields.
3. Maps the response to a sensible finish_reason ("stop"/"incomplete"/"tool_calls").
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from agent.codex_responses_adapter import (
    _normalize_codex_response,
    _extract_responses_message_text,
    _extract_responses_reasoning_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _response(
    output: Optional[List[Any]],
    *,
    status: Optional[str] = "completed",
    output_text: Optional[str] = None,
) -> SimpleNamespace:
    """Build a minimal Responses API response stub."""
    ns = SimpleNamespace()
    ns.output = output
    if status is not None:
        ns.status = status
    if output_text is not None:
        ns.output_text = output_text
    return ns


def _message_item(
    *,
    content: Any,
    status: str = "completed",
    phase: Optional[str] = None,
    item_id: Optional[str] = None,
) -> SimpleNamespace:
    ns = SimpleNamespace(type="message", role="assistant", status=status, content=content)
    if phase is not None:
        ns.phase = phase
    if item_id is not None:
        ns.id = item_id
    return ns


def _reasoning_item(
    *,
    summary: Any = None,
    encrypted_content: Optional[str] = None,
    text: Optional[str] = None,
) -> SimpleNamespace:
    ns = SimpleNamespace(type="reasoning")
    ns.summary = summary
    if encrypted_content is not None:
        ns.encrypted_content = encrypted_content
    if text is not None:
        ns.text = text
    return ns


def _function_call_item(
    *,
    name: Any = "terminal",
    arguments: Any = '{"command": "ls"}',
    call_id: str = "call_abc123",
    status: str = "completed",
) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call",
        name=name,
        arguments=arguments,
        call_id=call_id,
        id=f"fc_{call_id[len('call_'):]}",
        status=status,
    )


# ---------------------------------------------------------------------------
# Tests — content=None on a message item (the primary crash)
# ---------------------------------------------------------------------------


def test_message_item_with_none_content_does_not_raise():
    """gpt-5.5 returns message items where content is None.

    Before fix: `for part in content` inside _extract_responses_message_text
    raises TypeError when content is None.

    NB: The current code guards via `isinstance(content, list)`, but if a
    consumer ever loosens the guard or a sibling helper iterates without
    checking, this is the regression to catch.
    """
    output = [_message_item(content=None)]
    response = _response(output)
    msg, finish_reason = _normalize_codex_response(response)
    assert msg is not None
    # No visible text since content was None
    assert msg.content == ""
    # No tool_calls
    assert msg.tool_calls == []
    # When response has no content/tool_calls and is "completed", finish_reason
    # should be "stop"
    assert finish_reason == "stop"


def test_extract_message_text_handles_none_content_directly():
    """The helper must not iterate over None."""
    item = SimpleNamespace(content=None)
    # Must not raise — must return empty string
    assert _extract_responses_message_text(item) == ""


def test_extract_message_text_handles_missing_content():
    """The helper must not iterate when attribute is missing."""
    item = SimpleNamespace()
    assert _extract_responses_message_text(item) == ""


def test_extract_message_text_handles_content_with_none_parts():
    """Parts inside content list can themselves be None — must skip, not raise."""
    item = SimpleNamespace(
        content=[
            None,
            SimpleNamespace(type="output_text", text="hello"),
            None,
        ]
    )
    # getattr on None returns the default; the helper should still extract "hello"
    assert _extract_responses_message_text(item) == "hello"


# ---------------------------------------------------------------------------
# Tests — summary=None on a reasoning item (secondary crash)
# ---------------------------------------------------------------------------


def test_reasoning_item_with_none_summary_does_not_raise():
    """gpt-5.5 returns reasoning items with summary=None instead of [].

    Before fix: `for part in summary` raises TypeError.
    """
    output = [
        _reasoning_item(summary=None, encrypted_content="enc-blob-xyz"),
        _message_item(
            content=[SimpleNamespace(type="output_text", text="answer")]
        ),
    ]
    response = _response(output)
    msg, finish_reason = _normalize_codex_response(response)
    assert msg is not None
    assert msg.content == "answer"
    # reasoning items still captured for multi-turn continuity
    assert msg.codex_reasoning_items is not None
    assert len(msg.codex_reasoning_items) == 1
    assert msg.codex_reasoning_items[0]["encrypted_content"] == "enc-blob-xyz"
    # summary defaulted to empty list, not None
    assert msg.codex_reasoning_items[0].get("summary") == []
    assert finish_reason == "stop"


def test_extract_reasoning_text_handles_none_summary_and_text():
    item = SimpleNamespace(summary=None, text=None)
    assert _extract_responses_reasoning_text(item) == ""


def test_extract_reasoning_text_handles_summary_with_none_parts():
    item = SimpleNamespace(
        summary=[None, SimpleNamespace(text="thinking..."), None],
        text=None,
    )
    assert _extract_responses_reasoning_text(item) == "thinking..."


# ---------------------------------------------------------------------------
# Tests — response-level degraded shapes
# ---------------------------------------------------------------------------


def test_response_with_output_None_and_output_text_fallback():
    """Fallback to response.output_text when output is None."""
    response = _response(output=None, output_text="quick reply")
    msg, finish_reason = _normalize_codex_response(response)
    assert msg.content == "quick reply"
    assert finish_reason == "stop"


def test_response_with_output_None_and_no_output_text_raises_RuntimeError():
    """When both output and output_text are absent, we raise RuntimeError —
    NOT TypeError. RuntimeError is retryable in the upstream caller."""
    response = _response(output=None, output_text=None)
    with pytest.raises(RuntimeError, match="no output"):
        _normalize_codex_response(response)


def test_response_with_empty_output_list_and_no_output_text_raises_RuntimeError():
    response = _response(output=[], output_text=None)
    with pytest.raises(RuntimeError, match="no output"):
        _normalize_codex_response(response)


# ---------------------------------------------------------------------------
# Tests — function_call items with degraded fields
# ---------------------------------------------------------------------------


def test_function_call_with_None_arguments_does_not_raise():
    """gpt-5.5 occasionally emits arguments=None on a function_call item.

    Before fix: arguments is forwarded as None; downstream consumers expect a
    JSON string.
    """
    output = [_function_call_item(arguments=None)]
    response = _response(output)
    msg, finish_reason = _normalize_codex_response(response)
    assert msg is not None
    assert len(msg.tool_calls) == 1
    tc = msg.tool_calls[0]
    # arguments must be a string ("{}" or similar) — never None
    assert isinstance(tc.function.arguments, str)
    assert tc.function.arguments  # non-empty
    assert finish_reason == "tool_calls"


def test_function_call_with_None_name_is_skipped_or_handled():
    """A function_call item with name=None should not crash normalization."""
    output = [_function_call_item(name=None)]
    response = _response(output)
    # Must not raise — the item is either skipped or yields a tool_call with
    # an empty name string (caller treats empty-name as an error downstream).
    msg, _ = _normalize_codex_response(response)
    assert msg is not None
    for tc in msg.tool_calls:
        assert isinstance(tc.function.name, str)


# ---------------------------------------------------------------------------
# Tests — combined / full gpt-5.5 fallback scenario
# ---------------------------------------------------------------------------


def test_gpt55_degraded_fallback_full_shape():
    """End-to-end reproduction of the production crash.

    Shape observed when claude-opus-4-7 → openai-codex/gpt-5.5 fallback fires:
    - One reasoning item with summary=None
    - One message item with content=None (text leaked into reasoning instead)
    - response.output_text carries the actual reply
    """
    output = [
        _reasoning_item(summary=None, encrypted_content="enc-1"),
        _message_item(content=None),
    ]
    response = _response(output, output_text="Sorry, I lost context — retry.")
    # Must not raise TypeError
    msg, finish_reason = _normalize_codex_response(response)
    assert msg is not None
    # output_text fallback should populate content
    assert "lost context" in msg.content
    assert finish_reason in {"stop", "incomplete"}
    # Reasoning replay state preserved
    assert msg.codex_reasoning_items is not None
    assert msg.codex_reasoning_items[0]["encrypted_content"] == "enc-1"
