"""Member-side service contract for purpose-scoped LINE peer relay."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


_HASH_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
_ALLOWED_KEYS = {
    "schemaVersion", "memberId", "instanceId", "conversationRef",
    "messageRef", "sourceType", "senderRef", "message",
    "rawContextStored", "externalActionAllowed",
}


class LinePeerRelayError(RuntimeError):
    """Safe relay rejection that contains no raw message or credential."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _NoProxyHandler(ProxyHandler):
    def __init__(self):
        super().__init__({})


class LinePeerRelayStore:
    """Local-only response cache preventing duplicate peer agent turns."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS relay_responses ("
            "message_ref TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
            "response TEXT NOT NULL, created_at TEXT NOT NULL "
            "DEFAULT CURRENT_TIMESTAMP)"
        )
        columns = {
            str(row[1]) for row in self.connection.execute("PRAGMA table_info(relay_responses)")
        }
        if "fingerprint" not in columns:
            self.connection.execute(
                "ALTER TABLE relay_responses ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''"
            )
        self.connection.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def __enter__(self) -> "LinePeerRelayStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def get(self, message_ref: str) -> str | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT response FROM relay_responses WHERE message_ref = ?", (message_ref,)
            ).fetchone()
        return str(row[0]) if row else None

    def put(self, message_ref: str, response: str) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT OR IGNORE INTO relay_responses(message_ref, response) VALUES (?, ?)",
                (message_ref, response),
            )
            self.connection.commit()

    def get_or_compute(
        self, message_ref: str, fingerprint: str, compute: Callable[[], str]
    ) -> str:
        """Serialize one idempotency decision so duplicate agent turns cannot race."""
        with self._lock:
            row = self.connection.execute(
                "SELECT response, fingerprint FROM relay_responses WHERE message_ref = ?",
                (message_ref,),
            ).fetchone()
            if row:
                if str(row[1]) != fingerprint:
                    raise LinePeerRelayError(
                        "LINE peer message identity was reused with different content"
                    )
                return str(row[0])
            response = compute()
            self.connection.execute(
                "INSERT INTO relay_responses(message_ref, fingerprint, response) VALUES (?, ?, ?)",
                (message_ref, fingerprint, response),
            )
            self.connection.commit()
            return response


def validate_line_peer_payload(
    value: Mapping[str, Any], *, member_id: str, instance_id: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _ALLOWED_KEYS:
        raise LinePeerRelayError("LINE peer payload shape is invalid")
    if value.get("schemaVersion") != "sinria.line-peer.v1":
        raise LinePeerRelayError("LINE peer protocol version is invalid")
    if value.get("memberId") != member_id or value.get("instanceId") != instance_id:
        raise LinePeerRelayError("LINE peer target identity mismatch")
    if value.get("rawContextStored") is not False or value.get("externalActionAllowed") is not False:
        raise LinePeerRelayError("LINE peer safety boundary mismatch")
    for field in ("conversationRef", "messageRef", "senderRef"):
        if not _HASH_REF.fullmatch(str(value.get(field) or "")):
            raise LinePeerRelayError("LINE peer reference must be a one-way hash")
    if value.get("sourceType") not in {"user", "group"}:
        raise LinePeerRelayError("LINE peer source type is unsupported")
    message = value.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > 20_000:
        raise LinePeerRelayError("LINE peer message is empty or too large")
    return dict(value)


def _local_api_request(
    *, url: str, token: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            **dict(headers),
        },
    )
    try:
        with build_opener(_NoProxyHandler(), _NoRedirectHandler()).open(
            request, timeout=timeout
        ) as response:
            value = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise LinePeerRelayError("local Sinria agent request failed") from exc
    if not isinstance(value, dict):
        raise LinePeerRelayError("local Sinria agent returned invalid JSON")
    return value


def _loopback_chat_url(base_url: str) -> str:
    parsed = urlparse(str(base_url or "").strip().rstrip("/"))
    if parsed.username is not None or parsed.password is not None:
        raise LinePeerRelayError("local Sinria agent API must not contain userinfo")
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise LinePeerRelayError("local Sinria agent API must use HTTP loopback")
    if not parsed.netloc or parsed.query or parsed.fragment:
        raise LinePeerRelayError("local Sinria agent API URL is invalid")
    return str(base_url).strip().rstrip("/") + "/v1/chat/completions"


def process_line_peer_relay(
    payload: Mapping[str, Any],
    *,
    member_id: str,
    instance_id: str,
    local_api_url: str,
    local_api_key: str,
    store: LinePeerRelayStore,
    request_fn: Callable[..., dict[str, Any]] = _local_api_request,
    timeout: float = 180.0,
) -> dict[str, Any]:
    value = validate_line_peer_payload(
        payload, member_id=member_id, instance_id=instance_id
    )
    if not local_api_key:
        raise LinePeerRelayError("local Sinria agent credential is not configured")
    chat_url = _loopback_chat_url(local_api_url)
    def run_agent_once() -> str:
        conversation_hash = value["conversationRef"].removeprefix("sha256:")
        headers = {
            # Compatibility protocol headers exposed by the current API server.
            "X-Hermes-Session-Key": f"line-peer:{member_id}:{conversation_hash}",
            "X-Hermes-Session-Id": f"line_peer_{conversation_hash[:48]}",
            "Idempotency-Key": value["messageRef"],
        }
        request_payload = {
            "model": "sinria-agent",
            "stream": False,
            "sinria_no_tools": True,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "This is a private LINE turn relayed through the shared Sinria front door. "
                        f"You are the Sinria owned by {member_id} on {instance_id}. "
                        "Use only this member's local context and authority. Never claim another "
                        "member acted or read the response. External sends, production changes, "
                        "credentials, permissions, billing, deletion, and clinical actions remain "
                        "human-approval gated."
                    ),
                },
                {"role": "user", "content": value["message"]},
            ],
        }
        result = request_fn(
            url=chat_url,
            token=local_api_key,
            headers=headers,
            payload=request_payload,
            timeout=timeout,
        )
        try:
            answer = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LinePeerRelayError("local Sinria agent response is incomplete") from exc
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 20_000:
            raise LinePeerRelayError("local Sinria agent response is invalid")
        return answer.strip()

    fingerprint = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    answer = store.get_or_compute(value["messageRef"], fingerprint, run_agent_once)
    return {
        "ok": True,
        "response": answer,
        "memberId": member_id,
        "instanceId": instance_id,
        "rawContextStored": False,
        "externalActionPerformed": False,
    }
