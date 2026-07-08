from pathlib import Path

import hermes_cli.backup as backup
import hermes_cli.banner as banner
import hermes_cli.providers as providers


def test_banner_source_avoids_hardcoded_hermes_checkout_prose():
    source = Path(banner.__file__).read_text(encoding="utf-8")
    assert "Hermes checkout. Cached per-process." not in source
    assert "active checkout. Cached per-process." in source


def test_backup_source_avoids_hardcoded_hidden_hermes_config_path_example():
    source = Path(backup.__file__).read_text(encoding="utf-8")
    assert "Some tools zip as `.hermes/config.yaml` instead of `config.yaml`." not in source
    assert "Some tools zip the config under a hidden runtime-home directory instead of `config.yaml`." in source


def test_providers_source_avoids_selected_hardcoded_hermes_overlay_prose():
    source = Path(providers.__file__).read_text(encoding="utf-8")
    assert "**Hermes overlays**" not in source
    assert "# -- Hermes overlay" not in source
    assert '"""Hermes-specific provider metadata layered on top of models.dev."""' not in source
    assert "1. Hermes overlays" not in source
    assert "2. models.dev catalog + Hermes overlay" not in source
    assert "**Provider overlays**" in source
    assert "# -- Provider overlay" in source
    assert '"""CLI-specific provider metadata layered on top of models.dev."""' in source
    assert "1. Provider overlays" in source
    assert "2. models.dev catalog + overlay metadata" in source
