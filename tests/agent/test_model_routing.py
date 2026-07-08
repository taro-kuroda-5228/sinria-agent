"""Routing signals from verified outcomes (architecture-centric P1, Task C).

Sanitized escalation recommendations recorded when a small/medium-tier
model ends a practical turn with a verification/execution gap — the
learning substrate for local-first routing. Metadata only, never raw text.
"""

import json

from agent.model_routing import (
    append_routing_signal,
    build_routing_signal,
    routing_signals_path,
)


def test_escalates_small_tier_verification_gap():
    signal = build_routing_signal(
        model="qwen3.5:9b",
        provider="custom",
        tier="small",
        cause_kind="verification_gap",
        escalation_model="claude-fable-5",
    )
    assert signal["recommendation"] == "escalate"
    assert signal["tier"] == "small"
    assert signal["cause_kind"] == "verification_gap"
    assert signal["escalation_model"] == "claude-fable-5"
    assert signal["timestamp"]


def test_escalates_medium_tier_execution_incomplete_without_target():
    signal = build_routing_signal(
        model="qwen2.5-14b",
        provider="custom",
        tier="medium",
        cause_kind="execution_incomplete",
    )
    assert signal["recommendation"] == "escalate"
    assert "escalation_model" not in signal


def test_no_signal_for_large_tier_or_benign_causes():
    assert (
        build_routing_signal(
            model="claude-fable-5", provider="anthropic",
            tier="large", cause_kind="verification_gap",
        )
        is None
    )
    for cause in ("none", "not_practical_action", "interrupted_or_failed"):
        assert (
            build_routing_signal(
                model="qwen3.5:9b", provider="custom",
                tier="small", cause_kind=cause,
            )
            is None
        )


def test_append_writes_sanitized_jsonl():
    signal = build_routing_signal(
        model="qwen3.5:9b", provider="custom",
        tier="small", cause_kind="verification_gap",
    )
    path = append_routing_signal(signal)
    assert path == routing_signals_path()
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[-1]["recommendation"] == "escalate"
    # Metadata-only shape: nothing besides the sanctioned keys.
    assert set(rows[-1]) <= {
        "model", "provider", "tier", "cause_kind",
        "recommendation", "escalation_model", "timestamp",
    }


def test_loop_wires_routing_signal_after_outcome_recording():
    from pathlib import Path

    source = Path("agent/conversation_loop.py").read_text(encoding="utf-8")
    record = source.index("record_practical_outcome_and_candidates")
    signal = source.index("build_routing_signal")
    assert record < signal, "routing signal must derive from the recorded outcome"
