import hermes_cli.main as main


def test_cli_exe_shims_include_expected_windows_shims(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_is_windows", lambda: True)

    shims = main._cli_exe_shims(tmp_path)

    assert tmp_path / "hermes.exe" in shims
    assert tmp_path / "sinria-gateway.exe" in shims


def test_quarantine_helper_rename_is_generic(monkeypatch):
    doc = main._quarantine_running_cli_exe.__doc__ or ""
    assert "running CLI shim" in doc
    assert "running ``hermes.exe``" not in doc
