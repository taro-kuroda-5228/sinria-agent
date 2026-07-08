from pathlib import Path

import pytest

import hermes_cli.auth as auth
import hermes_cli.doctor as doctor


def test_doctor_source_avoids_selected_hermes_runtime_literals():
    source = Path(doctor.__file__).read_text(encoding="utf-8")
    assert "Doctor command for hermes CLI." not in source
    assert "~/.hermes/.env" not in source
    assert "~/.hermes/config.yaml" not in source
    assert "Doctor command for the local CLI." in source
    assert "runtime .env" in source
    assert "runtime-home config.yaml" in source


def test_auth_error_hint_uses_sinria_model(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    hinted = auth.format_auth_error(
        auth.AuthError("Token expired", relogin_required=True)
    )
    assert "Run `sinria model` to re-authenticate." in hinted
    assert "hermes model" not in hinted


def test_xai_oauth_missing_messages_use_sinria_model(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    monkeypatch.setattr(auth, "_load_auth_store", lambda: {})
    monkeypatch.setattr(auth, "_load_provider_state", lambda store, provider: None)

    with pytest.raises(auth.AuthError) as exc:
        auth._read_xai_oauth_tokens(_lock=False)

    msg = str(exc.value)
    assert "`sinria model`" in msg
    assert "`hermes model`" not in msg
