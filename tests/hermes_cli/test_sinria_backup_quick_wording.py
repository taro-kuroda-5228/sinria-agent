from pathlib import Path

import hermes_cli.backup as backup


def test_backup_source_avoids_hardcoded_hermes_quick_backup_prose():
    source = Path(backup.__file__).read_text(encoding="utf-8")
    assert 'used by /snapshot slash command and hermes backup --quick' not in source
    assert 'CLI entry point for hermes backup --quick.' not in source
    assert 'used by /snapshot slash command and backup --quick' in source
    assert 'CLI entry point for backup --quick.' in source
