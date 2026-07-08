from pathlib import Path

import hermes_cli.callbacks as callbacks
import hermes_cli.kanban_db as kanban_db
import hermes_cli.pt_input_extras as pt_input_extras


def test_callbacks_source_avoids_hardcoded_hermescli_reference_in_docstring():
    source = Path(callbacks.__file__).read_text(encoding="utf-8")
    assert "Each function takes the HermesCLI instance" not in source
    assert "Each function takes the main CLI instance" in source


def test_pt_input_extras_source_avoids_selected_hardcoded_hermes_prose():
    source = Path(pt_input_extras.__file__).read_text(encoding="utf-8")
    assert "key tuples Hermes already binds." not in source
    assert "never reach Hermes." not in source
    assert "key tuples the CLI already binds." in source
    assert "never reach the CLI." in source


def test_kanban_db_source_avoids_hardcoded_hermes_launch_prose():
    source = Path(kanban_db.__file__).read_text(encoding="utf-8")
    assert "Hermes is launched from a venv" not in source
    assert "the CLI is launched from a venv" in source
