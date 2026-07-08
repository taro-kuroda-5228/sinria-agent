"""CLI handlers for the ``hermes proxy`` subcommand."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

from hermes_cli.proxy.adapters import ADAPTERS, get_adapter
from hermes_cli.proxy.server import (
    AIOHTTP_AVAILABLE,
    DEFAULT_HOST,
    DEFAULT_PORT,
    run_server,
)

logger = logging.getLogger(__name__)


def _cli_command_name() -> str:
    return (os.getenv("SINRIA_CLI_NAME") or os.getenv("HERMES_CLI_NAME") or "sinria").strip().lower() or "sinria"


def _login_cmd(provider: str) -> str:
    return f"{_cli_command_name()} login {provider}".strip()


def _proxy_cmd(command: str) -> str:
    return f"{_cli_command_name()} proxy {command}".strip()


def _print_aiohttp_missing() -> None:
    cli = _cli_command_name()
    print(
        f"{cli} proxy requires aiohttp. Install one of:\n"
        "  pip install 'sinria-agent[messaging]'\n"
        "  pip install aiohttp\n"
        f"Then verify available upstreams with `{_proxy_cmd('providers')}`.",
        file=sys.stderr,
    )


def cmd_proxy_start(args: Any) -> int:
    """Run the proxy server in the foreground.

    Returns process exit code (0 on clean shutdown).
    """
    if not AIOHTTP_AVAILABLE:
        _print_aiohttp_missing()
        return 1

    provider = getattr(args, "provider", None) or "nous"
    try:
        adapter = get_adapter(provider)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if not adapter.is_authenticated():
        print(
            f"Not logged into {adapter.display_name}. "
            f"Run `{_login_cmd(adapter.name)}` first.",
            file=sys.stderr,
        )
        return 2

    host = getattr(args, "host", None) or DEFAULT_HOST
    port = getattr(args, "port", None) or DEFAULT_PORT

    print(
        f"Starting {_cli_command_name()} proxy for {adapter.display_name}\n"
        f"  Listening on:  http://{host}:{port}/v1\n"
        f"  Forwarding to: (resolved per-request from your subscription)\n"
        f"  Use any bearer token in the client — the proxy attaches your real credential.\n"
        f"\n"
        f"Press Ctrl+C to stop.",
        file=sys.stderr,
    )

    try:
        asyncio.run(run_server(adapter, host=host, port=port))
    except KeyboardInterrupt:
        print("\nproxy: stopped", file=sys.stderr)
    except OSError as exc:
        print(f"proxy: failed to bind {host}:{port}: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_proxy_status(args: Any) -> int:
    """Print the status of each configured upstream adapter."""
    print(f"{_cli_command_name()} proxy upstream adapters\n")
    for name in sorted(ADAPTERS):
        adapter = get_adapter(name)
        if not adapter.is_authenticated():
            print(f"  [{name:8s}] {adapter.display_name} — not logged in")
            continue
        try:
            cred = adapter.get_credential()
        except Exception as exc:
            print(
                f"  [{name:8s}] {adapter.display_name} — credentials need attention "
                f"({exc})"
            )
            continue
        expires = f" (bearer expires {cred.expires_at})" if cred.expires_at else ""
        print(f"  [{name:8s}] {adapter.display_name} — ready{expires}")
    print(
        f"\nStart the proxy with: {_proxy_cmd('start [--provider <name>]')}"
    )
    return 0


def cmd_proxy_list_providers(args: Any) -> int:
    """List available proxy upstream providers."""
    print("Available proxy upstream providers:")
    for name in sorted(ADAPTERS):
        adapter = get_adapter(name)
        print(f"  {name}  — {adapter.display_name}")
    return 0


def cmd_proxy(args: Any) -> int:
    """Dispatch ``hermes proxy <subcommand>``."""
    sub = getattr(args, "proxy_command", None)
    if sub == "start":
        return cmd_proxy_start(args)
    if sub == "status":
        return cmd_proxy_status(args)
    if sub in {"providers", "list"}:
        return cmd_proxy_list_providers(args)
    # No subcommand → print short help.
    print(
        f"{_cli_command_name()} proxy — local OpenAI-compatible proxy that attaches your\n"
        "OAuth-authenticated provider credentials to outbound requests.\n"
        "\n"
        "Subcommands:\n"
        f"  {_proxy_cmd('start [--provider nous] [--host 127.0.0.1] [--port 8645]')}\n"
        "      Run the proxy in the foreground.\n"
        f"  {_proxy_cmd('status')}\n"
        "      Show which upstream adapters are ready.\n"
        f"  {_proxy_cmd('providers')}\n"
        "      List available upstream providers.\n",
        file=sys.stderr,
    )
    return 0


__all__ = [
    "cmd_proxy",
    "cmd_proxy_start",
    "cmd_proxy_status",
    "cmd_proxy_list_providers",
]
