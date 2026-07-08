"""Runtime-home fallback tests for the Google Chat OAuth helper."""

import importlib.util
import sys
from pathlib import Path


OAUTH_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins/platforms/google_chat/oauth.py"
)


def _load_oauth_without_hermes_constants(monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_constants", None)
    spec = importlib.util.spec_from_file_location("google_chat_oauth_home_test", OAUTH_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_fallback_uses_explicit_hermes_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "runtime-home"))
    monkeypatch.delenv("HERMES_CLI_NAME", raising=False)

    module = _load_oauth_without_hermes_constants(monkeypatch)

    assert module._hermes_home() == tmp_path / "runtime-home"


def test_fallback_defaults_to_dot_sinria_for_bare_sinria_cli(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    module = _load_oauth_without_hermes_constants(monkeypatch)

    assert module._hermes_home() == Path.home() / ".sinria"
    assert module.display_hermes_home() == "~/.sinria"


def test_fallback_defaults_to_dot_hermes_for_upstream_hermes(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HERMES_CLI_NAME", raising=False)

    module = _load_oauth_without_hermes_constants(monkeypatch)

    assert module._hermes_home() == Path.home() / ".hermes"
    assert module.display_hermes_home() == "~/.hermes"
