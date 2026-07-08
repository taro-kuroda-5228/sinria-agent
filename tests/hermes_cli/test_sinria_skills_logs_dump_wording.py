from pathlib import Path

import hermes_cli.dump as dump_mod
import hermes_cli.logs as logs_mod
import hermes_cli.skills_config as skills_config


def test_skills_config_source_is_runtime_neutral():
    source = Path(skills_config.__file__).read_text(encoding="utf-8")
    assert "Skills configuration for Sinria." not in source
    assert "`hermes skills` enters this module." not in source
    assert "Config stored in ~/.hermes/config.yaml under:" not in source
    assert "Skills configuration for the local CLI." in source
    assert "`skills` enters this module." in source
    assert "runtime-home config" in source


def test_logs_source_is_runtime_neutral():
    source = Path(logs_mod.__file__).read_text(encoding="utf-8")
    assert "``hermes logs`` — view and filter Hermes log files." not in source
    assert "under ``~/.hermes/logs/``." not in source
    assert "    hermes logs" not in source
    assert "``logs`` — view and filter runtime log files." in source
    assert "runtime-home logs directory" in source
    assert "    <cli> logs" in source


def test_dump_source_is_runtime_neutral():
    source = Path(dump_mod.__file__).read_text(encoding="utf-8")
    assert "Dump command for hermes CLI." not in source
    assert "summary of the user's Hermes setup" not in source
    assert "Dump command for the local CLI." in source
    assert "summary of the active runtime setup" in source
