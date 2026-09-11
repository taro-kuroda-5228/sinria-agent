#!/usr/bin/env python3
"""Purpose-scoped member-side LINE relay for a shared Sinria front door."""
from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.line_peer_relay_service import (  # noqa: E402
    _loopback_chat_url,
    LinePeerRelayError,
    LinePeerRelayStore,
    process_line_peer_relay,
)
from gateway.line_peer_routing import validate_line_peer_token  # noqa: E402
from dotenv import dotenv_values  # noqa: E402
from sinria_constants import get_sinria_home  # noqa: E402


_MAX_BODY_BYTES = 32_768


class _RelayServer(ThreadingHTTPServer):
    store: LinePeerRelayStore

    def server_close(self) -> None:
        try:
            self.store.close()
        finally:
            super().server_close()


def create_server(
    *,
    host: str,
    port: int,
    relay_token: str,
    member_id: str,
    instance_id: str,
    local_api_url: str,
    local_api_key: str,
    state_path: str | Path,
    request_fn: Callable[..., dict[str, Any]] | None = None,
) -> _RelayServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("LINE peer relay must bind to loopback")
    required = (relay_token, member_id, instance_id, local_api_url, local_api_key)
    if not all(str(item or "").strip() for item in required):
        raise ValueError("LINE peer relay configuration is incomplete")
    validate_line_peer_token(relay_token)
    _loopback_chat_url(local_api_url)
    store = LinePeerRelayStore(state_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            # Never put request paths, bodies, identifiers, or credentials in logs.
            _ = (format, args)
            sys.stderr.write("sinria-line-peer-relay: request handled\n")

        def _json(self, status: int, value: dict[str, Any]) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") == "/healthz":
                self._json(200, {
                    "ok": True, "service": "sinria-line-peer-relay",
                    "rawContextStored": False,
                })
            else:
                self._json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/line-peer-relay":
                self._json(404, {"ok": False, "error": "not_found"})
                return
            authorization = self.headers.get("Authorization", "")
            supplied = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
            if not supplied or not hmac.compare_digest(supplied, relay_token):
                self._json(401, {"ok": False, "error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > _MAX_BODY_BYTES:
                self._json(413, {"ok": False, "error": "invalid_body_size"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                kwargs: dict[str, Any] = {}
                if request_fn is not None:
                    kwargs["request_fn"] = request_fn
                receipt = process_line_peer_relay(
                    payload,
                    member_id=member_id,
                    instance_id=instance_id,
                    local_api_url=local_api_url,
                    local_api_key=local_api_key,
                    store=store,
                    **kwargs,
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"ok": False, "error": "invalid_json"})
                return
            except LinePeerRelayError:
                self._json(422, {"ok": False, "error": "relay_rejected"})
                return
            self._json(200, receipt)

    server = _RelayServer((host, port), Handler)
    server.store = store
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Sinria member-side LINE peer relay")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--state-path",
        default=str(get_sinria_home() / "private" / "line-peer" / "relay.sqlite3"),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    profile_env = {
        str(key): str(value or "")
        for key, value in dotenv_values(get_sinria_home() / ".env").items()
    }

    def setting(name: str, default: str = "") -> str:
        return str(os.getenv(name) or profile_env.get(name) or default)

    values = {
        "relay_token": setting("SINRIA_LINE_PEER_RELAY_TOKEN"),
        "member_id": setting("SINRIA_MEMBER_ID"),
        "instance_id": setting("SINRIA_INSTANCE_ID"),
        "local_api_url": setting("SINRIA_LOCAL_API_URL", "http://127.0.0.1:8642"),
        "local_api_key": setting("SINRIA_LOCAL_API_KEY"),
    }
    missing = [name for name, value in values.items() if not str(value or "").strip()]
    if missing:
        print(json.dumps({"ok": False, "error": "config_missing", "fields": missing}))
        return 2
    try:
        validate_line_peer_token(values["relay_token"])
        _loopback_chat_url(values["local_api_url"])
    except (ValueError, LinePeerRelayError) as exc:
        error = "weak_relay_token" if isinstance(exc, ValueError) else "invalid_local_api_url"
        print(json.dumps({"ok": False, "error": error}))
        return 2
    if args.check:
        print(json.dumps({
            "ok": True,
            "service": "sinria-line-peer-relay",
            "memberId": values["member_id"],
            "instanceId": values["instance_id"],
            "rawContextStored": False,
            "externalActionPerformed": False,
        }, ensure_ascii=False))
        return 0

    server = create_server(
        host=args.host,
        port=args.port,
        state_path=args.state_path,
        relay_token=values["relay_token"],
        member_id=values["member_id"],
        instance_id=values["instance_id"],
        local_api_url=values["local_api_url"],
        local_api_key=values["local_api_key"],
    )
    sys.stderr.write(f"sinria-line-peer-relay listening on {args.host}:{args.port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
