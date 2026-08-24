from types import SimpleNamespace

import pytest

from agent import chat_completion_helpers as cch
from agent.sinria_egress import prepare_model_provider_payload


class _FakeTransport:
    def build_kwargs(self, **kwargs):
        return kwargs


def _chat_agent(monkeypatch):
    monkeypatch.setattr("providers.get_provider_profile", lambda provider: None)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {})
    return SimpleNamespace(
        api_mode="chat_completions",
        tools=[],
        model="gpt-test",
        base_url="https://api.openai.com/v1",
        _base_url_lower="https://api.openai.com/v1",
        provider="__unknown_test_provider__",
        providers_allowed=None,
        providers_ignored=None,
        providers_order=None,
        provider_sort=None,
        provider_require_parameters=False,
        provider_data_collection=None,
        max_tokens=None,
        reasoning_config=None,
        request_overrides=None,
        _ollama_num_ctx=None,
        openrouter_min_coding_score=None,
        session_id="test-session",
        _get_transport=lambda: _FakeTransport(),
        _is_qwen_portal=lambda: False,
        _is_openrouter_url=lambda: False,
        _prepare_messages_for_non_vision_model=lambda messages: messages,
        _resolved_api_call_timeout=lambda: 1,
        _max_tokens_param=lambda value: {"max_tokens": value},
        _supports_reasoning_extra_body=lambda: False,
        _github_models_reasoning_extra_body=lambda: None,
        _lmstudio_reasoning_options_cached=lambda: None,
        sinria_egress_config={
            "mode": "ask",
            "confidential_external_send": "ask",
            "redact_secrets_before_external_send": True,
            "classify_lightweight": True,
            "profile": "dogfood_frontier",
        },
    )


def test_prepare_api_kwargs_guards_exact_model_provider_payload(monkeypatch):
    calls = []

    def fake_guard(agent, payload):
        calls.append((agent, payload))

    monkeypatch.setattr("agent.sinria_egress.guard_model_provider_egress", fake_guard)
    agent = _chat_agent(monkeypatch)
    messages = [{"role": "user", "content": "hello"}]

    kwargs = cch.build_api_kwargs(agent, messages)
    prepared = prepare_model_provider_payload(agent, kwargs)

    assert calls == [(agent, prepared)]
    assert prepared["messages"] == messages


def test_prepare_api_kwargs_redacts_gh_auth_token_status_before_model_provider(monkeypatch):
    agent = _chat_agent(monkeypatch)
    agent.provider = "openai-codex"
    agent.base_url = "https://chatgpt.com/backend-api/codex"
    agent._base_url_lower = agent.base_url
    raw_token = "gho_1234567890abcdef1234567890abcdef123456"
    messages = [
        {
            "role": "tool",
            "content": (
                "github.com\\n"
                "  ✓ Logged in to github.com account taro-kuroda-5228 (keyring)\\n"
                "  - Active account: true\\n"
                "  - Git operations protocol: https\\n"
                f"  - Token: {raw_token}\\n"
                "  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'\\n"
                "Vercel CLI 50.37.0"
            ),
        },
        {"role": "user", "content": "continue"},
    ]

    kwargs = cch.build_api_kwargs(agent, messages)
    kwargs = prepare_model_provider_payload(agent, kwargs)

    rendered = str(kwargs["messages"])
    assert raw_token not in rendered
    assert "Token: [REDACTED]" in rendered
    assert "Token scopes" in rendered
    assert messages[0]["content"].find(raw_token) != -1  # local session copy remains untouched


def test_prepare_api_kwargs_fails_closed_when_egress_guard_crashes(monkeypatch):
    agent = _chat_agent(monkeypatch)

    def broken_guard(_agent, _messages):
        raise RuntimeError("raw diagnostic details must not escape")

    monkeypatch.setattr(
        "agent.sinria_egress.guard_model_provider_egress",
        broken_guard,
    )

    kwargs = cch.build_api_kwargs(agent, [{"role": "user", "content": "hello"}])
    with pytest.raises(RuntimeError) as exc:
        prepare_model_provider_payload(agent, kwargs)

    assert exc.value.__class__.__name__ == "SinriaEgressGuardFailure"
    assert "raw diagnostic details" not in str(exc.value)
    assert "failed closed" in str(exc.value)


def test_prepare_api_kwargs_redacts_transport_copy_before_guard(monkeypatch):
    agent = _chat_agent(monkeypatch)
    captured = {}
    fake_identifier = "P-" + "90001"
    raw_messages = [
        {"role": "user", "content": f"患者ID: {fake_identifier} の検査結果"}
    ]
    safe_messages = [
        {"role": "user", "content": "患者ID: [REDACTED] の検査結果"}
    ]

    def capture_guard(_agent, payload):
        captured["payload"] = payload

    monkeypatch.setattr(
        "agent.sinria_egress.guard_model_provider_egress",
        capture_guard,
    )
    monkeypatch.setattr(
        "agent.sinria_egress.redact_model_provider_payload",
        lambda _agent, payload: {**payload, "messages": safe_messages},
    )

    kwargs = cch.build_api_kwargs(agent, raw_messages)
    prepared = prepare_model_provider_payload(agent, kwargs)

    assert captured["payload"] is prepared
    assert fake_identifier not in str(captured["payload"])
    assert fake_identifier in str(raw_messages)
    assert fake_identifier not in str(prepared["messages"])
    assert prepared["messages"] == safe_messages


def test_prepare_model_provider_payload_redacts_and_guards_exact_transport_copy(
    monkeypatch,
):
    agent = _chat_agent(monkeypatch)
    fake_identifier = "P-" + "90001"
    raw_payload = {
        "model": "gpt-test",
        "messages": [
            {"role": "user", "content": f"患者ID: {fake_identifier} の検査結果"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "synthetic_tool",
                    "description": f"患者ID: {fake_identifier} の検査結果を読む",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "extra_body": {"metadata": f"患者ID: {fake_identifier}"},
    }
    guarded = []

    def fake_guard(guard_agent, payload):
        assert guard_agent is agent
        guarded.append(payload)

    monkeypatch.setattr("agent.sinria_egress.guard_model_provider_egress", fake_guard)

    prepared = prepare_model_provider_payload(agent, raw_payload)

    assert guarded == [prepared]
    assert fake_identifier not in str(prepared)
    assert "[REDACTED]" in str(prepared)
    assert fake_identifier in str(raw_payload), "the local source payload must not be mutated"

    # A deeper boundary may receive the same already-prepared object. It must
    # not trigger duplicate approval or classification work.
    assert prepare_model_provider_payload(agent, prepared) is prepared
    assert guarded == [prepared]
