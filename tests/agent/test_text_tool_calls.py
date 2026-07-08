"""Text-mode tool calling (architecture-centric P0, Task 5).

Models without native function calling emit ```tool_call``` fenced blocks;
parse_text_tool_calls turns them into normalized ToolCall objects so the
rest of the loop stays provider-agnostic. Parseability becomes an
architecture guarantee instead of a model skill.
"""

import json

from agent.text_tool_calls import TEXT_TOOL_CALL_GUIDANCE, parse_text_tool_calls


def test_parse_single_block():
    content = (
        "I'll read the file first.\n"
        "```tool_call\n"
        '{"name": "read_file", "arguments": {"path": "notes.md"}}\n'
        "```\n"
    )
    calls, cleaned = parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0].name == "read_file"
    assert json.loads(calls[0].arguments) == {"path": "notes.md"}
    # Deterministic id assigned at parse time (adapter-side id fill is
    # bypassed on this path, and tool results must pair with their calls).
    assert calls[0].id and calls[0].id.startswith("text_call_")
    # Compatibility properties used throughout the loop.
    assert calls[0].function.name == "read_file"
    assert calls[0].type == "function"
    assert "tool_call" not in cleaned
    assert "I'll read the file first." in cleaned


def test_parse_multiple_blocks():
    content = (
        "```tool_call\n"
        '{"name": "search_files", "arguments": {"pattern": "foo"}}\n'
        "```\n"
        "then\n"
        "```tool_call\n"
        '{"name": "read_file", "arguments": {"path": "a.py"}}\n'
        "```"
    )
    calls, cleaned = parse_text_tool_calls(content)
    assert [c.name for c in calls] == ["search_files", "read_file"]
    assert "then" in cleaned


def test_malformed_json_left_visible_not_dropped():
    content = "```tool_call\n{not json}\n```"
    calls, cleaned = parse_text_tool_calls(content)
    assert calls == []
    assert "{not json}" in cleaned  # malformed block stays visible


def test_non_dict_arguments_treated_as_malformed():
    content = '```tool_call\n{"name": "read_file", "arguments": "notes.md"}\n```'
    calls, cleaned = parse_text_tool_calls(content)
    assert calls == []
    assert "read_file" in cleaned


def test_missing_name_treated_as_malformed():
    content = '```tool_call\n{"arguments": {"path": "a"}}\n```'
    calls, cleaned = parse_text_tool_calls(content)
    assert calls == []


def test_no_block_returns_content_unchanged():
    content = "Just a normal answer."
    calls, cleaned = parse_text_tool_calls(content)
    assert calls == []
    assert cleaned == content


def test_arguments_default_to_empty_object():
    content = '```tool_call\n{"name": "todo"}\n```'
    calls, _ = parse_text_tool_calls(content)
    assert len(calls) == 1
    assert json.loads(calls[0].arguments) == {}


def test_provider_data_marks_text_source():
    content = '```tool_call\n{"name": "todo", "arguments": {}}\n```'
    calls, _ = parse_text_tool_calls(content)
    assert calls[0].provider_data == {"source": "text_tool_call"}


def test_guidance_documents_the_format():
    assert "```tool_call" in TEXT_TOOL_CALL_GUIDANCE
    assert "arguments" in TEXT_TOOL_CALL_GUIDANCE


def test_conversation_loop_wires_text_fallback_before_post_api_hook():
    from pathlib import Path

    source = Path("agent/conversation_loop.py").read_text(encoding="utf-8")
    seam = source.index("text_tool_calls_enabled")
    post_hook = source.index('"post_api_request"')
    assert seam < post_hook, "text fallback must run before the post_api_request hook"
    assert "parse_text_tool_calls" in source


def test_system_prompt_injects_guidance_when_enabled(monkeypatch):
    from types import SimpleNamespace

    import run_agent
    from agent.system_prompt import build_system_prompt_parts

    monkeypatch.setattr(run_agent, "load_soul_md", lambda: "Sinria identity")
    monkeypatch.setattr(run_agent, "build_nous_subscription_prompt", lambda names: "")
    monkeypatch.setattr(run_agent, "build_skills_system_prompt", lambda **kwargs: "")
    monkeypatch.setattr(run_agent, "build_environment_hints", lambda: "")
    monkeypatch.setattr(run_agent, "build_context_files_prompt", lambda **kwargs: "")
    monkeypatch.setattr(run_agent, "get_toolset_for_tool", lambda tool: None)

    def make_agent(enabled):
        return SimpleNamespace(
            load_soul_identity=True,
            skip_context_files=True,
            valid_tool_names={"memory"},
            _tool_use_enforcement=False,
            provider="custom",
            model="qwen2.5-7b-instruct",
            platform="cli",
            _memory_store=None,
            _memory_enabled=False,
            _user_profile_enabled=False,
            _memory_manager=None,
            pass_session_id=False,
            session_id="s",
            text_tool_calls_enabled=enabled,
        )

    on = build_system_prompt_parts(make_agent(True), system_message="x")
    off = build_system_prompt_parts(make_agent(False), system_message="x")
    assert "```tool_call" in on["stable"]
    assert "```tool_call" not in off["stable"]
