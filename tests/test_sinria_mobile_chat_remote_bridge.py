from pathlib import Path

from scripts import sinria_mobile_chat_remote_bridge as bridge


class FakeAgent:
    def __init__(self):
        self.prompts = []

    def chat(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return f"reply:{prompt}"


def test_agent_runner_reuses_one_local_sinria_agent_instance_for_multiple_requests():
    created: list[FakeAgent] = []

    def factory() -> FakeAgent:
        agent = FakeAgent()
        created.append(agent)
        return agent

    runner = bridge.AgentSinriaRunner(agent_factory=factory)

    assert runner.run("one") == (True, "reply:one", "")
    assert runner.run("two") == (True, "reply:two", "")

    assert len(created) == 1
    assert created[0].prompts == ["one", "two"]


def test_runner_factory_selects_agent_backend_without_spawning_cli(monkeypatch):
    monkeypatch.setenv("SINRIA_MOBILE_CHAT_BACKEND", "agent")

    runner = bridge.make_sinria_runner(repo_root=Path("/tmp/sinria"), timeout=10)

    assert isinstance(runner, bridge.AgentSinriaRunner)


def test_runner_factory_keeps_cli_backend_as_explicit_compatibility(monkeypatch):
    monkeypatch.setenv("SINRIA_MOBILE_CHAT_BACKEND", "cli")

    runner = bridge.make_sinria_runner(repo_root=Path("/tmp/sinria"), timeout=10)

    assert isinstance(runner, bridge.CliSinriaRunner)
