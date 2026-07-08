"""Hybrid strategist routing — Fable 5 plans, the executor acts.

Design: docs/plans/2026-07-07-hybrid-strategist-routing.md. Core rules:
plan only for complex practical tasks, hard per-task budget, sanitized
JSONL telemetry, feature fully off when model.strategist_model is unset.
"""

import json
from types import SimpleNamespace

from agent.strategist import (
    DEFAULT_MAX_CALLS_PER_TASK,
    configure_strategist,
    consume_strategist_budget,
    record_strategist_event,
    should_request_plan,
    strategist_enabled,
)


def _agent(**kwargs):
    return SimpleNamespace(**kwargs)


def test_configure_reads_model_provider_and_cap():
    agent = _agent()
    configure_strategist(
        agent,
        {"strategist_model": " claude-fable-5 ", "strategist_provider": "anthropic"},
        {"strategist": {"max_calls_per_task": 5}},
    )
    assert agent.strategist_model == "claude-fable-5"
    assert agent.strategist_provider == "anthropic"
    assert agent.strategist_max_calls == 5
    assert agent._strategist_calls_used == 0
    assert strategist_enabled(agent) is True


def test_configure_defaults_off_and_survives_junk_config():
    agent = _agent()
    configure_strategist(agent, None, {"strategist": {"max_calls_per_task": "junk"}})
    assert agent.strategist_model is None
    assert strategist_enabled(agent) is False
    assert agent.strategist_max_calls == DEFAULT_MAX_CALLS_PER_TASK


def test_plan_only_for_multi_step_practical_tasks():
    # practical + multi-step markers -> plan
    assert should_request_plan(
        "Fix the gateway config, update the tests, and then open a PR",
        tools_available=True,
    )
    # practical but short single-step -> no plan
    assert not should_request_plan("fix the typo in README", tools_available=True)
    # question -> no plan ("does" would match the "do" action term, so pick a clean phrase)
    assert not should_request_plan("why is the gateway slow?", tools_available=True)
    # chit-chat -> no plan
    assert not should_request_plan("おはよう！", tools_available=True)
    # no tools -> never plan
    assert not should_request_plan(
        "Fix the config and then update the tests", tools_available=False
    )


def test_long_practical_message_triggers_plan():
    long_task = "implement the following changes carefully: " + "x" * 300
    assert should_request_plan(long_task, tools_available=True)


def test_budget_consumes_then_refuses():
    agent = _agent(strategist_max_calls=2, _strategist_calls_used=0)
    assert consume_strategist_budget(agent) is True
    assert consume_strategist_budget(agent) is True
    assert consume_strategist_budget(agent) is False
    assert agent._strategist_calls_used == 2


def test_event_rows_are_sanitized_metadata_only(tmp_path):
    path = tmp_path / "strategist_events.jsonl"
    record_strategist_event(
        event="plan", model="claude-fable-5", session_id="s1", path=path
    )
    record_strategist_event(
        event="escalate", model="claude-fable-5", cause_kind="verification_gap", path=path
    )
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["event"] == "plan"
    assert rows[1]["cause_kind"] == "verification_gap"
    for row in rows:
        assert set(row) <= {"event", "model", "cause_kind", "session_id", "timestamp"}
        assert row["timestamp"]


def test_record_event_never_raises(tmp_path):
    # unwritable target (a directory) must not raise
    target = tmp_path / "dir_as_file"
    target.mkdir()
    assert record_strategist_event(event="error", model="m", path=target) is None


class _FakeCompletion:
    def __init__(self, text):
        message = SimpleNamespace(content=text)
        self.choices = [SimpleNamespace(message=message)]


class _FakeClient:
    def __init__(self, text):
        self._text = text
        self.calls = []
        completions = SimpleNamespace(create=self._create)
        self.chat = SimpleNamespace(completions=completions)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeCompletion(self._text)


def _hybrid_agent(events_path=None):
    return SimpleNamespace(
        strategist_model="claude-fable-5",
        strategist_provider="anthropic",
        strategist_max_calls=3,
        _strategist_calls_used=0,
        _strategist_warned=False,
        session_id="s1",
        model="sinria-gpt-5.5",
        tools=[{"function": {"name": "bash"}}, {"function": {"name": "todo"}}],
        capability_profile=SimpleNamespace(tier="large", max_iterations_cap=90),
        _todo_store=None,
    )


def test_maybe_request_plan_calls_client_and_records_event(monkeypatch, tmp_path):
    from agent import strategist as mod

    fake = _FakeClient("1. read config\n2. edit\n3. verify with tests")
    monkeypatch.setattr(
        mod, "_resolve_strategist_client", lambda agent: (fake, None)
    )
    events = tmp_path / "events.jsonl"
    monkeypatch.setattr(mod, "strategist_events_path", lambda home=None: events)
    agent = _hybrid_agent()

    plan = mod.maybe_request_plan(
        agent, "Fix the gateway config, update tests, and then open a PR"
    )
    assert plan.startswith("1.")
    assert agent._strategist_calls_used == 1
    packet = fake.calls[0]["messages"][1]["content"]
    assert "Fix the gateway config" in packet
    assert "bash" in packet  # tool names present
    assert fake.calls[0]["max_tokens"] == mod.PLAN_MAX_TOKENS
    rows = [json.loads(l) for l in events.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["event"] == "plan"


def test_maybe_request_plan_skips_simple_or_disabled(monkeypatch):
    from agent import strategist as mod

    agent = _hybrid_agent()
    assert mod.maybe_request_plan(agent, "fix typo in README") is None
    agent.strategist_model = None
    assert (
        mod.maybe_request_plan(agent, "Refactor a, then b, then c") is None
    )


def test_maybe_request_correction_packet_has_evidence(monkeypatch, tmp_path):
    from agent import strategist as mod

    fake = _FakeClient("Run the test suite before claiming completion.")
    monkeypatch.setattr(
        mod, "_resolve_strategist_client", lambda agent: (fake, None)
    )
    events = tmp_path / "events.jsonl"
    monkeypatch.setattr(mod, "strategist_events_path", lambda home=None: events)
    agent = _hybrid_agent()

    guidance = mod.maybe_request_correction(
        agent,
        user_message="update the deploy script and verify it",
        final_response="Done! Everything is complete.",
        cause_kind="verification_gap",
    )
    assert "test suite" in guidance
    packet = fake.calls[0]["messages"][1]["content"]
    assert "Everything is complete" in packet
    assert "verification_gap" in packet
    rows = [json.loads(l) for l in events.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["event"] == "escalate"
    assert rows[-1]["cause_kind"] == "verification_gap"


def test_side_call_failure_returns_none_and_warns_once(monkeypatch, tmp_path):
    from agent import strategist as mod

    def _boom(agent):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(mod, "_resolve_strategist_client", _boom)
    events = tmp_path / "events.jsonl"
    monkeypatch.setattr(mod, "strategist_events_path", lambda home=None: events)
    agent = _hybrid_agent()

    task = "Fix the config, then update the tests, then commit"
    assert mod.maybe_request_plan(agent, task) is None
    assert agent._strategist_warned is True
    rows = [json.loads(l) for l in events.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["event"] == "error"


def test_budget_exhaustion_records_event_and_skips_call(monkeypatch, tmp_path):
    from agent import strategist as mod

    fake = _FakeClient("plan")
    monkeypatch.setattr(
        mod, "_resolve_strategist_client", lambda agent: (fake, None)
    )
    events = tmp_path / "events.jsonl"
    monkeypatch.setattr(mod, "strategist_events_path", lambda home=None: events)
    agent = _hybrid_agent()
    agent._strategist_calls_used = 3  # cap reached

    task = "Fix the config, then update the tests, then commit"
    assert mod.maybe_request_plan(agent, task) is None
    assert fake.calls == []
    rows = [json.loads(l) for l in events.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["event"] == "budget_exhausted"


def test_packet_is_clipped_to_char_cap(monkeypatch):
    from agent import strategist as mod

    fake = _FakeClient("plan")
    monkeypatch.setattr(
        mod, "_resolve_strategist_client", lambda agent: (fake, None)
    )
    monkeypatch.setattr(
        mod, "record_strategist_event", lambda **kwargs: None
    )
    agent = _hybrid_agent()

    mod.maybe_request_plan(agent, "implement this now: " + "y" * 50_000)
    packet = fake.calls[0]["messages"][1]["content"]
    assert len(packet) <= mod.PACKET_CHAR_CAP + 32  # cap + truncation marker


# ── Finding 1: resolved/normalized model name propagation ─────────────────────

def test_resolved_model_name_is_used_in_api_call(monkeypatch, tmp_path):
    """When _resolve_strategist_client returns a normalized model, that name
    must be passed to client.chat.completions.create, not the raw config string."""
    from agent import strategist as mod

    fake = _FakeClient("1. step one\n2. step two")
    monkeypatch.setattr(
        mod, "_resolve_strategist_client", lambda agent: (fake, "normalized-model")
    )
    monkeypatch.setattr(mod, "strategist_events_path", lambda home=None: tmp_path / "e.jsonl")
    agent = _hybrid_agent()
    agent.strategist_model = "anthropic/claude-fable-5"  # raw config spelling

    mod.maybe_request_plan(agent, "Fix the config, then update the tests, then commit")
    assert fake.calls, "client must have been called"
    assert fake.calls[0]["model"] == "normalized-model"


def test_none_resolved_model_falls_back_to_raw_config(monkeypatch, tmp_path):
    """When _resolve_strategist_client returns None as the resolved model,
    fall back to agent.strategist_model (the raw config string)."""
    from agent import strategist as mod

    fake = _FakeClient("1. step one\n2. step two")
    monkeypatch.setattr(
        mod, "_resolve_strategist_client", lambda agent: (fake, None)
    )
    monkeypatch.setattr(mod, "strategist_events_path", lambda home=None: tmp_path / "e.jsonl")
    agent = _hybrid_agent()
    # strategist_model is "claude-fable-5" from _hybrid_agent()

    mod.maybe_request_plan(agent, "Fix the config, then update the tests, then commit")
    assert fake.calls, "client must have been called"
    assert fake.calls[0]["model"] == agent.strategist_model


# ── Finding 2: multimodal (list) user message handling ───────────────────────

def test_multimodal_short_text_plus_large_image_does_not_trigger_plan():
    """A list message with a short text part and a huge base64 image dict
    must NOT trigger a plan — the decision is based on extracted text length,
    not the full str() representation of the list."""
    from agent.strategist import should_request_plan

    big_base64 = "A" * 5000
    msg = [
        {"type": "text", "text": "what does this image show?"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{big_base64}"}},
    ]
    # str(msg) length >> 240 → would falsely trigger without the fix
    assert len(str(msg)) > 240
    # extracted text is short and not a multi-step practical request
    assert not should_request_plan(msg, tools_available=True)


def test_multimodal_multistep_text_fires_plan_without_base64_in_packet(monkeypatch, tmp_path):
    """A list message with a multi-step practical text part + image dict:
    plan should fire, and the packet must contain the text but not base64/image_url."""
    from agent import strategist as mod

    fake = _FakeClient("1. fix config\n2. update tests\n3. commit")
    monkeypatch.setattr(
        mod, "_resolve_strategist_client", lambda agent: (fake, None)
    )
    monkeypatch.setattr(mod, "strategist_events_path", lambda home=None: tmp_path / "e.jsonl")
    agent = _hybrid_agent()

    big_base64 = "B" * 2000
    msg = [
        {"type": "text", "text": "Fix the config, then update the tests, then commit"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{big_base64}"}},
    ]

    plan = mod.maybe_request_plan(agent, msg)
    assert plan is not None, "plan must fire for multi-step practical text"
    packet = fake.calls[0]["messages"][1]["content"]
    assert "Fix the config" in packet
    assert "base64" not in packet
    assert "image_url" not in packet
