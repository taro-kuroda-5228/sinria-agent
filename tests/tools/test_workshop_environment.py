"""Unit tests for the Workshop (Canonical/LXD sandbox) terminal backend.

The ``workshop`` CLI never runs in these tests: availability probes go
through a mocked ``subprocess.run`` and command spawning through a mocked
``_popen_bash``, mirroring the conventions in ``test_docker_environment.py``.
"""

import subprocess

import pytest

from tools.environments import workshop as workshop_env


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_probe_ok(monkeypatch):
    """Mock subprocess.run so every workshop CLI call succeeds.

    Returns the list of captured (cmd, kwargs) tuples.
    """
    calls = []

    def _run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(workshop_env.subprocess, "run", _run)
    return calls


def _mock_popen_bash(monkeypatch):
    """Mock _popen_bash with a harmless real bash process, capturing cmds."""
    calls = []

    def _fake(cmd, stdin_data=None, **kwargs):
        calls.append((list(cmd), stdin_data))
        return subprocess.Popen(
            ["bash", "-c", "exit 0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    monkeypatch.setattr(workshop_env, "_popen_bash", _fake)
    return calls


def _make_env(monkeypatch, **kwargs):
    """Construct a WorkshopEnvironment with CLI discovery and probe mocked."""
    monkeypatch.setattr(workshop_env, "find_workshop", lambda: "/snap/bin/workshop")
    probe_calls = _mock_probe_ok(monkeypatch)
    popen_calls = _mock_popen_bash(monkeypatch)
    env = workshop_env.WorkshopEnvironment(
        workshop_name=kwargs.pop("workshop_name", "poc"),
        **kwargs,
    )
    return env, probe_calls, popen_calls


# ---------------------------------------------------------------------------
# find_workshop
# ---------------------------------------------------------------------------


def test_find_workshop_env_override(monkeypatch, tmp_path):
    """TERMINAL_WORKSHOP_BINARY wins over PATH lookup."""
    fake = tmp_path / "workshop"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)

    monkeypatch.setenv("TERMINAL_WORKSHOP_BINARY", str(fake))
    workshop_env._reset_find_cache()

    assert workshop_env.find_workshop() == str(fake)


def test_find_workshop_path_lookup(monkeypatch):
    """Falls back to shutil.which('workshop')."""
    monkeypatch.delenv("TERMINAL_WORKSHOP_BINARY", raising=False)
    workshop_env._reset_find_cache()
    monkeypatch.setattr(
        workshop_env.shutil, "which",
        lambda name: "/snap/bin/workshop" if name == "workshop" else None,
    )

    assert workshop_env.find_workshop() == "/snap/bin/workshop"


def test_find_workshop_not_found_returns_none(monkeypatch):
    monkeypatch.delenv("TERMINAL_WORKSHOP_BINARY", raising=False)
    workshop_env._reset_find_cache()
    monkeypatch.setattr(workshop_env.shutil, "which", lambda name: None)

    assert workshop_env.find_workshop() is None


# ---------------------------------------------------------------------------
# Construction / availability probe
# ---------------------------------------------------------------------------


def test_init_raises_when_cli_missing(monkeypatch):
    """Without the workshop CLI, fail fast with an install hint."""
    monkeypatch.setattr(workshop_env, "find_workshop", lambda: None)
    monkeypatch.setattr(
        workshop_env.subprocess, "run",
        lambda *a, **k: pytest.fail("subprocess.run must not be called when CLI missing"),
    )

    with pytest.raises(RuntimeError) as excinfo:
        workshop_env.WorkshopEnvironment(workshop_name="poc")

    msg = str(excinfo.value)
    assert "workshop" in msg.lower()
    assert "snap install" in msg


def test_init_raises_on_empty_name(monkeypatch):
    monkeypatch.setattr(workshop_env, "find_workshop", lambda: "/snap/bin/workshop")

    with pytest.raises(ValueError):
        workshop_env.WorkshopEnvironment(workshop_name="")


def test_init_raises_on_invalid_name(monkeypatch):
    """Shell-metacharacter names are rejected before any CLI call."""
    monkeypatch.setattr(workshop_env, "find_workshop", lambda: "/snap/bin/workshop")

    with pytest.raises(ValueError):
        workshop_env.WorkshopEnvironment(workshop_name="poc; rm -rf /")


def test_init_probes_with_exec_true(monkeypatch):
    """Availability probe is `workshop exec <name> -- true` (format-agnostic)."""
    env, probe_calls, _ = _make_env(monkeypatch)

    assert probe_calls, "expected an availability probe via subprocess.run"
    cmd, kwargs = probe_calls[0]
    assert cmd == ["/snap/bin/workshop", "exec", "poc", "--", "true"]
    assert kwargs.get("timeout")  # bounded probe, never hangs


def test_init_raises_when_probe_fails(monkeypatch):
    """A failing probe surfaces stderr so the operator can act."""
    monkeypatch.setattr(workshop_env, "find_workshop", lambda: "/snap/bin/workshop")

    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr='workshop "poc" does not exist'
        )

    monkeypatch.setattr(workshop_env.subprocess, "run", _run)

    with pytest.raises(RuntimeError) as excinfo:
        workshop_env.WorkshopEnvironment(workshop_name="poc")

    assert 'does not exist' in str(excinfo.value)


def test_auto_start_retries_probe_after_start(monkeypatch):
    """auto_start=True: probe fail -> workshop start <name> -> probe again."""
    monkeypatch.setattr(workshop_env, "find_workshop", lambda: "/snap/bin/workshop")
    _mock_popen_bash(monkeypatch)

    calls = []

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[1] == "exec" and len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="stopped")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(workshop_env.subprocess, "run", _run)

    workshop_env.WorkshopEnvironment(workshop_name="poc", auto_start=True)

    assert calls[0] == ["/snap/bin/workshop", "exec", "poc", "--", "true"]
    assert calls[1] == ["/snap/bin/workshop", "start", "poc"]
    assert calls[2] == ["/snap/bin/workshop", "exec", "poc", "--", "true"]


def test_no_auto_start_by_default(monkeypatch):
    """Without auto_start, a failing probe must not try to start the workshop."""
    monkeypatch.setattr(workshop_env, "find_workshop", lambda: "/snap/bin/workshop")

    calls = []

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="stopped")

    monkeypatch.setattr(workshop_env.subprocess, "run", _run)

    with pytest.raises(RuntimeError):
        workshop_env.WorkshopEnvironment(workshop_name="poc")

    assert all(c[1] != "start" for c in calls)


# ---------------------------------------------------------------------------
# Command shape
# ---------------------------------------------------------------------------


def test_run_bash_command_shape(monkeypatch):
    env, _, popen_calls = _make_env(monkeypatch)
    popen_calls.clear()

    env._run_bash("echo hi")

    cmd, stdin_data = popen_calls[0]
    assert cmd == [
        "/snap/bin/workshop", "exec", "poc", "--", "bash", "-c", "echo hi",
    ]
    assert stdin_data is None


def test_run_bash_login_shape(monkeypatch):
    env, _, popen_calls = _make_env(monkeypatch)
    popen_calls.clear()

    env._run_bash("echo hi", login=True)

    cmd, _ = popen_calls[0]
    assert cmd == [
        "/snap/bin/workshop", "exec", "poc", "--", "bash", "-l", "-c", "echo hi",
    ]


def test_stdin_mode_is_heredoc(monkeypatch):
    """stdin is embedded as a heredoc (workshop exec stdin relay unverified)."""
    env, _, _ = _make_env(monkeypatch)

    assert env._stdin_mode == "heredoc"


def test_default_cwd_is_project(monkeypatch):
    """Workshop mounts the launch directory at /project — adopt as default."""
    env, _, _ = _make_env(monkeypatch)

    assert env.cwd == "/project"


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


def test_cleanup_default_noop(monkeypatch):
    env, probe_calls, _ = _make_env(monkeypatch)
    probe_calls.clear()

    env.cleanup()

    assert probe_calls == []


def test_cleanup_stop_on_cleanup(monkeypatch):
    env, probe_calls, _ = _make_env(monkeypatch, stop_on_cleanup=True)
    probe_calls.clear()

    env.cleanup()

    assert ["/snap/bin/workshop", "stop", "poc"] in [c for c, _ in probe_calls]


# ---------------------------------------------------------------------------
# terminal_tool registration
# ---------------------------------------------------------------------------


def test_env_config_includes_workshop_keys(monkeypatch):
    from tools import terminal_tool

    monkeypatch.setenv("TERMINAL_ENV", "workshop")
    monkeypatch.setenv("TERMINAL_WORKSHOP_NAME", "poc")

    config = terminal_tool._get_env_config()

    assert config["env_type"] == "workshop"
    assert config["workshop_name"] == "poc"
    assert config["cwd"] == "/project"


def test_create_environment_workshop_requires_name(monkeypatch):
    from tools import terminal_tool

    with pytest.raises(ValueError) as excinfo:
        terminal_tool._create_environment(
            "workshop", image="", cwd="/project", timeout=60,
            container_config={"workshop_name": ""},
        )

    assert "TERMINAL_WORKSHOP_NAME" in str(excinfo.value)


def test_create_environment_workshop_constructs_backend(monkeypatch):
    from tools import terminal_tool

    captured = {}

    class _FakeWorkshopEnv:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        workshop_env, "WorkshopEnvironment", _FakeWorkshopEnv
    )

    result = terminal_tool._create_environment(
        "workshop", image="", cwd="/project", timeout=60,
        container_config={
            "workshop_name": "poc",
            "workshop_auto_start": True,
            "workshop_stop_on_cleanup": False,
        },
    )

    assert isinstance(result, _FakeWorkshopEnv)
    assert captured["workshop_name"] == "poc"
    assert captured["cwd"] == "/project"
    assert captured["auto_start"] is True
    assert captured["stop_on_cleanup"] is False


def test_unknown_env_error_lists_workshop():
    from tools import terminal_tool

    with pytest.raises(ValueError) as excinfo:
        terminal_tool._create_environment(
            "nope", image="", cwd="/", timeout=60,
        )

    assert "workshop" in str(excinfo.value)


def test_cached_env_not_reused_across_backend_switch(monkeypatch):
    """A cached local env must be retired when policy switches to workshop.

    Regression guard for the sandbox-bypass hole: dispatch sets
    TERMINAL_ENV=workshop for a healthcare task, but a previously-cached
    local environment for the same task_id would otherwise be silently
    reused — running the command outside the sandbox.
    """
    import json as _json

    from tools import terminal_tool

    created = []

    class _FakeEnv:
        def __init__(self, kind):
            self.kind = kind
            self.cleaned = False

        def execute(self, command, **kwargs):
            return {"output": self.kind, "returncode": 0}

        def cleanup(self):
            self.cleaned = True

    local_env = _FakeEnv("local")
    task_id = "switch-test"

    monkeypatch.setattr(terminal_tool, "_resolve_container_task_id", lambda t: t)
    monkeypatch.setattr(terminal_tool, "_active_environments", {task_id: local_env})
    monkeypatch.setattr(terminal_tool, "_env_signatures", {task_id: ("local", "")})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {})
    monkeypatch.setattr(
        terminal_tool, "_check_all_guards", lambda command, env_type: {"approved": True}
    )
    monkeypatch.setattr(
        terminal_tool,
        "_get_env_config",
        lambda: {
            "env_type": "workshop",
            "workshop_name": "poc",
            "workshop_auto_start": False,
            "workshop_stop_on_cleanup": False,
            "cwd": "/project",
            "timeout": 60,
        },
    )
    monkeypatch.setattr(
        terminal_tool,
        "_create_environment",
        lambda **kwargs: (created.append(kwargs["env_type"]), _FakeEnv(kwargs["env_type"]))[1],
    )

    result = _json.loads(terminal_tool.terminal_tool(command="echo hi", task_id=task_id))

    assert created == ["workshop"], "expected a fresh workshop env, not the cached local one"
    assert result["output"] == "workshop"
    assert local_env.cleaned, "stale local env should be cleaned up"


def test_cached_env_reused_when_signature_matches(monkeypatch):
    """Same backend + same workshop name keeps reusing the cached env."""
    import json as _json

    from tools import terminal_tool

    class _FakeEnv:
        def execute(self, command, **kwargs):
            return {"output": "cached", "returncode": 0}

        def cleanup(self):  # pragma: no cover - must not be called
            raise AssertionError("cached env must not be cleaned on signature match")

    task_id = "stable-test"
    monkeypatch.setattr(terminal_tool, "_resolve_container_task_id", lambda t: t)
    monkeypatch.setattr(terminal_tool, "_active_environments", {task_id: _FakeEnv()})
    monkeypatch.setattr(
        terminal_tool, "_env_signatures", {task_id: ("workshop", "poc")}
    )
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {})
    monkeypatch.setattr(
        terminal_tool, "_check_all_guards", lambda command, env_type: {"approved": True}
    )
    monkeypatch.setattr(
        terminal_tool,
        "_get_env_config",
        lambda: {
            "env_type": "workshop",
            "workshop_name": "poc",
            "workshop_auto_start": False,
            "workshop_stop_on_cleanup": False,
            "cwd": "/project",
            "timeout": 60,
        },
    )
    monkeypatch.setattr(
        terminal_tool,
        "_create_environment",
        lambda **kwargs: pytest.fail("must reuse the cached env, not create a new one"),
    )

    result = _json.loads(terminal_tool.terminal_tool(command="echo hi", task_id=task_id))

    assert result["output"] == "cached"
