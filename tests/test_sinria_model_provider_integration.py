from types import SimpleNamespace

from agent import chat_completion_helpers as cch


class _FakeTransport:
    def build_kwargs(self, **kwargs):
        return kwargs


def _chat_agent(monkeypatch):
    monkeypatch.setattr("providers.get_provider_profile", lambda provider: None)
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


def test_build_api_kwargs_guards_model_provider_egress(monkeypatch):
    calls = []

    def fake_guard(agent, messages):
        calls.append((agent, messages))

    monkeypatch.setattr("agent.sinria_egress.guard_model_provider_egress", fake_guard)
    agent = _chat_agent(monkeypatch)
    messages = [{"role": "user", "content": "hello"}]

    kwargs = cch.build_api_kwargs(agent, messages)

    assert calls == [(agent, messages)]
    assert kwargs["messages"] == messages


def test_build_api_kwargs_redacts_gh_auth_token_status_before_model_provider(monkeypatch):
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

    rendered = str(kwargs["messages"])
    assert raw_token not in rendered
    assert "Token: [REDACTED]" in rendered
    assert "Token scopes" in rendered
    assert messages[0]["content"].find(raw_token) != -1  # local session copy remains untouched
