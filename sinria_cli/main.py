"""Sinria-native CLI entrypoint."""

from __future__ import annotations

import os
from pathlib import Path


def _pin_sinria_runtime_env() -> None:
    """Set Sinria product markers before importing shared CLI modules."""
    os.environ.setdefault("SINRIA_CLI_NAME", "sinria")
    os.environ.setdefault("HERMES_CLI_NAME", "sinria")
    os.environ.setdefault("SINRIA_HOME", str(Path.home() / ".sinria"))
    os.environ.setdefault("HERMES_HOME", os.environ["SINRIA_HOME"])
    os.environ.setdefault("HERMES_DISABLE_ACTIVE_PROFILE", "1")


def main() -> None:
    _pin_sinria_runtime_env()
    import sys

    from sinria_cli.credential_commands import dispatch as dispatch_credentials

    if dispatch_credentials(sys.argv[1:]):
        return
    from hermes_cli.main import main as _compat_main

    _compat_main()


if __name__ == "__main__":  # pragma: no cover
    main()
