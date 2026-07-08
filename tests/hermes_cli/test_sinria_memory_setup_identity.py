import importlib


def test_sinria_memory_setup_missing_provider_guidance_uses_sinria_command(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    import hermes_cli.memory_setup as memory_setup

    memory_setup = importlib.reload(memory_setup)
    monkeypatch.setattr(memory_setup, "_get_available_providers", lambda: [])

    memory_setup.cmd_setup_provider("missing-provider")

    output = capsys.readouterr().out
    assert "Run 'sinria memory setup'" in output
    assert "Run 'hermes memory setup'" not in output


def test_sinria_memory_setup_empty_provider_list_points_to_sinria_plugins(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.delenv("HERMES_HOME", raising=False)

    import hermes_cli.memory_setup as memory_setup

    memory_setup = importlib.reload(memory_setup)
    monkeypatch.setattr(memory_setup, "_get_available_providers", lambda: [])

    memory_setup.cmd_setup(None)

    output = capsys.readouterr().out
    assert "Install a plugin to ~/.sinria/plugins/" in output
    assert "~/.hermes/plugins/" not in output


def test_sinria_memory_status_missing_plugin_points_to_sinria_plugins(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.delenv("HERMES_HOME", raising=False)

    import hermes_cli.memory_setup as memory_setup

    memory_setup = importlib.reload(memory_setup)
    monkeypatch.setattr(memory_setup, "_get_available_providers", lambda: [])

    import hermes_cli.config as config_module

    monkeypatch.setattr(config_module, "load_config", lambda: {"memory": {"provider": "missing-provider"}})

    class Args:
        provider = "missing-provider"

    memory_setup.cmd_status(Args())

    output = capsys.readouterr().out
    assert "Install the 'missing-provider' memory plugin to ~/.sinria/plugins/" in output
    assert "~/.hermes/plugins/" not in output
