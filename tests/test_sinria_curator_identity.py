from __future__ import annotations


def test_curator_cli_name_accepts_sinria_native_env(monkeypatch):
    import agent.curator as curator

    monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
    monkeypatch.setenv("SINRIA_CLI_NAME", "sinria")

    assert curator._cli_command_name() == "sinria"
    assert curator._curator_cli_command() == "sinria curator"



def test_curator_config_docstring_no_longer_hardcodes_hermes_home():
    import agent.curator as curator

    assert "~/.hermes/config.yaml" not in (curator._load_config.__doc__ or "")
    assert "active runtime config.yaml" in (curator._load_config.__doc__ or "")
