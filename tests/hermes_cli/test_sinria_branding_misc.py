from types import SimpleNamespace

import pytest


def test_sinria_update_banner(monkeypatch, capsys):
    import hermes_cli.main as main

    class StopHere(RuntimeError):
        pass

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(main, "_install_hangup_protection", lambda gateway_mode=False: None)
    monkeypatch.setattr(main, "_finalize_update_output", lambda state: None)
    monkeypatch.setattr(main, "_run_pre_update_backup", lambda args: (_ for _ in ()).throw(StopHere()))
    monkeypatch.setattr("hermes_cli.config.is_managed", lambda: False)

    with pytest.raises(StopHere):
        main.cmd_update(SimpleNamespace())

    out = capsys.readouterr().out
    assert "Updating Sinria Agent" in out
    assert "Updating Hermes" not in out


def test_sinria_uninstall_banner_and_exit(monkeypatch, tmp_path, capsys):
    import hermes_cli.uninstall as uninstall

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(uninstall, "get_project_root", lambda: tmp_path / "sinria")
    monkeypatch.setattr(uninstall, "get_hermes_home", lambda: tmp_path / ".sinria")
    monkeypatch.setattr(uninstall, "_is_default_hermes_home", lambda path: False)
    monkeypatch.setattr("builtins.input", lambda prompt='': "3")

    uninstall.run_uninstall(SimpleNamespace())

    out = capsys.readouterr().out
    assert "Sinria Agent Uninstaller" in out
    assert "Sinria Uninstaller" not in out
    assert "Uninstall cancelled." in out



def test_sinria_uninstall_keep_data_message_uses_sinria(monkeypatch, tmp_path, capsys):
    import hermes_cli.uninstall as uninstall

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(uninstall, "get_project_root", lambda: tmp_path / "sinria")
    monkeypatch.setattr(uninstall, "get_hermes_home", lambda: tmp_path / ".sinria")
    monkeypatch.setattr(uninstall, "_is_default_hermes_home", lambda path: False)
    inputs = iter(["1", "no"])
    monkeypatch.setattr("builtins.input", lambda prompt='': next(inputs))

    uninstall.run_uninstall(SimpleNamespace())

    out = capsys.readouterr().out
    assert "remove the Sinria code but keep your configuration and data" in out
    assert "remove the Hermes code but keep your configuration and data" not in out



def test_sinria_uninstall_runtime_steps_use_sinria_command(monkeypatch, tmp_path, capsys):
    import hermes_cli.uninstall as uninstall

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(uninstall, "get_project_root", lambda: tmp_path / "sinria")
    monkeypatch.setattr(uninstall, "get_hermes_home", lambda: tmp_path / ".sinria")
    monkeypatch.setattr(uninstall, "_is_default_hermes_home", lambda path: False)
    monkeypatch.setattr(uninstall, "uninstall_gateway_service", lambda: False)
    monkeypatch.setattr(uninstall, "remove_path_from_shell_configs", lambda: [])
    monkeypatch.setattr(uninstall, "_is_windows", lambda: False)
    monkeypatch.setattr(uninstall, "remove_wrapper_script", lambda: [])
    monkeypatch.setattr(uninstall, "shutil", SimpleNamespace(rmtree=lambda path: None))
    inputs = iter(["1", "yes"])
    monkeypatch.setattr("builtins.input", lambda prompt='': next(inputs))

    uninstall.run_uninstall(SimpleNamespace())

    out = capsys.readouterr().out
    assert "Removing sinria command..." in out
    assert "Removing hermes command..." not in out



def test_sinria_uninstall_windows_envvar_noop_uses_sinria_product(monkeypatch, tmp_path, capsys):
    import hermes_cli.uninstall as uninstall

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(uninstall, "get_project_root", lambda: tmp_path / "sinria")
    monkeypatch.setattr(uninstall, "get_hermes_home", lambda: tmp_path / ".sinria")
    monkeypatch.setattr(uninstall, "_is_default_hermes_home", lambda path: False)
    monkeypatch.setattr(uninstall, "uninstall_gateway_service", lambda: False)
    monkeypatch.setattr(uninstall, "remove_path_from_shell_configs", lambda: [])
    monkeypatch.setattr(uninstall, "_is_windows", lambda: True)
    monkeypatch.setattr(uninstall, "remove_path_from_windows_registry", lambda path: [])
    monkeypatch.setattr(uninstall, "remove_hermes_env_vars_windows", lambda: [])
    monkeypatch.setattr(uninstall, "remove_wrapper_script", lambda: [])
    monkeypatch.setattr(uninstall, "remove_portable_tooling_windows", lambda path: [])
    monkeypatch.setattr(uninstall, "shutil", SimpleNamespace(rmtree=lambda path: None))
    monkeypatch.setattr(uninstall.os.path, "expandvars", lambda s: s)
    inputs = iter(["1", "yes"])
    monkeypatch.setattr("builtins.input", lambda prompt='': next(inputs))

    uninstall.run_uninstall(SimpleNamespace())

    out = capsys.readouterr().out
    assert "No Sinria-set User env vars to remove" in out
    assert "No Hermes-set User env vars to remove" not in out



def test_sinria_top_level_help_examples(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    from hermes_cli._parser import build_top_level_parser

    parser, _subparsers, chat_parser = build_top_level_parser()
    out = parser.format_help()
    chat_help = chat_parser.format_help()

    assert "sinria setup                  Run setup wizard" in out
    assert "sinria config edit            Edit config in $EDITOR" in out
    assert "sinria gateway                Run messaging gateway" in out
    assert "Ignore ~/.sinria/config.yaml" in chat_help
    assert "hermes setup                  Run setup wizard" not in out
    assert "Ignore ~/.hermes/config.yaml" not in chat_help



def test_auxiliary_cli_cmd_uses_sinria(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    from agent.auxiliary_client import _cli_cmd

    assert _cli_cmd("auth") == "sinria auth"



def test_sinria_random_tip_renders_commands(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    import hermes_cli.tips as tips

    rendered = tips._render_tip("hermes dashboard --tui embeds the full Hermes TUI in your browser via ~/.hermes/.")

    assert "sinria dashboard --tui" in rendered
    assert "full Sinria TUI" in rendered
    assert "~/.sinria/" in rendered
    assert "hermes dashboard --tui" not in rendered



def test_login_command_uses_sinria(monkeypatch, capsys):
    import hermes_cli.auth as auth
    import pytest

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    with pytest.raises(SystemExit) as exc:
        auth.login_command(None)

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "The 'sinria login' command has been removed." in out
    assert "'sinria model' to select a provider, or 'sinria setup' for full setup." in out
    assert "hermes login" not in out
    assert "hermes model" not in out



def test_config_parse_warning_uses_sinria(monkeypatch, tmp_path, capsys):
    from hermes_cli import config as cfg_mod

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cfg_mod._CONFIG_PARSE_WARNED.clear()
    (tmp_path / "config.yaml").write_text("\tbroken:\n")

    cfg_mod.load_config()
    err = capsys.readouterr().err
    assert "sinria config:" in err
    assert "hermes config:" not in err



def test_config_azure_foundry_description_uses_sinria(monkeypatch):
    import importlib
    import hermes_cli.config as config_mod

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    config_mod = importlib.reload(config_mod)

    desc = config_mod.OPTIONAL_ENV_VARS["AZURE_FOUNDRY_BASE_URL"]["description"]
    assert "sinria model" in desc
    assert "hermes model" not in desc



def test_web_server_fastapi_title_uses_sinria(monkeypatch):
    import importlib
    import hermes_cli.web_server as web_server

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    web_server = importlib.reload(web_server)

    assert web_server.app.title == "Sinria Agent"



def test_main_custom_endpoint_warning_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr("hermes_cli.models.probe_api_models", lambda *args, **kwargs: {"probed_url": "https://example.invalid", "models": None})
    monkeypatch.setattr(main, "_prompt_custom_api_mode_selection", lambda *args, **kwargs: "")
    answers = iter(["https://example.invalid", "test-model", "", "Test Model"])
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda *args, **kwargs: "key-123")
    monkeypatch.setattr("hermes_cli.auth._save_model_choice", lambda *args, **kwargs: None)
    monkeypatch.setattr("hermes_cli.auth.deactivate_provider", lambda *args, **kwargs: None)
    monkeypatch.setattr("hermes_cli.config.save_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {})
    monkeypatch.setattr("hermes_cli.config.get_env_value", lambda *args, **kwargs: "")
    monkeypatch.setattr(main, "_save_custom_provider", lambda *args, **kwargs: None)

    main._model_flow_custom({})

    out = capsys.readouterr().out
    assert "Sinria Agent will still save it." in out
    assert "Hermes will still save it." not in out



def test_main_slack_usage_hint_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main
    from types import SimpleNamespace

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    rc = main.cmd_slack(SimpleNamespace(slack_command=None))

    assert rc == 1
    err = capsys.readouterr().err
    assert "Run `sinria slack manifest -h` for details." in err
    assert "Run `hermes slack manifest -h` for details." not in err



def test_main_web_build_soft_failure_hint_uses_sinria(monkeypatch, tmp_path, capsys):
    import hermes_cli.main as main
    from types import SimpleNamespace

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    (tmp_path / "package.json").write_text("{}")
    monkeypatch.setattr(main, "_web_ui_build_needed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/npm")
    monkeypatch.setattr(main, "_run_npm_install_deterministic", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="npm failed"))

    ok = main._build_web_ui(tmp_path, fatal=False)

    assert ok is False
    out = capsys.readouterr().out
    assert "(sinria web will not be available)" in out
    assert "(hermes web will not be available)" not in out



def test_cmd_chat_first_run_hint_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(main, "_has_any_provider_configured", lambda: False)
    monkeypatch.setattr("hermes_cli.setup.is_interactive_stdin", lambda: False)
    monkeypatch.setattr("hermes_cli.setup.print_noninteractive_setup_guidance", lambda reason: None)

    with pytest.raises(SystemExit) as exc:
        main.cmd_chat(SimpleNamespace())

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Sinria isn't configured yet" in out
    assert "Run:  sinria setup" in out
    assert "Run:  hermes setup" not in out
