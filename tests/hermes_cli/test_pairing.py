from types import SimpleNamespace

from hermes_cli import pairing


class _EmptyStore:
    def list_pending(self):
        return []

    def list_approved(self):
        return []


class _RejectStore:
    def approve_code(self, platform, code):
        return None

    def _is_locked_out(self, platform):
        return False


def test_pairing_usage_uses_sinria(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setitem(__import__("sys").modules, "gateway.pairing", SimpleNamespace(PairingStore=lambda: _EmptyStore()))

    pairing.pairing_command(SimpleNamespace(pairing_action=None))
    out = capsys.readouterr().out
    assert "Usage: sinria pairing {list|approve|revoke|clear-pending}" in out
    assert "Run 'sinria pairing --help' for details." in out
    assert "hermes pairing" not in out


def test_pairing_invalid_code_hint_uses_sinria(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    pairing._cmd_approve(_RejectStore(), "discord", "abc123")
    out = capsys.readouterr().out
    assert "Run 'sinria pairing list' to see pending codes." in out
    assert "hermes pairing list" not in out


def test_pairing_lockout_reset_path_uses_sinria_home(monkeypatch, capsys):
    class _LockedStore:
        def approve_code(self, platform, code):
            return None

        def _is_locked_out(self, platform):
            return True

        def _load_json(self, path):
            return {"_lockout:discord": 0}

        def _rate_limit_path(self):
            return "ignored"

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    pairing._cmd_approve(_LockedStore(), "discord", "abc123")
    out = capsys.readouterr().out
    assert "~/.sinria/platforms/pairing/_rate_limits.json" in out
    assert "~/.hermes/platforms/pairing/_rate_limits.json" not in out
