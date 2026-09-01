"""Local-only CLI for Sinria Credential Vault.

There is deliberately no command that prints a stored value. Registration is
accepted only from an interactive local terminal, never from stdin piping or a
messaging gateway.
"""

from __future__ import annotations

import getpass
import sys

from sinria_cli.credential_vault import (
    CredentialVaultError,
    get_default_vault,
)

_COMMANDS = {"credentials", "credential-vault"}


def _help() -> None:
    print(
        "Usage:\n"
        "  sinria credentials backend\n"
        "  sinria credentials set <alias>\n"
        "  sinria credentials status <alias>\n"
        "  sinria credentials delete <alias>\n\n"
        "Values are stored in macOS Keychain or Windows Credential Manager. "
        "Sinria never prints stored values."
    )


def _require_local_terminal() -> bool:
    if sys.stdin.isatty():
        return True
    print(
        "Credential changes require a local terminal. "
        "Open Terminal on this device and run the command there."
    )
    return False


def dispatch(argv: list[str]) -> bool:
    """Handle credential-vault argv, returning False for unrelated commands."""
    if not argv or argv[0] not in _COMMANDS:
        return False

    if len(argv) == 1 or argv[1] in {"help", "-h", "--help"}:
        _help()
        return True

    action = argv[1]
    try:
        vault = get_default_vault()

        if action == "backend" and len(argv) == 2:
            print(f"Credential backend: {vault.backend_name}")
            return True

        if action == "set" and len(argv) == 3:
            if not _require_local_terminal():
                return True
            alias = argv[2]
            first = getpass.getpass("Credential value: ")
            second = getpass.getpass("Confirm value: ")
            if not first:
                print("Credential value cannot be empty; nothing was stored.")
                return True
            if first != second:
                print("Values did not match; nothing was stored.")
                return True
            vault.set(alias, first)
            print(f"Stored {alias} in {vault.backend_name}.")
            return True

        if action == "status" and len(argv) == 3:
            alias = argv[2]
            state = "stored" if vault.exists(alias) else "not stored"
            print(f"{alias}: {state} ({vault.backend_name})")
            return True

        if action == "delete" and len(argv) == 3:
            if not _require_local_terminal():
                return True
            alias = argv[2]
            answer = input(f"Delete {alias} from {vault.backend_name}? [y/N] ")
            if answer.strip().lower() not in {"y", "yes"}:
                print("Deletion cancelled.")
                return True
            deleted = vault.delete(alias)
            print(f"{alias}: {'deleted' if deleted else 'not stored'}")
            return True

        _help()
        return True
    except CredentialVaultError as exc:
        print(f"Credential Vault error: {exc}")
        return True
