from pathlib import Path

import hermes_cli.auth as auth


def test_auth_source_avoids_selected_hermes_runtime_literals():
    source = Path(auth.__file__).read_text(encoding="utf-8")
    assert "is persisted in ~/.hermes/auth.json with cross-process file locking." not in source
    assert "# Check both os.environ and ~/.hermes/.env file" not in source
    assert "# Auth Store — persistence layer for ~/.hermes/auth.json" not in source
    assert "Tokens stored in ~/.hermes/auth.json." not in source
    assert "Persist MiniMax OAuth state to Hermes auth store (~/.hermes/auth.json)." not in source
    assert "Tokens live in ~/.hermes/auth/google_oauth.json" not in source
    assert "resulting client_id to ~/.hermes/.env" not in source
    assert "runtime auth store" in source
    assert "runtime .env file" in source
    assert "runtime auth/google_oauth.json path" in source


def test_auth_source_avoids_selected_hermes_login_literals():
    source = Path(auth.__file__).read_text(encoding="utf-8")
    assert "Run `hermes auth` to authenticate." not in source
    assert "Codex auth state is missing tokens. Run `hermes auth` to re-authenticate." not in source
    assert "Codex auth is missing refresh_token. Run `hermes auth` to re-authenticate." not in source
    assert "Open this URL to authorize Hermes:" not in source
    assert "Open this URL to authorize Hermes with xAI:" not in source
    assert "Starting Hermes login via MiniMax" not in source
    assert "Run `hermes model` and select MiniMax (OAuth)." not in source
    assert "Starting Hermes login via {pconfig.name}" not in source
    assert "Hermes is not logged into Nous Portal." not in source
    assert "`hermes auth status`" not in source
    assert "Re-authenticate with: hermes auth add nous" not in source
    assert "{_cli_command_name()} auth` to authenticate" in source
    assert "{_cli_command_name()} model` and select MiniMax (OAuth)." in source
    assert "authorize {_product_name()}" in source
    assert "authorize {_product_name()} with xAI" in source
    assert "Starting {_product_name()} login via MiniMax" in source
    assert "{_product_name()} is not logged into Nous Portal." in source
    assert "{_cli_command_name()} auth add nous" in source
