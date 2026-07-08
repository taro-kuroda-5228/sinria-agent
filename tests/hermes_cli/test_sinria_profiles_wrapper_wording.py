from pathlib import Path

import hermes_cli.profiles as profiles


def test_profiles_source_avoids_selected_hermes_wrapper_literals():
    source = Path(profiles.__file__).read_text(encoding="utf-8")
    assert "Checks: reserved names, hermes subcommands, existing binaries in PATH." not in source
    assert 'if "hermes -p" in content:' not in source
    assert "Checks: reserved names, CLI subcommands, existing binaries in PATH." in source
    assert 'if f"{_cli_command_name()} -p" in content:' in source
