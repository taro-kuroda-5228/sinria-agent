from pathlib import Path

import hermes_cli.auth as auth


def test_auth_source_avoids_selected_hermes_command_comment_literals():
    source = Path(auth.__file__).read_text(encoding="utf-8")
    assert "Historically ``hermes auth add nous`` wrote" not in source
    assert "``hermes auth add nous --label <name>``" not in source
    assert "via `hermes auth add nous --type oauth`" not in source
    assert "`hermes tools`" not in source
    assert "`hermes auth login/logout/add/remove`" not in source
    assert "where `hermes auth` stores credentials" not in source
    assert "`hermes model` store device_code tokens" not in source
    assert "Historically ``auth add nous`` wrote" in source
    assert "``auth add nous --label <name>``" in source
    assert "via `auth add nous --type oauth`" in source
    assert "where `auth` stores credentials" in source
    assert "`model` store device_code tokens" in source


def test_auth_source_avoids_selected_hermes_local_session_comment_literals():
    source = Path(auth.__file__).read_text(encoding="utf-8")
    assert "Hermes gets its own OAuth session" not in source
    assert "Save tokens to Hermes auth store" not in source
    assert "(Hermes creates its own local OAuth session)" not in source
    assert "Hermes-originated logins" not in source
    assert "Skip Hermes models" not in source
    assert "the local CLI gets its own OAuth session" in source
    assert "runtime auth store" in source
    assert "creates its own local OAuth session" in source
    assert "local-CLI-originated logins" in source
    assert 'Skip "hermes" model families' in source
