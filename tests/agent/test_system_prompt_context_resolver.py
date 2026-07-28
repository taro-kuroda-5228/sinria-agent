
from types import SimpleNamespace

from agent.system_prompt import build_system_prompt_parts


class MemoryStore:
    def format_for_system_prompt(self, target):
        if target == "memory":
            return "Sinria identity memory"
        if target == "user":
            return "User expects context continuity"
        return ""


def test_system_prompt_volatile_layer_includes_context_resolver_block(monkeypatch):
    import run_agent
    monkeypatch.setattr(run_agent, "load_soul_md", lambda: "Sinria identity")
    monkeypatch.setattr(run_agent, "build_nous_subscription_prompt", lambda names: "")
    monkeypatch.setattr(run_agent, "build_skills_system_prompt", lambda **kwargs: "")
    monkeypatch.setattr(run_agent, "build_environment_hints", lambda: "")
    monkeypatch.setattr(run_agent, "build_context_files_prompt", lambda **kwargs: "")
    monkeypatch.setattr(run_agent, "get_toolset_for_tool", lambda tool: None)

    agent = SimpleNamespace(
        load_soul_identity=True,
        skip_context_files=True,
        valid_tool_names={"memory", "session_search", "skill_view"},
        _tool_use_enforcement=False,
        provider="openai",
        model="gpt-5.5",
        platform="discord",
        _memory_store=MemoryStore(),
        _memory_enabled=True,
        _user_profile_enabled=True,
        _memory_manager=None,
        pass_session_id=False,
        session_id="session-1",
    )

    parts = build_system_prompt_parts(agent, system_message="Sinriaのコンテキストシェア改善")

    assert "Context Share Resolver" in parts["volatile"]
    assert "prior corrections" in parts["volatile"]
    assert "Sinria-native" in parts["volatile"]


def test_system_prompt_context_resolver_uses_current_user_message(monkeypatch):
    import run_agent
    monkeypatch.setattr(run_agent, "load_soul_md", lambda: "Sinria identity")
    monkeypatch.setattr(run_agent, "build_nous_subscription_prompt", lambda names: "")
    monkeypatch.setattr(run_agent, "build_skills_system_prompt", lambda **kwargs: "")
    monkeypatch.setattr(run_agent, "build_environment_hints", lambda: "")
    monkeypatch.setattr(run_agent, "build_context_files_prompt", lambda **kwargs: "")
    monkeypatch.setattr(run_agent, "get_toolset_for_tool", lambda tool: None)

    agent = SimpleNamespace(
        load_soul_identity=True,
        skip_context_files=True,
        valid_tool_names={"memory", "session_search", "skill_view"},
        _tool_use_enforcement=False,
        provider="openai",
        model="gpt-5.5",
        platform="discord",
        _memory_store=MemoryStore(),
        _memory_enabled=True,
        _user_profile_enabled=True,
        _memory_manager=None,
        pass_session_id=False,
        session_id="session-1",
    )

    parts = build_system_prompt_parts(agent, system_message=None, current_user_message="Sales Agent OSをTDDで完成させて")

    assert "Implement the requested Sinria plan with tests and safety verification" in parts["volatile"]
    assert "test-driven-development" in parts["volatile"]


def test_system_prompt_context_resolver_does_not_reseed_from_stale_system_message(monkeypatch):
    import run_agent
    monkeypatch.setattr(run_agent, "load_soul_md", lambda: "Sinria identity")
    monkeypatch.setattr(run_agent, "build_nous_subscription_prompt", lambda names: "")
    monkeypatch.setattr(run_agent, "build_skills_system_prompt", lambda **kwargs: "")
    monkeypatch.setattr(run_agent, "build_environment_hints", lambda: "")
    monkeypatch.setattr(run_agent, "build_context_files_prompt", lambda **kwargs: "")
    monkeypatch.setattr(run_agent, "get_toolset_for_tool", lambda tool: None)

    agent = SimpleNamespace(
        load_soul_identity=True,
        skip_context_files=True,
        valid_tool_names={"memory", "session_search", "skill_view"},
        _tool_use_enforcement=False,
        provider="openai",
        model="gpt-5.5",
        platform="discord",
        _memory_store=MemoryStore(),
        _memory_enabled=True,
        _user_profile_enabled=True,
        _memory_manager=None,
        pass_session_id=False,
        session_id="session-sidework",
    )

    stale_other_channel_system_message = (
        "## Context Share Resolver\n"
        "- Active project override: MedSpotの本番化計画を作成して\n"
        "Current MedSpot productionization context must resolve to the MedSpot repo."
    )

    parts = build_system_prompt_parts(
        agent,
        system_message=stale_other_channel_system_message,
        current_user_message="SinriaのDiscordチャンネル混線を直して",
    )

    assert "Context Share Resolver" in parts["volatile"]
    assert "MedSpot productionization" not in parts["volatile"]
    assert "MedSpotの本番化" not in parts["volatile"]


def test_system_prompt_injects_source_lock_from_current_user_message(monkeypatch):
    import run_agent
    monkeypatch.setattr(run_agent, "load_soul_md", lambda: "Sinria identity")
    monkeypatch.setattr(run_agent, "build_nous_subscription_prompt", lambda names: "")
    monkeypatch.setattr(run_agent, "build_skills_system_prompt", lambda **kwargs: "")
    monkeypatch.setattr(run_agent, "build_environment_hints", lambda: "")
    monkeypatch.setattr(run_agent, "build_context_files_prompt", lambda **kwargs: "")
    monkeypatch.setattr(run_agent, "get_toolset_for_tool", lambda tool: None)

    agent = SimpleNamespace(
        load_soul_identity=True,
        skip_context_files=True,
        valid_tool_names={"memory", "session_search", "skill_view"},
        _tool_use_enforcement=False,
        provider="openai",
        model="gpt-5.5",
        platform="discord",
        _memory_store=MemoryStore(),
        _memory_enabled=True,
        _user_profile_enabled=True,
        _memory_manager=None,
        pass_session_id=False,
        session_id="session-medspot",
    )

    parts = build_system_prompt_parts(
        agent,
        system_message="old Company OS context",
        current_user_message="MedSpotのUIをmockupに合わせて実装して",
    )

    assert "Project Source-Lock Gate" in parts["volatile"]
    assert "current repository" in parts["volatile"]
    assert "mockup/source artifact" in parts["volatile"]
    assert "/Users/" not in parts["volatile"]

def test_system_prompt_reply_header_does_not_reseed_stale_proxy_context_for_medevidence(monkeypatch):
    import run_agent
    monkeypatch.setattr(run_agent, "load_soul_md", lambda: "Sinria identity")
    monkeypatch.setattr(run_agent, "build_nous_subscription_prompt", lambda names: "")
    monkeypatch.setattr(run_agent, "build_skills_system_prompt", lambda **kwargs: "")
    monkeypatch.setattr(run_agent, "build_environment_hints", lambda: "")
    monkeypatch.setattr(run_agent, "build_context_files_prompt", lambda **kwargs: "")
    monkeypatch.setattr(run_agent, "get_toolset_for_tool", lambda tool: None)

    agent = SimpleNamespace(
        load_soul_identity=True,
        skip_context_files=True,
        valid_tool_names={"memory", "session_search", "skill_view"},
        _tool_use_enforcement=False,
        provider="openai",
        model="gpt-5.5",
        platform="discord",
        _memory_store=MemoryStore(),
        _memory_enabled=True,
        _user_profile_enabled=True,
        _memory_manager=None,
        pass_session_id=False,
        session_id="session-medevidence",
    )

    current_user_message = (
        '[Replying to: "Cloud Run proxy は既に終了しており、port 18082 は使っていません。"]\n\n'
        "[Test User] メドエビデンスレポジトリのconflictを解消してmainにマージして"
    )

    parts = build_system_prompt_parts(
        agent,
        system_message="old MedSpot Cloud Run proxy context",
        current_user_message=current_user_message,
    )

    assert "Project Source-Lock Gate" in parts["volatile"]
    assert "current repository" in parts["volatile"]
    assert "older durable project context" in parts["volatile"]
    assert "Cloud Run proxy は既に終了" not in parts["volatile"]
    assert "/Users/" not in parts["volatile"]
