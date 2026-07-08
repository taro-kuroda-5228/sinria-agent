from pathlib import Path

import hermes_cli.main as main


def test_gateway_prompt_and_update_check_docs_are_fork_neutral():
    gateway_doc = main._gateway_prompt.__doc__ or ""
    update_check_doc = main._cmd_update_check.__doc__ or ""

    assert "hermes update --gateway" not in gateway_doc
    assert "Used by update gateway mode" in gateway_doc
    assert "hermes update --check" not in update_check_doc
    assert "update --check" in update_check_doc


def test_dashboard_cleanup_comment_avoids_hermes_update_literal():
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "path `hermes update` uses to clean up stale dashboards" not in source
    assert "cleanup path update uses for stale dashboards" in source
