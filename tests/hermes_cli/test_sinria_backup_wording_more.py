from pathlib import Path

import hermes_cli.backup as backup


def test_backup_source_avoids_selected_hardcoded_hermes_command_examples():
    source = Path(backup.__file__).read_text(encoding="utf-8")
    assert '`hermes backup` / `sinria backup`' not in source
    assert '`hermes import` / `sinria import`' not in source
    assert '`hermes claw migrate`' not in source
    assert '``hermes import <archive>``' not in source
    assert '`backup` creates a zip archive' in source
    assert '`import` restores from a backup zip' in source
    assert '`claw migrate`' in source
    assert '``import <archive>``' in source
