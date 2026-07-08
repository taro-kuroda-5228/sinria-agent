from pathlib import Path

import hermes_cli.auth as auth
import hermes_cli.gateway as gateway


def test_gateway_source_avoids_selected_hermes_process_comments():
    source = Path(gateway.__file__).read_text(encoding="utf-8")
    assert "Avoid treating Hermes and Sinria gateways as the same process." not in source
    assert "explicit Hermes markers above" not in source
    assert "known Hermes process" not in source
    assert "Profile-mode Hermes often sets ``HOME``" not in source
    assert "different product gateways as the same process" in source
    assert "explicit opposite-product markers" in source
    assert "known other-product process" in source
    assert "Profile mode often sets ``HOME``" in source


def test_auth_source_avoids_selected_hermes_legacy_comments():
    source = Path(auth.__file__).read_text(encoding="utf-8")
    assert "configuring anthropic in Hermes" not in source
    assert "with Hermes's refresh_token" not in source
    assert "Hermes's next refresh uses it" not in source
    assert "Fall back to legacy provider state" not in source
    assert "existing Hermes-owned credentials" not in source
    assert "users on older Hermes builds still see" not in source
    assert "configuring anthropic in the local CLI" in source
    assert "with the local refresh_token" in source
    assert "older provider-state path" in source
    assert "existing local-CLI-owned credentials" in source
    assert "users on older builds still see" in source
