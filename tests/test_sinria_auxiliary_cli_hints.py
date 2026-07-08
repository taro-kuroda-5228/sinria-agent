from agent import auxiliary_client as aux


def test_cli_cmd_uses_sinria_name(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    assert aux._cli_cmd("setup") == "sinria setup"
    assert aux._cli_cmd("model") == "sinria model"


def test_custom_endpoint_error_uses_cli_name(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    try:
        aux._validate_base_url("https://bad.example:abc")
    except RuntimeError as exc:
        msg = str(exc)
        assert "sinria setup" in msg
        assert "sinria model" in msg
        assert "hermes setup" not in msg
    else:
        raise AssertionError("expected malformed URL error")
