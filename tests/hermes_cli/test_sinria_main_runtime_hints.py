from types import SimpleNamespace

import pytest


def test_continue_missing_session_hint_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(main, "_resolve_session_by_name_or_id", lambda value: None)

    with pytest.raises(SystemExit) as exc:
        main.cmd_chat(SimpleNamespace(tui=False, continue_last="missing", resume=None))

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Use 'sinria sessions list' to see available sessions." in out
    assert "Use 'hermes sessions list' to see available sessions." not in out


def test_provider_key_clear_hint_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr("builtins.input", lambda prompt='': "c")
    monkeypatch.setattr("hermes_cli.config.save_env_value", lambda key, value: None)

    provider = SimpleNamespace(name="Test Provider", api_key_env_vars=["TEST_KEY"])
    key, cleared = main._prompt_api_key(provider, existing_key="sk-test-1234")

    assert key == ""
    assert cleared is True
    out = capsys.readouterr().out
    assert "Re-run `sinria setup` to configure Test Provider again." in out
    assert "Re-run `hermes setup`" not in out


def test_weixin_setup_guidance_uses_sinria_identity_and_home(monkeypatch, capsys):
    import hermes_cli.gateway as gateway

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(gateway, "get_env_value", lambda key: "")
    monkeypatch.setattr(gateway, "prompt_yes_no", lambda prompt, default=False: False)

    gateway._setup_weixin()

    out = capsys.readouterr().out
    assert "Sinria will open Tencent iLink QR login" in out
    assert "Sinria will store the returned account_id/token in ~/.sinria/.env." in out
    assert "Hermes will open Tencent iLink QR login" not in out
    assert "~/.hermes/.env" not in out



def test_spotify_setup_saved_path_uses_sinria_home(monkeypatch, capsys):
    import hermes_cli.auth as auth

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(auth, "_is_remote_session", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt='': "client-id-123")
    monkeypatch.setattr("hermes_cli.config.save_env_value", lambda *args, **kwargs: None)

    result = auth._spotify_interactive_setup("http://127.0.0.1/callback")

    assert result == "client-id-123"
    out = capsys.readouterr().out
    assert "Saved SINRIA_SPOTIFY_CLIENT_ID to ~/.sinria/.env" in out
    assert "HERMES_SPOTIFY_CLIENT_ID" not in out
    assert "~/.hermes/.env" not in out



def test_tools_config_piper_hint_uses_sinria_home(monkeypatch, capsys):
    import hermes_cli.tools_config as tools_config

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(tools_config, "_print_info", lambda msg: print(msg))
    monkeypatch.setattr(tools_config, "_print_success", lambda msg: None)
    monkeypatch.setattr(tools_config, "_print_warning", lambda msg: None)
    monkeypatch.setattr(tools_config, "_pip_install", lambda *args, **kwargs: type("R", (), {"returncode": 0, "stderr": ""})())
    monkeypatch.setitem(__import__("sys").modules, "piper", object())

    tools_config._run_post_setup("piper")

    out = capsys.readouterr().out
    assert "/config.yaml" in out
    assert ".sinria" in out or "hermes_test" in out
    assert "~/.hermes/config.yaml" not in out



def test_claude_manual_setup_hint_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(
        "agent.anthropic_adapter.run_oauth_setup_token",
        lambda: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr("getpass.getpass", lambda prompt='': "")

    result = main._run_anthropic_oauth_flow(lambda key, value: None)

    assert result is False
    out = capsys.readouterr().out
    assert "Re-run:               sinria model" in out
    assert "Re-run:               hermes model" not in out
