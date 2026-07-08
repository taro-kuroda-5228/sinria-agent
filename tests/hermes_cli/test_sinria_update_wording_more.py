from pathlib import Path

import hermes_cli.main as main


def test_main_source_neutralizes_selected_bootstrap_and_update_comments():
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "(or ``hermes update``" not in source
    assert "the user can't run\n# ``hermes update`` to recover" not in source
    assert "(or an update" in source
    assert "the user can't run\n# update to recover" in source


def test_curator_recent_run_doc_avoids_hermes_update_literal():
    doc = main._print_curator_recent_run_notice.__doc__ or ""
    assert "``hermes update``" not in doc
    assert "Update is a high-attention surface" in doc
    assert "Subsequent update invocations" in doc
