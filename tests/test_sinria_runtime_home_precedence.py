from pathlib import Path

from tools import mcp_oauth


def test_mcp_oauth_fallback_runtime_home_prefers_sinria_home(monkeypatch, tmp_path):
    sinria_home = tmp_path / ".sinria-custom"
    hermes_home = tmp_path / ".hermes-custom"
    monkeypatch.setenv("SINRIA_HOME", str(sinria_home))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    assert mcp_oauth._fallback_runtime_home() == sinria_home


def test_mcp_oauth_fallback_runtime_home_uses_cli_name_when_no_explicit_home(monkeypatch, tmp_path):
    monkeypatch.delenv("SINRIA_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert mcp_oauth._fallback_runtime_home() == tmp_path / ".sinria"
