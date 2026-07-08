from pathlib import Path

import hermes_cli.main as main


def test_rhel_path_helper_uses_sinria_fhs_link(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(main.sys, "platform", "linux")
    monkeypatch.setattr(main.os, "geteuid", lambda: 0)

    seen = {}

    class FakePath:
        def __init__(self, value):
            seen["path"] = value
        def is_symlink(self):
            return False
        def exists(self):
            return False

    monkeypatch.setattr(main, "Path", FakePath)

    main._ensure_fhs_path_guard()

    assert seen["path"] == "/usr/local/bin/sinria"
