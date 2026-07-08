import json
import subprocess
import sys

from scripts.sinria_context_share_verify import build_verification_commands, run_commands


def test_build_verification_commands_uses_current_interpreter():
    # Bare "python" is absent from cron/CI shells on this host (a repeatedly
    # logged autonomous-run failure class); the runner must not rely on PATH.
    commands = build_verification_commands()
    assert all(cmd[0] == sys.executable for cmd in commands)


def test_build_verification_commands_uses_serial_bounded_pytest_groups():
    commands = build_verification_commands()

    joined = "\n".join(" ".join(cmd) for cmd in commands)
    assert "tests/agent/context_share" in joined
    assert "tests/gateway/test_context_resolver_injection.py" in joined
    assert "-n" not in joined


def test_run_commands_reports_failures_without_raw_log_dump(monkeypatch):
    def fake_run(cmd, cwd, text, capture_output, timeout, env):
        return subprocess.CompletedProcess(cmd, 1, stdout="safe stdout", stderr="token=abcdef should not appear")

    monkeypatch.setattr(subprocess, "run", fake_run)

    report = run_commands([["python", "-m", "pytest", "missing"]], cwd="/tmp")

    serialized = json.dumps(report, ensure_ascii=False)
    assert report["commands"][0]["exit_code"] == 1
    assert "abcdef" not in serialized
    assert report["all_passed"] is False
