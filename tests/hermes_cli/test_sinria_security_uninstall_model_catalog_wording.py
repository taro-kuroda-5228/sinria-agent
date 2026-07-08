from pathlib import Path

import hermes_cli.model_catalog as model_catalog
import hermes_cli.security_advisories as security_advisories
import hermes_cli.uninstall as uninstall


def test_security_advisories_source_avoids_selected_hardcoded_hermes_doctor_prose():
    source = Path(security_advisories.__file__).read_text(encoding="utf-8")
    assert "``hermes doctor --ack <id>``" not in source
    assert "1. ``hermes doctor``" not in source
    assert "rest of Hermes failed to import" not in source
    assert "``doctor --ack <id>``" in source
    assert "1. ``doctor``" in source
    assert "rest of the CLI failed to import" in source
    assert "_cli_command_name()" in source


def test_uninstall_source_avoids_selected_hardcoded_hermes_owned_path_prose():
    source = Path(uninstall.__file__).read_text(encoding="utf-8")
    assert "Remove Hermes PATH entries from shell configuration files." not in source
    assert "identify Hermes-owned User-PATH entries." not in source
    assert "Strip Hermes-owned entries from User-scope PATH in the registry." not in source
    assert "Remove CLI-owned PATH entries from shell configuration files." in source
    assert "identify CLI-owned User-PATH entries." in source
    assert "Strip CLI-owned entries from User-scope PATH in the registry." in source


def test_model_catalog_source_avoids_hardcoded_legacy_hermes_cache_path_example():
    source = Path(model_catalog.__file__).read_text(encoding="utf-8")
    assert "(``~/.hermes/...`` remains a legacy compatibility alias)." not in source
    assert "(the legacy runtime-home cache path remains a compatibility alias)." in source
