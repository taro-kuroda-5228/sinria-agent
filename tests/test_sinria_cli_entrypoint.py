import importlib


def test_sinria_cli_entrypoint_pins_native_runtime_env(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    for name in (
        "SINRIA_HOME",
        "SINRIA_CLI_NAME",
        "HERMES_HOME",
        "HERMES_CLI_NAME",
        "HERMES_DISABLE_ACTIVE_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)

    main_mod = importlib.import_module("sinria_cli.main")
    main_mod._pin_sinria_runtime_env()

    assert main_mod.os.environ["SINRIA_CLI_NAME"] == "sinria"
    assert main_mod.os.environ["HERMES_CLI_NAME"] == "sinria"
    assert main_mod.os.environ["SINRIA_HOME"] == str(tmp_path / ".sinria")
    assert main_mod.os.environ["HERMES_HOME"] == str(tmp_path / ".sinria")
    assert main_mod.os.environ["HERMES_DISABLE_ACTIVE_PROFILE"] == "1"
