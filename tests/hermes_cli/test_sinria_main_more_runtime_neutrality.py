from pathlib import Path

import hermes_cli.main as main


def test_main_source_neutralizes_selected_runtime_home_comments():
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "~/.hermes/profiles/coder" not in source
    assert "systemd hardcodes HERMES_HOME=/root/.hermes" not in source
    assert "`hermes profile use`" not in source
    assert "user's ~/.hermes/config.yaml" not in source
    assert "OPENAI_BASE_URL in ~/.hermes/.env" not in source
    assert "Save to ~/.hermes/config.yaml." not in source
    assert "<runtime-root>/profiles/coder" in source
    assert "`profile use`" in source
    assert "runtime-home config.yaml" in source
    assert "runtime .env" in source
    assert "Save to the runtime config.yaml." in source


def test_main_docs_avoid_selected_hermes_update_literals():
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "causes the next ``hermes update`` to stash" not in source
    assert "``hermes update``, every profile is now current." not in source
    assert "know ``hermes update`` is still progressing" not in source
    assert "The ``--backup`` flag on ``hermes update``" not in source
    assert "causes the next update to stash" in source
    assert "update, every profile is now current." in source
    assert "know update is still progressing" in source
    assert "The ``--backup`` flag on update" in source
