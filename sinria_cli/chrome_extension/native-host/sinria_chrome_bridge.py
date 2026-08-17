#!/usr/bin/env python3
"""Local-only native messaging bridge for Sinria in Chrome."""

from __future__ import annotations

import json
import os
import struct
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

HOSTS = {"127.0.0.1", "localhost"}
METHODS = {"GET", "POST"}
REQUEST_HEADERS = {"authorization", "content-type", "x-sinria-session-id"}
MAX_MESSAGE_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 900_000


def load_local_api_token(sinria_home=None):
    """Read only the local API key; never return the rest of the env file."""
    home = Path(sinria_home or os.environ.get("SINRIA_HOME", Path.home() / ".sinria")).expanduser()
    try:
        lines = (home / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "API_SERVER_KEY":
            return value.strip().strip('"').strip("'")
    return ""


def authorized_headers(headers, sinria_home=None):
    result = dict(headers)
    if not any(str(key).lower() == "authorization" for key in result):
        token = load_local_api_token(sinria_home)
        if token:
            result["Authorization"] = f"Bearer {token}"
    return result


def _read_message():
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return None
    if len(raw_length) != 4:
        raise ValueError("Invalid native message length.")
    length = struct.unpack("<I", raw_length)[0]
    if length <= 0 or length > MAX_MESSAGE_BYTES:
        raise ValueError("Native message exceeds the allowed size.")
    payload = sys.stdin.buffer.read(length)
    if len(payload) != length:
        raise ValueError("Incomplete native message.")
    return json.loads(payload.decode("utf-8"))


def _write_message(message):
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        payload = json.dumps({"ok": False, "error": "Native response exceeds the allowed size."}).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(payload)))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _validated_request(message):
    if message.get("op") != "http":
        raise ValueError("Unsupported native operation.")
    url = str(message.get("url") or "")
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in HOSTS:
        raise ValueError("Only the local Sinria HTTP API is allowed.")
    method = str(message.get("method") or "GET").upper()
    if method not in METHODS:
        raise ValueError("Unsupported HTTP method.")
    headers = authorized_headers({
        str(key): str(value)
        for key, value in dict(message.get("headers") or {}).items()
        if str(key).lower() in REQUEST_HEADERS and value is not None
    })
    body = message.get("body")
    data = None if body is None else str(body).encode("utf-8")
    return urllib.request.Request(url, data=data, headers=headers, method=method)


def _perform(message):
    request = _validated_request(message)
    try:
        response = urllib.request.urlopen(request, timeout=120)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("Sinria API response exceeds the allowed size.")
        return {
            "ok": True,
            "status": int(getattr(response, "status", 0) or 0),
            "statusText": str(getattr(response, "reason", "") or ""),
            "headers": {
                "content-type": response.headers.get("Content-Type", ""),
            },
            "body": body.decode("utf-8", "replace"),
        }


def main():
    while True:
        try:
            message = _read_message()
            if message is None:
                return
            _write_message(_perform(message))
        except Exception as error:
            _write_message({"ok": False, "error": str(error)})


if __name__ == "__main__":
    main()
