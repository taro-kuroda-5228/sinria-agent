"""Tests for the model capability contract (architecture-centric P0).

See docs/plans/2026-07-06-architecture-centric-agent-os-p0.md Task 1/2.
"""

import pytest

from agent.model_capabilities import (
    SMALL_CONTEXT_FLOOR,
    apply_char_budget,
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
        assert profile.resolver_char_budget == 0


def test_small_tier_scales_iterations_and_budgets():
    profile = resolve_capability_profile(16_000)
    assert profile.tier == "small"
    assert profile.max_iterations_cap < 90
    assert profile.memory_char_budget > 0
    assert profile.user_profile_char_budget > 0
    assert profile.resolver_char_budget > 0


def test_medium_tier_sits_between_small_and_large():
    small = resolve_capability_profile(16_000)
    medium = resolve_capability_profile(64_000)
    assert medium.tier == "medium"
    assert small.max_iterations_cap < medium.max_iterations_cap <= 90
    assert small.memory_char_budget < medium.memory_char_budget


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
