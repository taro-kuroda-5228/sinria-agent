"""Tests for the model capability contract (architecture-centric P0).

See docs/plans/2026-07-06-architecture-centric-agent-os-p0.md Task 1/2.
"""

import pytest

from agent.model_capabilities import (
    SMALL_CONTEXT_FLOOR,
    apply_char_budget,
    parse_param_billions,
    resolve_capability_profile,
    tier_for_context_length,
    validate_context_length,
)


def test_tier_boundaries():
    assert tier_for_context_length(8_000) == "small"
    assert tier_for_context_length(31_999) == "small"
    assert tier_for_context_length(32_000) == "medium"
    assert tier_for_context_length(99_999) == "medium"
    assert tier_for_context_length(100_000) == "large"
    assert tier_for_context_length(1_000_000) == "large"


def test_unknown_context_is_large_tier_with_unlimited_budgets():
    for ctx in (None, 0):
        profile = resolve_capability_profile(ctx)
        assert profile.tier == "large"
        assert profile.max_iterations_cap == 90
        assert profile.memory_char_budget == 0
        assert profile.user_profile_char_budget == 0
        assert profile.advice_char_budget == 0


def test_small_tier_scales_iterations_and_budgets():
    profile = resolve_capability_profile(16_000)
    assert profile.tier == "small"
    assert profile.max_iterations_cap < 90
    assert profile.memory_char_budget > 0
    assert profile.user_profile_char_budget > 0
    assert profile.advice_char_budget > 0


def test_medium_tier_sits_between_small_and_large():
    small = resolve_capability_profile(16_000)
    medium = resolve_capability_profile(64_000)
    assert medium.tier == "medium"
    assert small.max_iterations_cap < medium.max_iterations_cap <= 90
    assert small.memory_char_budget < medium.memory_char_budget


# ── parameter-size tier clamp ──────────────────────────────────────
# Context length stopped being a usable capability proxy on its own: current
# small local models (qwen3.5:9b = 131K ctx) advertise frontier-sized windows,
# so every local model resolved to "large" and the routing-signal substrate
# (P1) could never fire. Parameter count from the model name clamps the tier
# back to actual capability; context length alone can still LOWER the tier.


def test_parse_param_billions():
    assert parse_param_billions("qwen3.5:9b") == 9.0
    assert parse_param_billions("qwen3.5:27b") == 27.0
    assert parse_param_billions("llama3:70b-instruct") == 70.0
    assert parse_param_billions("phi4:3.8b") == 3.8
    # Cloud/frontier names carry no NNb parameter suffix — no clamp.
    assert parse_param_billions("claude-fable-5") is None
    assert parse_param_billions("gpt-5.5") is None
    assert parse_param_billions("gemma4:latest") is None


def test_param_clamp_caps_tier_for_small_local_models():
    # 9B with a 131K window: ctx says "large", params say "medium" — the
    # more conservative wins, so routing signals / budgets engage.
    profile = resolve_capability_profile(131_072, model="qwen3.5:9b")
    assert profile.tier == "medium"
    # ≤8B params clamp all the way to "small" even with a huge window.
    assert resolve_capability_profile(131_072, model="phi4:3.8b").tier == "small"
    # Params above the medium cap leave the ctx tier untouched.
    assert resolve_capability_profile(131_072, model="llama3:70b-instruct").tier == "large"


def test_param_clamp_never_raises_tier_and_ignores_frontier_names():
    # A small context window stays "small" even for a big-param model.
    assert resolve_capability_profile(16_000, model="llama3:70b-instruct").tier == "small"
    # Frontier names without a param suffix keep pure ctx behavior.
    assert resolve_capability_profile(1_000_000, model="claude-fable-5").tier == "large"
    # Unknown ctx + small-param name: the name is strong evidence — clamp.
    assert resolve_capability_profile(None, model="qwen3.5:9b").tier == "medium"


def test_validate_below_absolute_floor_raises():
    with pytest.raises(ValueError):
        validate_context_length(
            SMALL_CONTEXT_FLOOR - 1,
            small_context_mode="auto",
            model="tiny-model",
            minimum_context_length=64_000,
        )


def test_validate_strict_mode_rejects_below_minimum():
    with pytest.raises(ValueError):
        validate_context_length(
            16_000,
            small_context_mode="strict",
            model="qwen2.5-7b",
            minimum_context_length=64_000,
        )


def test_validate_auto_mode_warns_below_minimum():
    warning = validate_context_length(
        16_000,
        small_context_mode="auto",
        model="qwen2.5-7b",
        minimum_context_length=64_000,
    )
    assert warning
    assert "small-context" in warning


def test_validate_at_or_above_minimum_is_silent():
    assert (
        validate_context_length(
            64_000,
            small_context_mode="strict",
            model="m",
            minimum_context_length=64_000,
        )
        is None
    )
    assert (
        validate_context_length(
            None,
            small_context_mode="auto",
            model="m",
            minimum_context_length=64_000,
        )
        is None
    )


def test_apply_char_budget():
    assert apply_char_budget("short", 100, "memory") == "short"
    # 0 = unlimited
    assert apply_char_budget("anything", 0, "memory") == "anything"
    truncated = apply_char_budget("x" * 500, 100, "memory")
    assert len(truncated) < 500 + 200
    assert truncated.startswith("x" * 100)
    assert "recall_context" in truncated


def test_agent_init_wires_capability_profile():
    from pathlib import Path

    source = Path("agent/agent_init.py").read_text(encoding="utf-8")
    assert "resolve_capability_profile" in source
    assert "capability_profile" in source
    assert "small_context_mode" in source
