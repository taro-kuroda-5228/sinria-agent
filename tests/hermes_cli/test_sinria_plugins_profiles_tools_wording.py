from pathlib import Path

import hermes_cli.plugins_cmd as plugins_cmd
import hermes_cli.profiles as profiles
import hermes_cli.tools_config as tools_config


def test_plugins_cmd_source_is_runtime_neutral():
    source = Path(plugins_cmd.__file__).read_text(encoding="utf-8")
    assert "``hermes plugins`` CLI subcommand" not in source
    assert "``~/.hermes/plugins/``" not in source
    assert "other Hermes subprocess resolution" not in source
    assert "Dispatch hermes plugins subcommands." not in source
    assert "``plugins`` CLI subcommand" in source
    assert "runtime-home plugins directory" in source
    assert "local CLI subprocess resolution" in source
    assert "Dispatch plugins subcommands." in source


def test_profiles_source_is_runtime_neutral():
    source = Path(profiles.__file__).read_text(encoding="utf-8")
    assert "multiple isolated Hermes instances" not in source
    assert "Profiles live under ``~/.hermes/profiles/<name>/`` by default." not in source
    assert 'The "default" profile is ``~/.hermes`` itself' not in source
    assert "hermes profile create coder" not in source
    assert "multiple isolated CLI instances" in source
    assert "runtime-home profiles directory" in source
    assert 'The "default" profile is the runtime home itself' in source
    assert "<cli> profile create coder" in source


def test_tools_config_source_is_runtime_neutral():
    source = Path(tools_config.__file__).read_text(encoding="utf-8")
    assert "Unified tool configuration for Sinria." not in source
    assert "`hermes tools` and `hermes setup tools` both enter this module." not in source
    assert "Saves per-platform tool configuration to ~/.hermes/config.yaml" not in source
    assert "Hermes routes X searches" not in source
    assert "`hermes update`" not in source
    assert "terminal / Hermes process" not in source
    assert "to ~/.hermes/.env" not in source
    assert "Unified tool configuration for the local CLI." in source
    assert "`tools` and `setup tools` both enter this module." in source
    assert "runtime-home config" in source
    assert "{_tools_product_name()} routes X searches" in source
    assert "terminal / {_tools_product_name()} process" in source
    assert "runtime .env" in source
