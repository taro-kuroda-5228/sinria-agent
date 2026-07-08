from pathlib import Path

import hermes_cli._parser as parser_mod
import hermes_cli.kanban as kanban


def test_parser_source_avoids_selected_hermes_cli_literals():
    source = Path(parser_mod.__file__).read_text(encoding="utf-8")
    assert "Top-level argparse construction for the hermes CLI." not in source
    assert "Mirrors `hermes chat --model ... --provider ...` semantics." not in source
    assert "Top-level argparse construction for the local CLI." in source
    assert "Mirrors `chat --model ... --provider ...` semantics." in source


def test_kanban_source_avoids_selected_hermes_profile_literals():
    source = Path(kanban.__file__).read_text(encoding="utf-8")
    assert "CLI for the Hermes Kanban board" not in source
    assert "shared across Hermes profiles" not in source
    assert "~/.hermes/profiles/" not in source
    assert "real Hermes profile" not in source
    assert "``<cli> kanban …``" in source
    assert "shared across profiles" in source
    assert "runtime-home profiles" in source
