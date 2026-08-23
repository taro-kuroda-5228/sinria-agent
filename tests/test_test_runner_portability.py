"""Portability contracts for the hermetic test runner."""

from pathlib import Path


RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "run_tests.sh"


def test_runner_finds_primary_checkout_venv_from_linked_worktree():
    source = RUNNER.read_text(encoding="utf-8")

    assert "git -C \"$REPO_ROOT\" rev-parse --git-common-dir" in source
    assert '"$MAIN_CHECKOUT_ROOT/.venv"' in source
    assert '"$MAIN_CHECKOUT_ROOT/venv"' in source


def test_runner_prefers_sinria_owned_runtime_over_legacy_paths():
    source = RUNNER.read_text(encoding="utf-8")

    native_venv = '"$SINRIA_RUNTIME_HOME/sinria-agent/venv"'
    legacy_venv = '"$HOME/.hermes/sinria-agent/venv"'
    native_guard = '"$SINRIA_RUNTIME_HOME/pytest_live_guard.py"'
    legacy_guard = '"$HOME/.hermes/pytest_live_guard.py"'

    assert source.index(native_venv) < source.index(legacy_venv)
    assert source.index(native_guard) < source.index(legacy_guard)


def test_runner_raises_low_macos_file_descriptor_limit():
    source = RUNNER.read_text(encoding="utf-8")

    assert "ulimit -Sn 4096" in source
    assert "_CURRENT_NOFILE < 4096" in source
