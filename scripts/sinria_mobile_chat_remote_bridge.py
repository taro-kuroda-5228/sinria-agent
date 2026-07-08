#!/usr/bin/env python3
"""Local-only HTTPS-tunnel target for Company OS mobile chat.

The production Company OS app can call this process through an operator-approved
HTTPS tunnel. This process runs on the local Sinria machine, executes Sinria CLI,
and returns only the assistant reply. It does not log raw prompts.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Protocol

MAX_BODY_BYTES = 32_000
DEFAULT_TIMEOUT = 120


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("content-length", str(len(body)))
    handler.send_header("cache-control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _authorized(handler: BaseHTTPRequestHandler, secret: str) -> bool:
    header = handler.headers.get("authorization", "")
    return bool(secret) and header == f"Bearer {secret}"


def _sinria_command(repo_root: Path, prompt: str) -> list[str]:
    configured = os.environ.get("SINRIA_COMMAND")
    if configured:
        return [configured, "-z", prompt, "--ignore-rules", "--toolsets", ""]
    venv_entrypoint = repo_root / ".venv" / "bin" / "sinria"
    if venv_entrypoint.exists():
        return [str(venv_entrypoint), "-z", prompt, "--ignore-rules", "--toolsets", ""]
    return ["sinria", "-z", prompt, "--ignore-rules", "--toolsets", ""]


class SinriaRunner(Protocol):
    def run(self, prompt: str) -> tuple[bool, str, str]: ...


class CliSinriaRunner:
    """Compatibility runner that invokes the installed Sinria CLI per request."""

    def __init__(self, repo_root: Path, timeout: int) -> None:
        self.repo_root = repo_root
        self.timeout = timeout

    def run(self, prompt: str) -> tuple[bool, str, str]:
        cmd = _sinria_command(self.repo_root, prompt)
        env = os.environ.copy()
        env["SINRIA_MOBILE_CHAT_BRIDGE_CHILD"] = "1"
        env.setdefault("SINRIA_NO_CONTEXT_SHARE", "1")
        proc = subprocess.run(
            cmd,
            cwd=str(self.repo_root),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.timeout,
            check=False,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip()), proc.stdout.strip(), proc.stderr.strip()


class AgentSinriaRunner:
    """Fast runner that keeps one local Sinria CLI/agent instance warm inside the bridge."""

    def __init__(self, agent_factory: Callable[[], Any] | None = None) -> None:
        self._agent_factory = agent_factory or self._default_agent_factory
        self._agent: Any | None = None

    def _default_agent_factory(self) -> Any:
        # Reuse SinriaCLI's runtime/provider resolution instead of constructing
        # AIAgent directly; the CLI path is the installed, Sinria-native surface
        # that already knows how to resolve custom providers and credentials.
        from cli import HermesCLI  # legacy internal class name; installed surface remains Sinria.

        return HermesCLI(toolsets=[], verbose=False, compact=True, ignore_rules=True)

    def _get_agent(self) -> Any:
        if self._agent is None:
            self._agent = self._agent_factory()
        return self._agent

    def run(self, prompt: str) -> tuple[bool, str, str]:
        reply = str(self._get_agent().chat(prompt) or "").strip()
        return bool(reply), reply, ""


def make_sinria_runner(repo_root: Path, timeout: int) -> SinriaRunner:
    backend = os.environ.get("SINRIA_MOBILE_CHAT_BACKEND", "cli").strip().lower()
    if backend == "agent":
        return AgentSinriaRunner()
    if backend == "cli":
        return CliSinriaRunner(repo_root=repo_root, timeout=timeout)
    raise ValueError(f"unsupported SINRIA_MOBILE_CHAT_BACKEND: {backend}")


def make_handler(secret: [REDACTED], repo_root: Path, timeout: int, runner: SinriaRunner | None = None):
    sinria_runner = runner or make_sinria_runner(repo_root=repo_root, timeout=timeout)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # avoid raw request logging
            _ = (format, args)
            sys.stderr.write("sinria-mobile-chat-bridge: request handled\n")

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") == "/healthz":
                _json(self, 200, {"ok": True, "service": "sinria-mobile-chat-remote-bridge"})
                return
            _json(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/mobile-chat":
                _json(self, 404, {"ok": False, "error": "not found"})
                return
            if not _authorized(self, secret):
                _json(self, 401, {"ok": False, "error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("content-length", "0"))
            except ValueError:
                _json(self, 400, {"ok": False, "error": "invalid content length"})
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                _json(self, 413, {"ok": False, "error": "request too large or empty"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                _json(self, 400, {"ok": False, "error": "invalid json"})
                return
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                _json(self, 400, {"ok": False, "error": "missing prompt"})
                return
            try:
                ok, stdout, stderr = sinria_runner.run(prompt)
            except subprocess.TimeoutExpired:
                _json(self, 504, {"ok": False, "error": "local sinria timed out"})
                return
            except Exception:
                _json(self, 500, {"ok": False, "error": "local sinria execution failed"})
                return
            if not ok:
                # Do not return raw stderr; provider/tool errors may contain local paths.
                _json(self, 502, {"ok": False, "error": "local sinria returned no reply"})
                return
            _json(self, 200, {"ok": True, "reply": stdout, "source": "local_sinria"})

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local Sinria mobile chat bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("SINRIA_MOBILE_CHAT_REMOTE_BRIDGE_PORT", "8787")))
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("SINRIA_MOBILE_CHAT_REMOTE_BRIDGE_TIMEOUT", str(DEFAULT_TIMEOUT))))
    args = parser.parse_args()
    secret = os.environ.get("SINRIA_MOBILE_CHAT_REMOTE_SECRET", "").strip()
    if not secret:
        sys.stderr.write("SINRIA_MOBILE_CHAT_REMOTE_SECRET is required\n")
        return 2
    server = ThreadingHTTPServer((args.host, args.port), make_handler(secret, Path(args.repo_root), args.timeout))
    sys.stderr.write(f"sinria-mobile-chat-bridge listening on {args.host}:{args.port}\n")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
