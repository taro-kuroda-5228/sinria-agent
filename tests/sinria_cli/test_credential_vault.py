from __future__ import annotations

import pytest

from sinria_cli import credential_vault as vault_mod


class FakeBackend:
    display_name = "Fake OS Vault"

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def get(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def exists(self, service: str, account: str) -> bool:
        return (service, account) in self.values

    def delete(self, service: str, account: str) -> bool:
        return self.values.pop((service, account), None) is not None


def test_vault_round_trip_uses_fixed_sinria_service() -> None:
    backend = FakeBackend()
    vault = vault_mod.CredentialVault(backend=backend)

    vault.set("owel.password", "local-only-value")

    assert vault.exists("owel.password") is True
    assert vault.get_for_local_use("owel.password") == "local-only-value"
    assert backend.values == {
        (vault_mod.SINRIA_CREDENTIAL_SERVICE, "owel.password"): "local-only-value"
    }
    assert vault.delete("owel.password") is True
    assert vault.exists("owel.password") is False


@pytest.mark.parametrize(
    "alias",
    ["", " leading", "trailing ", "../value", "owel\npassword", "a" * 129],
)
def test_alias_validation_rejects_unsafe_names(alias: str) -> None:
    vault = vault_mod.CredentialVault(backend=FakeBackend())

    with pytest.raises(vault_mod.CredentialVaultError) as exc:
        vault.exists(alias)

    if alias:
        assert alias not in str(exc.value)


def test_backend_selection_supports_macos_and_windows(monkeypatch) -> None:
    monkeypatch.setattr(vault_mod, "MacOSKeychainBackend", lambda: "mac")
    monkeypatch.setattr(vault_mod, "WindowsCredentialManagerBackend", lambda: "windows")

    assert vault_mod.default_backend(platform_name="darwin") == "mac"
    assert vault_mod.default_backend(platform_name="win32") == "windows"


def test_backend_selection_fails_closed_on_unsupported_platform() -> None:
    with pytest.raises(vault_mod.CredentialVaultUnavailable) as exc:
        vault_mod.default_backend(platform_name="linux")

    assert "Linux" in str(exc.value)


def test_backend_exception_never_includes_value() -> None:
    class BrokenBackend(FakeBackend):
        def set(self, service: str, account: str, value: str) -> None:
            raise RuntimeError(f"backend rejected {value}")

    vault = vault_mod.CredentialVault(backend=BrokenBackend())
    sensitive_value = "must-not-escape"

    with pytest.raises(vault_mod.CredentialVaultError) as exc:
        vault.set("owel.password", sensitive_value)

    assert sensitive_value not in str(exc.value)
