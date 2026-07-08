from pathlib import Path

import hermes_cli.main as main


def test_main_source_avoids_selected_profile_and_tools_comment_examples():
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "Bare `hermes profile`" not in source
    assert "# hermes tools list [--platform cli]" not in source
    assert "# hermes tools disable <name...> [--platform cli]" not in source
    assert "# hermes tools enable <name...> [--platform cli]" not in source
    assert "# Bare `profile` — show current profile status" in source
    assert "# tools list [--platform cli]" in source


def test_main_source_avoids_selected_dashboard_status_wording():
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "``hermes dashboard --status``" not in source
    assert "embedded `hermes --tui` via PTY/WebSocket" not in source
    assert "Dispatches ``hermes slack <subcommand>``." not in source
    assert "current process is excluded, but since ``dashboard --status``" in source
    assert "used after update." in source
    assert "embedded `{_cli_command_name()} --tui` via PTY/WebSocket" in source
    assert "Dispatches ``slack <subcommand>``." in source
