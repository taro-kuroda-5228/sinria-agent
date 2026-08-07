"""Token-budgeted system-prompt assembly (architecture-centric P0, Task 3).

Small-tier models get truncated volatile blocks plus small-context
operational guidance; large-tier (or profile-less) agents keep today's
behavior byte-identical.
"""

from types import SimpleNamespace

from agent.model_capabilities import (
    ModelCapabilityProfile,
    resolve_capability_profile,
)
from agent.system_prompt import build_system_prompt_parts


class LongMemoryStore:
    def format_for_system_prompt(self, target):
        if target == "memory":
            return "M" * 10_000
        if target == "user":
            return "U" * 5_000
        return ""


def _patch_prompt_helpers(monkeypatch):
    import run_agent

    monkeypatch.setattr(run_agent, "load_soul_md", lambda: "Sinria identity")
    monkeypatch.setattr(run_agent, "build_nous_subscription_prompt", lambda names: "")
    monkeypatch.setattr(run_agent, "build_skills_system_prompt", lambda **kwargs: "")
    monkeypatch.setattr(run_agent, "build_environment_hints", lambda: "")
    monkeypatch.setattr(run_agent, "build_context_files_prompt", lambda **kwargs: "")
    monkeypatch.setattr(run_agent, "get_toolset_for_tool", lambda tool: None)


def _make_agent(profile):
    agent = SimpleNamespace(
        load_soul_identity=True,
        skip_context_files=True,
        valid_tool_names={"memory"},
        _tool_use_enforcement=False,
        provider="custom",
        model="qwen2.5-7b-instruct",
        platform="cli",
        _memory_store=LongMemoryStore(),
        _memory_enabled=True,
        _user_profile_enabled=True,
        _memory_manager=None,
        pass_session_id=False,
        session_id="session-1",
    )
    if profile is not None:
        agent.capability_profile = profile
    return agent


def test_small_tier_truncates_memory_and_user_blocks(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    profile = resolve_capability_profile(16_000)
    parts = build_system_prompt_parts(_make_agent(profile), system_message="do a task")

    # Memory block truncated to its budget (plus marker), not injected whole.
    assert "M" * profile.memory_char_budget in parts["volatile"]
    assert "M" * (profile.memory_char_budget + 1) not in parts["volatile"]
    assert "U" * (profile.user_profile_char_budget + 1) not in parts["volatile"]
    assert "recall_context" in parts["volatile"]
    # Small-context operational guidance lands in the stable tier.
    assert "Small-context mode" in parts["stable"]


def test_large_tier_is_unchanged(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    parts = build_system_prompt_parts(
        _make_agent(resolve_capability_profile(1_000_000)),
        system_message="do a task",
    )
    assert "M" * 10_000 in parts["volatile"]
    assert "U" * 5_000 in parts["volatile"]
    assert "Small-context mode" not in parts["stable"]


def test_missing_profile_is_unchanged(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    parts = build_system_prompt_parts(_make_agent(None), system_message="do a task")
    assert "M" * 10_000 in parts["volatile"]
    assert "Small-context mode" not in parts["stable"]


def test_system_prompt_omits_turn_scoped_resolver_even_with_budget(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    tiny = ModelCapabilityProfile(
        context_length=16_000,
        tier="small",
        max_iterations_cap=30,
        memory_char_budget=4_000,
        user_profile_char_budget=2_000,
        advice_char_budget=50,
    )
    parts = build_system_prompt_parts(_make_agent(tiny), system_message="改善して")
    # conversation_loop is the single owner of per-turn correction advice.
    assert "Correction Checklist" not in parts["volatile"]
