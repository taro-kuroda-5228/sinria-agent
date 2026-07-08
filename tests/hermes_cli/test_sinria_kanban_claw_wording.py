from pathlib import Path

import hermes_cli.claw as claw
import hermes_cli.kanban_db as kanban_db


def test_kanban_db_source_avoids_remaining_selected_hardcoded_hermes_profile_prose():
    source = Path(kanban_db.__file__).read_text(encoding="utf-8")
    assert "rather than a Hermes" not in source
    assert "real Hermes profile" not in source
    assert "default Hermes root exists" not in source
    assert "rather than a named" in source
    assert "real named profile" in source
    assert "default runtime root exists" in source


def test_claw_source_avoids_selected_hardcoded_hermes_migration_prose():
    source = Path(claw.__file__).read_text(encoding="utf-8")
    assert 'Run the OpenClaw → Hermes migration.' not in source
    assert 'Pre-apply backup of the Hermes home' not in source
    assert 'restorable with `hermes import`.' not in source
    assert 'Run the OpenClaw → current-CLI migration.' in source
    assert 'Pre-apply backup of the runtime home' in source
    assert 'restorable with `import`.' in source
