from pathlib import Path

import hermes_cli.backup as backup


def test_backup_source_avoids_remaining_selected_hermes_root_prose():
    source = Path(backup.__file__).read_text(encoding="utf-8")
    assert "relative to hermes root" not in source
    assert "inside hermes root" not in source
    assert "a hermes home would have" not in source
    assert "a hermes dir name" not in source
    assert "relative to the runtime root" in source
    assert "inside the runtime root" in source
    assert "a runtime home would have" in source
    assert "a runtime-home dir name" in source
