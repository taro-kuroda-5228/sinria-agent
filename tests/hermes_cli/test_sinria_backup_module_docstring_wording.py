from pathlib import Path

import hermes_cli.backup as backup


def test_backup_source_avoids_duplicated_product_name_in_module_docstring():
    source = Path(backup.__file__).read_text(encoding="utf-8")
    assert "Backup and import commands for the Sinria/Sinria CLI." not in source
    assert "Backup and import commands for the CLI." in source
