from pathlib import Path

import hermes_cli.kanban_db as kanban_db
import hermes_cli.security_advisories as security_advisories
import hermes_cli.stdio as stdio


def test_stdio_source_avoids_selected_hardcoded_hermes_prose():
    source = Path(stdio.__file__).read_text(encoding="utf-8")
    assert "Hermes's banners" not in source
    assert "Hermes's interactive" not in source
    assert "and Hermes's" not in source
    assert "Set this before launching Hermes" not in source
    assert "and Hermes picks it up automatically" not in source
    assert "Any subprocess Hermes spawns" not in source
    assert "Hermes venv Scripts directory" not in source
    assert "The CLI's banners" in source
    assert "the CLI's interactive" in source
    assert "and the CLI's" in source
    assert "Set this before launching the CLI" in source
    assert "and the CLI picks it up automatically" in source
    assert "Any subprocess the CLI spawns" in source
    assert "CLI venv Scripts directory" in source


def test_security_advisories_source_avoids_hardcoded_hermes_requires_comment():
    source = Path(security_advisories.__file__).read_text(encoding="utf-8")
    assert "Hermes requires 3.10+ but defensive." not in source
    assert "the CLI requires 3.10+ but defensive." in source


def test_kanban_db_source_avoids_selected_hardcoded_hermes_root_prose():
    source = Path(kanban_db.__file__).read_text(encoding="utf-8")
    assert "shared Hermes root" not in source
    assert "``hermes -p <profile>``" not in source
    assert "profile's Hermes home" not in source
    assert "``hermes kanban boards" not in source
    assert "``~/.hermes``" not in source
    assert "shared runtime root" in source
    assert "``-p <profile>``" in source
    assert "profile's runtime home" in source
    assert "``kanban boards" in source
    assert "default runtime home" in source
