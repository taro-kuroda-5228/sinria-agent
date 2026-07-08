from pathlib import Path

import hermes_cli.profiles as profiles
import hermes_cli.tools_config as tools_config


def test_profiles_source_avoids_selected_hermes_runtime_literals_more():
    source = Path(profiles.__file__).read_text(encoding="utf-8")
    assert "# Directories/files to exclude when exporting the default (~/.hermes) profile." not in source
    assert "# Hermes subcommands that cannot be used as profile names/aliases" not in source
    assert "``~/.hermes``, profiles live under ``HERMES_HOME/profiles/``" not in source
    assert "In standard deployments this is ``~/.hermes``." not in source
    assert "outside ``~/.hermes``" not in source
    assert "special alias for ~/.hermes" not in source
    assert "Writes to ``~/.hermes/active_profile``" not in source
    assert "points to ``~/.hermes``" not in source
    assert "default runtime profile" in source
    assert "CLI subcommands" in source
    assert "default runtime home" in source
    assert "runtime-home ``active_profile`` marker" in source


def test_tools_config_source_avoids_selected_hermes_tools_literals_more():
    source = Path(tools_config.__file__).read_text(encoding="utf-8")
    assert "`hermes tools` → Video Generation" not in source
    assert "`hermes tools` → X (Twitter) Search" not in source
    assert "`hermes tools` checklist" not in source
    assert '``hermes tools`` → "reconfigure existing"' not in source
    assert "through ``hermes tools`` to flip the toolset on" not in source
    assert "saving via `hermes tools`" not in source
    assert "must opt in via `hermes tools`" not in source
    assert "once `hermes tools`" not in source
    assert "seen by hermes tools" not in source
    assert "Entry point for `hermes tools` and `hermes setup tools`" not in source
    assert "`tools` → Video Generation" in source
    assert "`tools` → X (Twitter) Search" in source
    assert "`tools` checklist" in source
    assert '``tools`` → "reconfigure existing"' in source
    assert "through ``tools`` to flip the toolset on" in source
    assert "saving via `tools`" in source
    assert "must opt in via `tools`" in source
    assert "once `tools`" in source
    assert "seen by tools" in source
    assert "Entry point for `tools` and `setup tools`" in source
