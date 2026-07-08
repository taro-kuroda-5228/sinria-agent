from pathlib import Path

import hermes_cli._subprocess_compat as subprocess_compat
import hermes_cli.commands as commands
import hermes_cli.hooks as hooks
import hermes_cli.kanban_diagnostics as kanban_diagnostics
import hermes_cli.slack_cli as slack_cli
import hermes_cli.stdio as stdio


def test_helper_modules_avoid_selected_hardcoded_hermes_prose():
    subprocess_source = Path(subprocess_compat.__file__).read_text(encoding="utf-8")
    commands_source = Path(commands.__file__).read_text(encoding="utf-8")
    hooks_source = Path(hooks.__file__).read_text(encoding="utf-8")
    kanban_source = Path(kanban_diagnostics.__file__).read_text(encoding="utf-8")
    slack_source = Path(slack_cli.__file__).read_text(encoding="utf-8")
    stdio_source = Path(stdio.__file__).read_text(encoding="utf-8")

    assert "Hermes is developed on Linux / macOS" not in subprocess_source
    assert "for the Hermes CLI" not in commands_source
    assert "Hermes wire shape" not in hooks_source
    assert "works for Hermes profiles" not in kanban_source
    assert "the rest of Hermes" not in slack_source
    assert "for a Hermes deployment" not in slack_source
    assert "e.g. Hermes is running" not in stdio_source

    assert "The CLI is developed on Linux / macOS" in subprocess_source
    assert "for the CLI" in commands_source
    assert "dispatcher wire shape" in hooks_source
    assert "works for runtime profiles" in kanban_source
    assert "the rest of the CLI" in slack_source
    assert "for a typical deployment" in slack_source
    assert "e.g. the CLI is running" in stdio_source
