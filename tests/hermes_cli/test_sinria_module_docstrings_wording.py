from pathlib import Path

import hermes_cli
import hermes_cli.browser_connect as browser_connect
import hermes_cli.cli_output as cli_output
import hermes_cli.colors as colors
import hermes_cli.curses_ui as curses_ui


def test_module_docstrings_avoid_selected_hardcoded_hermes_branding():
    init_source = Path(hermes_cli.__file__).read_text(encoding="utf-8")
    browser_source = Path(browser_connect.__file__).read_text(encoding="utf-8")
    colors_source = Path(colors.__file__).read_text(encoding="utf-8")
    cli_output_source = Path(cli_output.__file__).read_text(encoding="utf-8")
    curses_source = Path(curses_ui.__file__).read_text(encoding="utf-8")

    assert "Sinria/Hermes compatibility CLI." not in init_source
    assert "upstream Hermes changes" not in init_source
    assert "attaching Hermes to a local Chrome CDP port" not in browser_source
    assert "for Hermes CLI modules" not in colors_source
    assert "for Hermes CLI modules" not in cli_output_source
    assert "Shared curses-based UI components for Hermes CLI." not in curses_source
    assert "Used by `hermes tools` and `hermes skills`" not in curses_source

    assert "Fork/rename-compatible CLI package." in init_source
    assert "upstream changes can be merged with low conflict" in init_source
    assert "attaching the CLI to a local Chrome CDP port" in browser_source
    assert "Shared ANSI color utilities for CLI modules." in colors_source
    assert "Shared CLI output helpers for CLI modules." in cli_output_source
    assert "Shared curses-based UI components for the CLI." in curses_source
    assert "Used by `tools` and `skills`" in curses_source
