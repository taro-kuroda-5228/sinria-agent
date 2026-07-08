from pathlib import Path

import hermes_cli.auth_commands as auth_commands
import hermes_cli.debug as debug
import hermes_cli.dump as dump


def test_auth_commands_source_avoids_selected_hardcoded_hermes_auth_phrasing():
    source = Path(auth_commands.__file__).read_text(encoding="utf-8")
    assert "`hermes auth" not in source
    assert "Every credential source Hermes reads from" not in source
    assert "when `hermes auth` is called bare" not in source
    assert "Every credential source the CLI reads from" in source
    assert "when `auth` is called bare" in source


def test_debug_source_avoids_selected_hardcoded_hermes_debug_literals():
    source = Path(debug.__file__).read_text(encoding="utf-8")
    assert "Hermes version" not in source
    assert "`hermes debug share`" not in source
    assert "----HermesDebugBoundary9f3c" not in source
    assert "Run ``hermes dump`` and return its stdout as a string." not in source
    assert "CLI version" in source
    assert "`debug share`" in source
    assert "----CliDebugBoundary9f3c" in source
    assert "Run ``dump`` and return its stdout as a string." in source
    assert 'return f"{_cli_command_name()} debug delete {url}"' in source


def test_dump_source_avoids_selected_hardcoded_hermes_dump_literals():
    source = Path(dump.__file__).read_text(encoding="utf-8")
    assert "``hermes dump`` formats empty values as blank" not in source
    assert 'lines.append("--- hermes dump ---")' not in source
    assert "``dump`` formats empty values as blank" in source
    assert 'lines.append(f"--- {_cli_command_name()} dump ---")' in source
