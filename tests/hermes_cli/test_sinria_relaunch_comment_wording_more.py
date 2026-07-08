from pathlib import Path

import hermes_cli.relaunch as relaunch


def test_relaunch_source_avoids_hardcoded_hermes_path_comment():
    source = Path(relaunch.__file__).read_text(encoding="utf-8")
    assert "Common causes: ``hermes`` not on PATH yet" not in source
    assert "Common causes: the CLI shim is not on PATH yet" in source
