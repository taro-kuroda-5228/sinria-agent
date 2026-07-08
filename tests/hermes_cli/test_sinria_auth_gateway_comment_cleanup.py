from pathlib import Path

import hermes_cli.auth as auth
import hermes_cli.gateway as gateway


def test_auth_source_avoids_selected_hermes_comment_literals():
    source = Path(auth.__file__).read_text(encoding="utf-8")
    assert "``~/.hermes/auth.json`` even when HERMES_HOME is set to a profile" not in source
    assert "``hermes auth add <provider>`` inside the profile" not in source
    assert "`hermes auth list` / `hermes status`" not in source
    assert "# Spotify auth — PKCE tokens stored in ~/.hermes/auth.json" not in source
    assert "Resolve runtime credentials from Hermes's own Codex token store." not in source
    assert "other Hermes processes" not in source
    assert "runtime-root auth store" in source
    assert "``auth add <provider>`` inside the profile" in source
    assert "`auth list` / `status`" in source
    assert "runtime auth store" in source
    assert "local Codex token store" in source
    assert "other local CLI processes" in source


def test_gateway_source_avoids_selected_hermes_install_path_comments():
    source = Path(gateway.__file__).read_text(encoding="utf-8")
    assert "older Hermes installs" not in source
    assert "older Hermes versions" not in source
    assert "# Default ~/.hermes → remap to target user's default" not in source
    assert "# Profile or subdir of ~/.hermes → preserve the relative structure" not in source
    assert "# Completely custom path (not under ~/.hermes) — keep as-is" not in source
    assert "older installs that predate the" in source
    assert "older versions that used a" in source
    assert "Default runtime home → remap to target user's default." in source
    assert "Profile or subdir of the default runtime home" in source
