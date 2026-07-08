from pathlib import Path

import hermes_cli.main as main


def test_update_output_stream_doc_avoids_hermes_update_log_path():
    doc = main._UpdateOutputStream.__doc__ or ""
    assert "~/.hermes/logs/update.log" not in doc
    assert "CLI update" in doc
    assert "runtime update log file" in doc


def test_main_source_neutralizes_selected_update_wording():
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "Print a short heads-up about the skill curator after `hermes update`." not in source
    assert "Users commonly run ``hermes update`` in an SSH session" not in source
    assert "streamed output to ``~/.hermes/logs/update.log`` so nothing is lost." not in source
    assert "after an update." in source
    assert "runtime update log" in source
