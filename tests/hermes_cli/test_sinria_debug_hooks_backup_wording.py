from pathlib import Path

import hermes_cli.backup as backup
import hermes_cli.debug as debug
import hermes_cli.hooks as hooks


def test_debug_source_avoids_selected_hermes_literals():
    source = Path(debug.__file__).read_text(encoding="utf-8")
    assert "``hermes debug`` debug tools for Sinria." not in source
    assert "~/.hermes/logs/*.log" not in source
    assert "~/.hermes/pastes/pending.json" not in source
    assert "Hermes team" not in source
    assert "runtime log files" in source
    assert "runtime-home ``pastes/pending.json``" in source
    assert "support team" in source


def test_hooks_usage_and_source_are_cli_neutral(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    hooks.hooks_command(type("Args", (), {"hooks_action": None})())
    out = capsys.readouterr().out
    assert "Usage: sinria hooks" in out
    assert "hermes hooks" not in out

    source = Path(hooks.__file__).read_text(encoding="utf-8")
    assert "~/.hermes/config.yaml" not in source
    assert "runtime-home config.yaml" in source


def test_backup_docstrings_avoid_hermes_backup_term():
    source = Path(backup.__file__).read_text(encoding="utf-8")
    assert "Create a zip backup of the Hermes home directory." not in source
    assert "Check that a zip looks like a Hermes backup." not in source
    assert "Restore a Hermes backup from a zip file." not in source
    assert "Create a zip backup of the active runtime home." in source
    assert "runtime-home backup" in source
    assert "update snapshots this set before pulling" in source
