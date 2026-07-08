import hermes_cli.main as main


def test_windows_update_docstrings_avoid_hermes_exe_literal():
    quarantine_doc = main._quarantine_running_cli_exe.__doc__ or ""
    cleanup_doc = main._cleanup_quarantined_exes.__doc__ or ""

    assert "running ``hermes.exe``" not in quarantine_doc
    assert "next hermes invocation" not in quarantine_doc
    assert "hermes.exe.old.*" not in cleanup_doc
    assert "every hermes invocation" not in cleanup_doc
    assert "running CLI shim" in quarantine_doc
    assert "every CLI invocation" in cleanup_doc
