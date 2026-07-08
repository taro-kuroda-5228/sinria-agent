from __future__ import annotations

import os


def test_auth_commands_cli_name_accepts_sinria_native_env(monkeypatch):
    import hermes_cli.auth_commands as auth_commands

    monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
    monkeypatch.setenv("SINRIA_CLI_NAME", "sinria")

    assert auth_commands._cli_command_name() == "sinria"



def test_config_recommended_update_command_prefers_sinria_native_env(monkeypatch):
    from hermes_cli import config

    monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
    monkeypatch.setenv("SINRIA_CLI_NAME", "sinria")

    assert config.recommended_update_command_for_method("git") == "sinria update"



def test_inventory_warning_uses_sinria_native_env(monkeypatch):
    from hermes_cli.inventory import _apply_picker_hints

    monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
    monkeypatch.setenv("SINRIA_CLI_NAME", "sinria")

    rows = [{"slug": "custom-provider", "source": "canonical", "models": []}]
    _apply_picker_hints(rows)

    assert rows[0]["warning"] == "run `sinria model` to configure (api_key)"
    assert "hermes model" not in rows[0]["warning"]



def test_setup_module_docstring_no_longer_claims_hermes_home():
    import hermes_cli.setup as setup_mod

    assert "~/.hermes/" not in (setup_mod.__doc__ or "")
    assert "~/.sinria/" in (setup_mod.__doc__ or "") or "active runtime home" in (setup_mod.__doc__ or "")
