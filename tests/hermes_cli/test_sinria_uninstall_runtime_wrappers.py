from pathlib import Path

import hermes_cli.uninstall as uninstall


def test_remove_wrapper_script_uses_active_cli_name(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    wrapper = tmp_path / ".local" / "bin" / "sinria"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\npython -m hermes_cli.main\n", encoding="utf-8")

    removed = uninstall.remove_wrapper_script()

    assert wrapper in removed
    assert not wrapper.exists()


def test_remove_wrapper_script_leaves_other_cli_names(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    legacy = tmp_path / ".local" / "bin" / "hermes"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("#!/bin/sh\npython -m hermes_cli.main\n", encoding="utf-8")

    removed = uninstall.remove_wrapper_script()

    assert legacy not in removed
    assert legacy.exists()
