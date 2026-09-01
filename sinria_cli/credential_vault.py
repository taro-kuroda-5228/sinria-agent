"""Sinria-native credential vault backed by the operating system.

Values are stored in macOS Keychain or Windows Credential Manager. This module
is intentionally not registered as an agent tool: callers may use a value only
inside a trusted local workflow and must never return it to a model, gateway,
or log sink.
"""

from __future__ import annotations

import ctypes
import re
import sys
from typing import Protocol

SINRIA_CREDENTIAL_SERVICE = "ai.sinria.credential-vault"
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MACOS_ITEM_NOT_FOUND = -25300
_WINDOWS_NOT_FOUND = 1168


class CredentialVaultError(RuntimeError):
    """A sanitized credential-vault failure safe for user-facing output."""


class CredentialVaultUnavailable(CredentialVaultError):
    """Raised when no supported operating-system vault is available."""


class CredentialBackend(Protocol):
    display_name: str

    def set(self, service: str, account: str, value: str) -> None: ...

    def get(self, service: str, account: str) -> str | None: ...

    def exists(self, service: str, account: str) -> bool: ...

    def delete(self, service: str, account: str) -> bool: ...


class MacOSKeychainBackend:
    """Generic-password storage using Apple's Security framework directly."""

    display_name = "macOS Keychain"

    def __init__(self) -> None:
        try:
            self._security = ctypes.CDLL(
                "/System/Library/Frameworks/Security.framework/Security"
            )
            self._core_foundation = ctypes.CDLL(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )
        except OSError as exc:
            raise CredentialVaultUnavailable("macOS Keychain is unavailable.") from exc
        self._configure_functions()

    def _configure_functions(self) -> None:
        void_p = ctypes.c_void_p
        uint32_p = ctypes.POINTER(ctypes.c_uint32)
        void_pp = ctypes.POINTER(void_p)

        self._security.SecKeychainFindGenericPassword.argtypes = [
            void_p,
            ctypes.c_uint32,
            void_p,
            ctypes.c_uint32,
            void_p,
            uint32_p,
            void_pp,
            void_pp,
        ]
        self._security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self._security.SecKeychainAddGenericPassword.argtypes = [
            void_p,
            ctypes.c_uint32,
            void_p,
            ctypes.c_uint32,
            void_p,
            ctypes.c_uint32,
            void_p,
            void_pp,
        ]
        self._security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self._security.SecKeychainItemModifyAttributesAndData.argtypes = [
            void_p,
            void_p,
            ctypes.c_uint32,
            void_p,
        ]
        self._security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        self._security.SecKeychainItemDelete.argtypes = [void_p]
        self._security.SecKeychainItemDelete.restype = ctypes.c_int32
        self._security.SecKeychainItemFreeContent.argtypes = [void_p, void_p]
        self._security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        self._core_foundation.CFRelease.argtypes = [void_p]
        self._core_foundation.CFRelease.restype = None

    @staticmethod
    def _encoded(text: str) -> tuple[bytes, ctypes.Array[ctypes.c_char]]:
        raw = text.encode("utf-8")
        return raw, ctypes.create_string_buffer(raw)

    def _find(
        self, service: str, account: str, *, include_value: bool
    ) -> tuple[int, ctypes.c_void_p, ctypes.c_void_p, int]:
        service_raw, service_buf = self._encoded(service)
        account_raw, account_buf = self._encoded(account)
        value_length = ctypes.c_uint32(0)
        value_data = ctypes.c_void_p()
        item_ref = ctypes.c_void_p()
        status = self._security.SecKeychainFindGenericPassword(
            None,
            len(service_raw),
            ctypes.cast(service_buf, ctypes.c_void_p),
            len(account_raw),
            ctypes.cast(account_buf, ctypes.c_void_p),
            ctypes.byref(value_length) if include_value else None,
            ctypes.byref(value_data) if include_value else None,
            ctypes.byref(item_ref),
        )
        return status, item_ref, value_data, value_length.value

    def set(self, service: str, account: str, value: str) -> None:
        value_raw = bytearray(value.encode("utf-8"))
        value_buf = (ctypes.c_char * len(value_raw)).from_buffer(value_raw)
        status, item_ref, _unused_data, _unused_length = self._find(
            service, account, include_value=False
        )
        try:
            if status == 0:
                result = self._security.SecKeychainItemModifyAttributesAndData(
                    item_ref,
                    None,
                    len(value_raw),
                    ctypes.cast(value_buf, ctypes.c_void_p),
                )
            elif status == _MACOS_ITEM_NOT_FOUND:
                service_raw, service_buf = self._encoded(service)
                account_raw, account_buf = self._encoded(account)
                result = self._security.SecKeychainAddGenericPassword(
                    None,
                    len(service_raw),
                    ctypes.cast(service_buf, ctypes.c_void_p),
                    len(account_raw),
                    ctypes.cast(account_buf, ctypes.c_void_p),
                    len(value_raw),
                    ctypes.cast(value_buf, ctypes.c_void_p),
                    None,
                )
            else:
                raise CredentialVaultError("macOS Keychain lookup failed.")
            if result != 0:
                raise CredentialVaultError("macOS Keychain write failed.")
        finally:
            ctypes.memset(value_buf, 0, len(value_raw))
            if item_ref:
                self._core_foundation.CFRelease(item_ref)

    def get(self, service: str, account: str) -> str | None:
        status, item_ref, value_data, value_length = self._find(
            service, account, include_value=True
        )
        if status == _MACOS_ITEM_NOT_FOUND:
            return None
        if status != 0:
            raise CredentialVaultError("macOS Keychain read failed.")
        try:
            raw = bytearray(ctypes.string_at(value_data, value_length))
            try:
                return raw.decode("utf-8")
            finally:
                raw[:] = b"\0" * len(raw)
        finally:
            if value_data:
                ctypes.memset(value_data, 0, value_length)
                self._security.SecKeychainItemFreeContent(None, value_data)
            if item_ref:
                self._core_foundation.CFRelease(item_ref)

    def exists(self, service: str, account: str) -> bool:
        status, item_ref, _unused_data, _unused_length = self._find(
            service, account, include_value=False
        )
        try:
            if status == _MACOS_ITEM_NOT_FOUND:
                return False
            if status != 0:
                raise CredentialVaultError("macOS Keychain lookup failed.")
            return True
        finally:
            if item_ref:
                self._core_foundation.CFRelease(item_ref)

    def delete(self, service: str, account: str) -> bool:
        status, item_ref, _unused_data, _unused_length = self._find(
            service, account, include_value=False
        )
        if status == _MACOS_ITEM_NOT_FOUND:
            return False
        if status != 0:
            raise CredentialVaultError("macOS Keychain lookup failed.")
        try:
            result = self._security.SecKeychainItemDelete(item_ref)
            if result != 0:
                raise CredentialVaultError("macOS Keychain delete failed.")
            return True
        finally:
            if item_ref:
                self._core_foundation.CFRelease(item_ref)


class WindowsCredentialManagerBackend:
    """Generic credential storage using the native Windows CredWrite API."""

    display_name = "Windows Credential Manager"

    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", ctypes.c_uint32),
            ("Type", ctypes.c_uint32),
            ("TargetName", ctypes.c_wchar_p),
            ("Comment", ctypes.c_wchar_p),
            ("LastWritten", ctypes.c_uint32 * 2),
            ("CredentialBlobSize", ctypes.c_uint32),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", ctypes.c_uint32),
            ("AttributeCount", ctypes.c_uint32),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", ctypes.c_wchar_p),
            ("UserName", ctypes.c_wchar_p),
        ]

    def __init__(self) -> None:
        win_dll_factory = getattr(ctypes, "WinDLL", None)
        get_last_error = getattr(ctypes, "get_last_error", None)
        if win_dll_factory is None or get_last_error is None:
            raise CredentialVaultUnavailable(
                "Windows Credential Manager is unavailable."
            )
        try:
            self._advapi32 = win_dll_factory("Advapi32.dll", use_last_error=True)
        except (AttributeError, OSError) as exc:
            raise CredentialVaultUnavailable(
                "Windows Credential Manager is unavailable."
            ) from exc
        self._get_last_error = get_last_error
        credential_p = ctypes.POINTER(self._CREDENTIALW)
        self._advapi32.CredWriteW.argtypes = [credential_p, ctypes.c_uint32]
        self._advapi32.CredWriteW.restype = ctypes.c_int
        self._advapi32.CredReadW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(credential_p),
        ]
        self._advapi32.CredReadW.restype = ctypes.c_int
        self._advapi32.CredDeleteW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        self._advapi32.CredDeleteW.restype = ctypes.c_int
        self._advapi32.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi32.CredFree.restype = None

    @staticmethod
    def _target(service: str, account: str) -> str:
        return f"{service}:{account}"

    def set(self, service: str, account: str, value: str) -> None:
        raw = bytearray(value.encode("utf-16-le"))
        blob = (ctypes.c_ubyte * len(raw)).from_buffer(raw)
        entry = self._CREDENTIALW()
        entry.Type = 1
        entry.TargetName = self._target(service, account)
        entry.CredentialBlobSize = len(raw)
        entry.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        entry.Persist = 2
        entry.UserName = account
        try:
            if not self._advapi32.CredWriteW(ctypes.byref(entry), 0):
                raise CredentialVaultError("Windows Credential Manager write failed.")
        finally:
            ctypes.memset(blob, 0, len(raw))

    def get(self, service: str, account: str) -> str | None:
        credential_p = ctypes.POINTER(self._CREDENTIALW)
        result = credential_p()
        if not self._advapi32.CredReadW(
            self._target(service, account), 1, 0, ctypes.byref(result)
        ):
            if self._get_last_error() == _WINDOWS_NOT_FOUND:
                return None
            raise CredentialVaultError("Windows Credential Manager read failed.")
        try:
            raw = ctypes.string_at(
                result.contents.CredentialBlob,
                result.contents.CredentialBlobSize,
            )
            return raw.decode("utf-16-le")
        finally:
            if result.contents.CredentialBlob:
                ctypes.memset(
                    result.contents.CredentialBlob,
                    0,
                    result.contents.CredentialBlobSize,
                )
            self._advapi32.CredFree(result)

    def exists(self, service: str, account: str) -> bool:
        credential_p = ctypes.POINTER(self._CREDENTIALW)
        result = credential_p()
        if not self._advapi32.CredReadW(
            self._target(service, account), 1, 0, ctypes.byref(result)
        ):
            if self._get_last_error() == _WINDOWS_NOT_FOUND:
                return False
            raise CredentialVaultError("Windows Credential Manager lookup failed.")
        try:
            return True
        finally:
            if result.contents.CredentialBlob:
                ctypes.memset(
                    result.contents.CredentialBlob,
                    0,
                    result.contents.CredentialBlobSize,
                )
            self._advapi32.CredFree(result)

    def delete(self, service: str, account: str) -> bool:
        if self._advapi32.CredDeleteW(self._target(service, account), 1, 0):
            return True
        if self._get_last_error() == _WINDOWS_NOT_FOUND:
            return False
        raise CredentialVaultError("Windows Credential Manager delete failed.")


def default_backend(platform_name: str | None = None) -> CredentialBackend:
    platform_name = platform_name or sys.platform
    if platform_name == "darwin":
        return MacOSKeychainBackend()
    if platform_name == "win32":
        return WindowsCredentialManagerBackend()
    raise CredentialVaultUnavailable(
        "Sinria Credential Vault currently supports macOS Keychain and Windows "
        "Credential Manager. Linux support is not enabled on this installation."
    )


class CredentialVault:
    """Validated, sanitized access to a native operating-system credential store."""

    def __init__(self, backend: CredentialBackend | None = None) -> None:
        self._backend = backend or default_backend()

    @property
    def backend_name(self) -> str:
        return self._backend.display_name

    @staticmethod
    def _validate_alias(alias: str) -> str:
        if not isinstance(alias, str) or not _ALIAS_RE.fullmatch(alias):
            raise CredentialVaultError(
                "Credential alias must be 1-128 characters using letters, numbers, "
                "dot, underscore, or hyphen."
            )
        return alias

    def set(self, alias: str, value: str) -> None:
        alias = self._validate_alias(alias)
        if not isinstance(value, str) or not value:
            raise CredentialVaultError("Credential value cannot be empty.")
        try:
            self._backend.set(SINRIA_CREDENTIAL_SERVICE, alias, value)
        except CredentialVaultError:
            raise
        except Exception:
            raise CredentialVaultError("OS credential store operation failed.") from None

    def get_for_local_use(self, alias: str) -> str | None:
        """Return a value only to trusted local code; never expose this via a tool."""
        alias = self._validate_alias(alias)
        try:
            return self._backend.get(SINRIA_CREDENTIAL_SERVICE, alias)
        except CredentialVaultError:
            raise
        except Exception:
            raise CredentialVaultError("OS credential store operation failed.") from None

    def exists(self, alias: str) -> bool:
        alias = self._validate_alias(alias)
        try:
            return self._backend.exists(SINRIA_CREDENTIAL_SERVICE, alias)
        except CredentialVaultError:
            raise
        except Exception:
            raise CredentialVaultError("OS credential store operation failed.") from None

    def delete(self, alias: str) -> bool:
        alias = self._validate_alias(alias)
        try:
            return self._backend.delete(SINRIA_CREDENTIAL_SERVICE, alias)
        except CredentialVaultError:
            raise
        except Exception:
            raise CredentialVaultError("OS credential store operation failed.") from None


def get_default_vault() -> CredentialVault:
    return CredentialVault()
