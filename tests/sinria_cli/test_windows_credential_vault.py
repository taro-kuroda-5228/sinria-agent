from __future__ import annotations

import ctypes

from sinria_cli import credential_vault as vault_mod


class FakeFunction:
    def __init__(self, implementation):
        self.implementation = implementation
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.implementation(*args)


class FakeAdvapi32:
    def __init__(self) -> None:
        self.last_write: tuple[str, str, str, int] | None = None
        self.deleted_target: str | None = None
        self.freed = False
        self._blob = None
        self._entry = None
        self.CredWriteW = FakeFunction(self._write)
        self.CredReadW = FakeFunction(self._read)
        self.CredDeleteW = FakeFunction(self._delete)
        self.CredFree = FakeFunction(self._free)

    def _write(self, entry_pointer, _flags):
        entry_type = vault_mod.WindowsCredentialManagerBackend._CREDENTIALW
        entry = ctypes.cast(entry_pointer, ctypes.POINTER(entry_type)).contents
        raw = ctypes.string_at(entry.CredentialBlob, entry.CredentialBlobSize)
        self.last_write = (
            entry.TargetName,
            entry.UserName,
            raw.decode("utf-16-le"),
            entry.Persist,
        )
        return 1

    def _read(self, target, _entry_type, _flags, output_pointer):
        entry_type = vault_mod.WindowsCredentialManagerBackend._CREDENTIALW
        raw = "windows-local-value".encode("utf-16-le")
        self._blob = ctypes.create_string_buffer(raw)
        self._entry = entry_type()
        self._entry.CredentialBlobSize = len(raw)
        self._entry.CredentialBlob = ctypes.cast(
            self._blob, ctypes.POINTER(ctypes.c_ubyte)
        )
        output = ctypes.cast(
            output_pointer, ctypes.POINTER(ctypes.POINTER(entry_type))
        )
        output[0] = ctypes.pointer(self._entry)
        return 1

    def _delete(self, target, _entry_type, _flags):
        self.deleted_target = target
        return 1

    def _free(self, _pointer):
        self.freed = True


def test_windows_backend_round_trip_contract_uses_native_credential_manager(
    monkeypatch,
) -> None:
    fake_dll = FakeAdvapi32()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: fake_dll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0, raising=False)
    backend = vault_mod.WindowsCredentialManagerBackend()

    backend.set("ai.sinria.credential-vault", "owel.password", "windows-local-value")

    assert fake_dll.last_write == (
        "ai.sinria.credential-vault:owel.password",
        "owel.password",
        "windows-local-value",
        2,
    )
    assert (
        backend.get("ai.sinria.credential-vault", "owel.password")
        == "windows-local-value"
    )
    assert fake_dll.freed is True
    assert backend.delete("ai.sinria.credential-vault", "owel.password") is True
    assert fake_dll.deleted_target == "ai.sinria.credential-vault:owel.password"
