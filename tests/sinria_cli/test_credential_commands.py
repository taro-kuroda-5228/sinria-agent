from __future__ import annotations

from sinria_cli import credential_commands


class FakeVault:
    backend_name = "Windows Credential Manager"

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, alias: str, value: str) -> None:
        self.values[alias] = value

    def exists(self, alias: str) -> bool:
        return alias in self.values

    def delete(self, alias: str) -> bool:
        return self.values.pop(alias, None) is not None


def test_set_prompts_locally_twice_and_never_prints_value(monkeypatch, capsys) -> None:
    vault = FakeVault()
    prompts = iter(["private-value", "private-value"])
    monkeypatch.setattr(credential_commands, "get_default_vault", lambda: vault)
    monkeypatch.setattr(credential_commands.getpass, "getpass", lambda _prompt: next(prompts))
    monkeypatch.setattr(credential_commands.sys.stdin, "isatty", lambda: True)

    assert credential_commands.dispatch(["credentials", "set", "owel.password"]) is True

    output = capsys.readouterr().out
    assert "private-value" not in output
    assert "owel.password" in output
    assert vault.values["owel.password"] == "private-value"


def test_set_refuses_noninteractive_input(monkeypatch, capsys) -> None:
    vault = FakeVault()
    monkeypatch.setattr(credential_commands, "get_default_vault", lambda: vault)
    monkeypatch.setattr(credential_commands.sys.stdin, "isatty", lambda: False)

    assert credential_commands.dispatch(["credentials", "set", "owel.password"]) is True

    output = capsys.readouterr().out
    assert "local terminal" in output
    assert vault.values == {}


def test_set_rejects_mismatched_confirmation(monkeypatch, capsys) -> None:
    vault = FakeVault()
    prompts = iter(["first", "second"])
    monkeypatch.setattr(credential_commands, "get_default_vault", lambda: vault)
    monkeypatch.setattr(credential_commands.getpass, "getpass", lambda _prompt: next(prompts))
    monkeypatch.setattr(credential_commands.sys.stdin, "isatty", lambda: True)

    assert credential_commands.dispatch(["credentials", "set", "owel.password"]) is True

    assert "did not match" in capsys.readouterr().out
    assert vault.values == {}


def test_status_reports_presence_without_retrieving_value(monkeypatch, capsys) -> None:
    vault = FakeVault()
    vault.values["owel.password"] = "never-print-me"
    monkeypatch.setattr(credential_commands, "get_default_vault", lambda: vault)

    assert credential_commands.dispatch(["credentials", "status", "owel.password"]) is True

    output = capsys.readouterr().out
    assert "stored" in output
    assert "never-print-me" not in output


def test_delete_requires_explicit_confirmation(monkeypatch, capsys) -> None:
    vault = FakeVault()
    vault.values["owel.password"] = "kept"
    monkeypatch.setattr(credential_commands, "get_default_vault", lambda: vault)
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    monkeypatch.setattr(credential_commands.sys.stdin, "isatty", lambda: True)

    assert credential_commands.dispatch(["credentials", "delete", "owel.password"]) is True

    assert "cancelled" in capsys.readouterr().out.lower()
    assert vault.exists("owel.password") is True


def test_non_credentials_command_falls_through() -> None:
    assert credential_commands.dispatch(["chat"]) is False
