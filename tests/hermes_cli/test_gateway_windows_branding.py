from pathlib import Path


def test_gateway_windows_task_description_uses_sinria(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    import importlib
    import hermes_cli.gateway_windows as gw

    gw = importlib.reload(gw)

    assert gw._TASK_DESCRIPTION == "Sinria Agent Gateway - Messaging Platform Integration"


def test_gateway_windows_next_steps_uses_sinria(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    import hermes_cli.gateway_windows as gw
    import hermes_cli.config as config_mod

    monkeypatch.setattr(config_mod, "get_hermes_home", lambda: tmp_path / ".sinria")
    gw._print_next_steps()

    out = capsys.readouterr().out
    assert "sinria gateway status" in out
    assert "hermes gateway status" not in out


def test_gateway_windows_status_install_hint_uses_sinria(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    import hermes_cli.gateway_windows as gw

    monkeypatch.setattr(gw, "_assert_windows", lambda: None)
    monkeypatch.setattr(gw, "get_task_name", lambda: "Hermes_Gateway")
    monkeypatch.setattr(gw, "is_task_registered", lambda: False)
    monkeypatch.setattr(gw, "is_startup_entry_installed", lambda: False)
    monkeypatch.setattr(gw, "_gateway_pids", lambda: [])
    monkeypatch.setattr(gw, "get_task_script_path", lambda: Path("C:/tmp/gateway.cmd"))
    monkeypatch.setattr(gw, "get_startup_entry_path", lambda: Path("C:/tmp/Sinria Gateway.cmd"))

    gw.status()

    out = capsys.readouterr().out
    assert "sinria gateway install" in out
    assert "hermes gateway install" not in out


def test_gateway_windows_task_name_stays_stable_for_compat(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    import hermes_cli.gateway_windows as gw

    monkeypatch.setattr(gw, "_assert_windows", lambda: None)
    assert gw.get_task_name() == "Hermes_Gateway"


def test_gateway_pairing_hints_use_sinria(monkeypatch, capsys):
    import hermes_cli.gateway as gateway

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    answers = iter([1])
    monkeypatch.setattr(gateway, "prompt_choice", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(gateway, "save_env_value", lambda *args, **kwargs: None)

    monkeypatch.setattr(gateway, "get_env_value", lambda *args, **kwargs: "")
    def _prompt(prompt_text, *args, **kwargs):
        if "Bot token" in str(prompt_text):
            return "token-123"
        return ""

    monkeypatch.setattr(gateway, "prompt", _prompt)

    gateway._setup_standard_platform({
        "key": "telegram",
        "label": "Telegram",
        "emoji": "📨",
        "token_var": "TELEGRAM_BOT_TOKEN",
        "allowed_users_var": "TELEGRAM_ALLOWED_USERS",
        "vars": [
            {"name": "TELEGRAM_BOT_TOKEN", "prompt": "Bot token", "required": True, "help": "Bot token help"},
            {"name": "TELEGRAM_ALLOWED_USERS", "prompt": "Allowed users", "help": "Allowlist help", "is_allowlist": True},
        ],
    })

    out = capsys.readouterr().out
    assert "sinria pairing approve <platform> <code>" in out
    assert "hermes pairing approve <platform> <code>" not in out



def test_gateway_windows_failed_start_hint_uses_sinria(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    import hermes_cli.gateway_windows as gw
    import hermes_cli.config as config_mod

    monkeypatch.setattr(gw, "_wait_for_gateway_ready", lambda timeout_s=6.0, interval_s=0.4: [])
    monkeypatch.setattr(config_mod, "get_hermes_home", lambda: tmp_path / ".sinria")

    gw._report_gateway_start("Scheduled Task")

    out = capsys.readouterr().out
    assert "Then check status: sinria gateway status" in out
    assert "Then check status: hermes gateway status" not in out
