from pathlib import Path

import hermes_cli.main as main


def test_require_tty_doc_avoids_hermes_command_examples():
    doc = main._require_tty.__doc__ or ""
    assert "hermes tools" not in doc
    assert "hermes setup" not in doc
    assert "hermes model" not in doc
    assert "Interactive TUI commands (tools, setup, model)" in doc


def test_profile_install_description_avoids_hermes_branding():
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "Install a Hermes profile distribution." not in source
    assert "Install a profile distribution." in source


def test_main_source_avoids_selected_runtime_home_help_literals():
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "Falls back to ~/.hermes/active_profile" not in source
    assert "Load .env from ~/.hermes/.env first" not in source
    assert "runtime root's active_profile file" in source
    assert "Load the runtime .env first" in source
