"""Verify-after-act actuator (architecture-centric P1, Task A).

The practical-completion guard detected unverified completion claims but
only appended a warning. P1 turns detection into actuation: one bounded
nudge turn to actually verify before the answer is accepted.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.context_share.outcome_gap import should_nudge_verification


# ── Pure classifier ─────────────────────────────────────────────


def test_nudges_unverified_practical_claim():
    assert should_nudge_verification(
        user_message="Fix the failing build and deploy the config change",
        final_response="Done — I completed the fix and everything is finished.",
        tool_turn_count=2,
    )


def test_no_nudge_when_verification_cited():
    assert not should_nudge_verification(
        user_message="Fix the failing build",
        final_response="Done — fixed and verified: the test suite passed after the change.",
        tool_turn_count=2,
    )


def test_no_nudge_for_question_turns():
    assert not should_nudge_verification(
        user_message="Which files control the gateway theme colors?",
        final_response="The skin engine files under sinria_cli. Done explaining.",
        tool_turn_count=1,
    )


def test_no_nudge_without_tool_turns():
    assert not should_nudge_verification(
        user_message="Fix the failing build now",
        final_response="Done, I completed it.",
        tool_turn_count=0,
    )


# ── Loop wiring (source-level invariants) ───────────────────────


def test_loop_wires_nudge_before_post_loop_guard():
    source = Path("agent/conversation_loop.py").read_text(encoding="utf-8")
    seam = source.index("should_nudge_verification")
    guard = source.rindex("apply_practical_completion_guard")
    assert seam < guard, "actuator must run in-loop, before the post-loop warning guard"
    assert "verify_after_act_enabled" in source
    assert "_verify_after_act_retried" in source  # one-retry bound


# ── Integration: mocked client through the real loop ────────────


@pytest.fixture()
def agent():
    with (
        patch("run_agent.get_tool_definitions", return_value=[
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "web_search tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        a = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        a.client = MagicMock()
        a._cached_system_prompt = "You are helpful."
        a._use_prompt_caching = False
        a.tool_delay = 0
        a.compression_enabled = False
        a.save_trajectories = False
        return a


def _resp(content):
    msg = SimpleNamespace(
        content=content, tool_calls=None, reasoning_content=None, reasoning=None
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason="stop")],
        model="test/model",
        usage=None,
    )


def _history_with_tool_turn():
    return [
        {"role": "user", "content": "start"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "name": "web_search", "tool_call_id": "call_1", "content": "ok"},
        {"role": "assistant", "content": "found it"},
    ]


def _run(agent, first, second):
    agent.client.chat.completions.create.side_effect = [first, second]
    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        return agent.run_conversation(
            "Fix the failing build and deploy the config change",
            conversation_history=_history_with_tool_turn(),
        )


def test_nudge_retry_produces_verified_answer(agent):
    agent.verify_after_act_enabled = True
    result = _run(
        agent,
        _resp("Done — I completed the fix and everything is finished."),
        _resp("Verified: I re-ran the build and the test suite passed."),
    )
    assert agent.client.chat.completions.create.call_count == 2
    assert "Verified" in result["final_response"]
    # The nudge exchange stays in the transcript.
    assert any(
        isinstance(m, dict) and m.get("_verify_after_act_synthetic")
        for m in result["messages"]
    )
    # Telemetry: the nudge event is recorded as sanitized metadata for the
    # 運用観察 measurement cron.
    import json as _json

    from hermes_constants import get_sinria_home

    nudges_path = get_sinria_home() / "context_share" / "verify_nudges.jsonl"
    assert nudges_path.exists()
    rows = [
        _json.loads(line)
        for line in nudges_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[-1]["tier"]
    assert set(rows[-1]) <= {"timestamp", "session_id", "model", "provider", "tier"}


def test_nudge_fires_at_most_once(agent):
    agent.verify_after_act_enabled = True
    result = _run(
        agent,
        _resp("Done, I completed the task."),
        _resp("It is done, everything finished."),  # still unverified
    )
    # Two calls: original + one nudge — never a second nudge.
    assert agent.client.chat.completions.create.call_count == 2
    assert result["completed"] is True


def test_disabled_flag_skips_nudge(agent):
    agent.verify_after_act_enabled = False
    result = _run(
        agent,
        _resp("Done, I completed the task."),
        _resp("unused"),
    )
    assert agent.client.chat.completions.create.call_count == 1
    assert "completed the task" in result["final_response"]
