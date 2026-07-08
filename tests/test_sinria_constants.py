"""Tests for Sinria-native constants compatibility aliases."""

from pathlib import Path


def test_sinria_constants_exports_native_home_aliases(tmp_path, monkeypatch):
    monkeypatch.setenv("SINRIA_CLI_NAME", "sinria")
    monkeypatch.setenv("SINRIA_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)

    import sinria_constants

    assert sinria_constants.get_sinria_home() == tmp_path
    assert sinria_constants.get_home() == tmp_path
    expected_display = "~/" + str(tmp_path.relative_to(Path.home())) if tmp_path.is_relative_to(Path.home()) else str(tmp_path)
    assert sinria_constants.display_home() == expected_display


def test_sinria_constants_keeps_legacy_imports_available():
    import sinria_constants

    assert callable(sinria_constants.get_hermes_home)
    assert callable(sinria_constants.get_sinria_home)
    assert callable(sinria_constants.get_dir)
