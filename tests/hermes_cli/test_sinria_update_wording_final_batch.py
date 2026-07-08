from pathlib import Path

import hermes_cli.main as main


def test_kill_stale_dashboard_docs_are_more_fork_neutral():
    doc = main._kill_stale_dashboard_processes.__doc__ or ""
    assert "``hermes update``" not in doc
    assert "``hermes dashboard --stop``" not in doc
    assert "Called at the end of update" in doc
    assert "from ``dashboard --stop``" in doc


def test_main_source_neutralizes_selected_update_tail_comments():
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "# Fork detection and upstream management for `hermes update`" not in source
    assert "# ``hermes update`` runs on Windows." not in source
    assert "# Render path using display_hermes_home so the user sees ~/.hermes/..." not in source
    assert "every `hermes update` surfaces the issue" not in source
    assert "# Fork detection and upstream management for update" in source
    assert "# Update runs on Windows." in source
    assert "active runtime home" in source
    assert "every update surfaces the issue" in source
