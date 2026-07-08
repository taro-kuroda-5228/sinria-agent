"""Runtime-home fallback tests for the achievements dashboard plugin."""

import importlib.util
import sys
from pathlib import Path


PLUGIN_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins/hermes-achievements/dashboard/plugin_api.py"
)


def _load_plugin_without_hermes_constants(monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_constants", None)
    spec = importlib.util.spec_from_file_location("achievements_plugin_home_test", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_fallback_uses_explicit_hermes_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "runtime-home"))
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    module = _load_plugin_without_hermes_constants(monkeypatch)

    assert module.get_hermes_home() == tmp_path / "runtime-home"


def test_fallback_defaults_to_dot_sinria_for_bare_sinria_cli(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    module = _load_plugin_without_hermes_constants(monkeypatch)

    assert module.get_hermes_home() == Path.home() / ".sinria"


def test_fallback_defaults_to_dot_hermes_for_upstream_hermes(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HERMES_CLI_NAME", raising=False)

    module = _load_plugin_without_hermes_constants(monkeypatch)

    assert module.get_hermes_home() == Path.home() / ".hermes"
