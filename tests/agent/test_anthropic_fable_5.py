"""Regression coverage for Claude Fable 5 on the native Anthropic path."""

from agent.anthropic_adapter import (
    _get_anthropic_max_output,
    _supports_adaptive_thinking,
    _supports_xhigh_effort,
    build_anthropic_kwargs,
)
from agent.model_metadata import DEFAULT_CONTEXT_LENGTHS
from agent.usage_pricing import CanonicalUsage, estimate_usage_cost


def test_fable_5_supports_adaptive_thinking_xhigh_and_128k_output():
    assert _supports_adaptive_thinking("claude-fable-5") is True
    assert _supports_xhigh_effort("claude-fable-5") is True
    assert _get_anthropic_max_output("claude-fable-5") == 128_000


def test_fable_5_builds_anthropic_kwargs_with_xhigh_effort():
    kwargs = build_anthropic_kwargs(
        model="claude-fable-5",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        max_tokens=None,
        reasoning_config={"enabled": True, "effort": "xhigh"},
    )

    assert kwargs["model"] == "claude-fable-5"
    assert kwargs["max_tokens"] == 128_000
    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kwargs["output_config"] == {"effort": "xhigh"}


def test_fable_5_context_and_pricing_fallbacks_are_known():
    assert DEFAULT_CONTEXT_LENGTHS["claude-fable-5"] == 1_000_000

    cost = estimate_usage_cost(
        "claude-fable-5",
        CanonicalUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        provider="anthropic",
    )
    assert str(cost.amount_usd) == "60.00"
    assert cost.source == "official_docs_snapshot"
