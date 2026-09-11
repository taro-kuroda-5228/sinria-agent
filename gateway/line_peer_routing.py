"""Purpose-scoped routing from one LINE front door to member-owned Sinria.

Raw message text is sent only to the selected peer endpoint. Company OS receives
no message body. Route configuration contains token *environment names*, never
credential values.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ENV_RE = re.compile(r"^SINRIA_LINE_PEER_[A-Z0-9_]+_TOKEN$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_ALLOWED_FIELDS = {
    "display_name",
    "member_id",
    "instance_id",
    "endpoint",
    "token_env",
    "dm_user_ids",
    "group_ids",
    "group_prefixes",
    "timeout_seconds",
}


class LinePeerProtocolError(RuntimeError):
    """Safe failure for a rejected or unverifiable peer relay operation."""


def validate_line_peer_token(value: str) -> None:
    """Require a token shaped like 32+ random bytes encoded as base64url."""
    if not _TOKEN_RE.fullmatch(value):
        raise ValueError("LINE peer purpose token must encode at least 32 random bytes")
    try:
        decoded = base64.b64decode(
            value + ("=" * (-len(value) % 4)), altchars=b"-_", validate=True
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError("LINE peer purpose token is not canonical base64url") from exc
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if canonical != value or len(decoded) < 32:
        raise ValueError("LINE peer purpose token must canonically encode at least 32 bytes")
    counts = {char: value.count(char) for char in set(value)}
    entropy = -sum(
        (count / len(value)) * math.log2(count / len(value))
        for count in counts.values()
    )
    if entropy < 4.5:
        raise ValueError("LINE peer purpose token has insufficient entropy")
    for width in range(1, len(value) // 2 + 1):
        if len(value) % width == 0 and value == value[:width] * (len(value) // width):
            raise ValueError("LINE peer purpose token has a repeated pattern")


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _NoProxyHandler(ProxyHandler):
    def __init__(self):
        super().__init__({})


class LinePeerDeliveryGate:
    """Durable hash-only state machine for ordered, retry-safe delivery."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS peer_delivery ("
            "conversation_ref TEXT PRIMARY KEY, message_ref TEXT NOT NULL, "
            "fingerprint TEXT NOT NULL, state TEXT NOT NULL, updated_at REAL NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS delivered_messages ("
            "message_ref TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, delivered_at REAL NOT NULL)"
        )
        self.connection.commit()
        try:
            os.chmod(self.path.parent, 0o700)
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def claim(self, conversation_ref: str, message_ref: str, fingerprint: str) -> str:
        """Return processing/delivered/sending, or blocked for a newer turn."""
        with self._lock:
            now = time.time()
            self.connection.execute(
                "DELETE FROM peer_delivery WHERE state = 'delivered' AND updated_at < ?",
                (now - 604800,),
            )
            self.connection.execute(
                "DELETE FROM delivered_messages WHERE delivered_at < ?", (now - 604800,)
            )
            delivered = self.connection.execute(
                "SELECT fingerprint FROM delivered_messages WHERE message_ref = ?",
                (message_ref,),
            ).fetchone()
            if delivered is not None:
                if str(delivered[0]) != fingerprint:
                    self.connection.rollback()
                    raise LinePeerProtocolError(
                        "LINE peer message identity was reused with different content"
                    )
                self.connection.commit()
                return "delivered"
            row = self.connection.execute(
                "SELECT message_ref, fingerprint, state FROM peer_delivery "
                "WHERE conversation_ref = ?",
                (conversation_ref,),
            ).fetchone()
            if row is None:
                self.connection.execute(
                    "INSERT INTO peer_delivery VALUES (?, ?, ?, 'processing', ?)",
                    (conversation_ref, message_ref, fingerprint, now),
                )
                self.connection.commit()
                return "processing"
            if str(row[0]) != message_ref:
                if str(row[2]) == "delivered":
                    self.connection.execute(
                        "UPDATE peer_delivery SET message_ref = ?, fingerprint = ?, "
                        "state = 'processing', updated_at = ? WHERE conversation_ref = ?",
                        (message_ref, fingerprint, now, conversation_ref),
                    )
                    self.connection.commit()
                    return "processing"
                self.connection.commit()
                return "blocked"
            if str(row[1]) != fingerprint:
                self.connection.rollback()
                raise LinePeerProtocolError("LINE peer message identity was reused with different content")
            self.connection.commit()
            return str(row[2])

    def transition(self, conversation_ref: str, message_ref: str, state: str) -> None:
        if state not in {"processing", "sending", "delivered"}:
            raise ValueError("invalid LINE peer delivery state")
        with self._lock:
            cursor = self.connection.execute(
                "UPDATE peer_delivery SET state = ?, updated_at = ? "
                "WHERE conversation_ref = ? AND message_ref = ?",
                (state, time.time(), conversation_ref, message_ref),
            )
            if cursor.rowcount != 1:
                self.connection.rollback()
                raise LinePeerProtocolError("LINE peer delivery claim is unavailable")
            if state == "delivered":
                row = self.connection.execute(
                    "SELECT fingerprint FROM peer_delivery WHERE conversation_ref = ?",
                    (conversation_ref,),
                ).fetchone()
                self.connection.execute(
                    "INSERT OR REPLACE INTO delivered_messages VALUES (?, ?, ?)",
                    (message_ref, str(row[0]), time.time()),
                )
            self.connection.commit()

    def reconcile(
        self,
        conversation_ref: str,
        message_ref: str,
        *,
        decision: str,
        human_confirmed: bool,
    ) -> None:
        if not human_confirmed:
            raise ValueError("LINE peer sending reconciliation requires human review")
        if decision not in {"retry", "delivered"}:
            raise ValueError("LINE peer reconciliation decision is invalid")
        with self._lock:
            row = self.connection.execute(
                "SELECT state FROM peer_delivery WHERE conversation_ref = ? AND message_ref = ?",
                (conversation_ref, message_ref),
            ).fetchone()
            if row is None or str(row[0]) != "sending":
                raise LinePeerProtocolError("LINE peer sending claim is unavailable")
            self.transition(
                conversation_ref,
                message_ref,
                "processing" if decision == "retry" else "delivered",
            )

    def close(self) -> None:
        with self._lock:
            self.connection.close()


@dataclass(frozen=True)
class LinePeerRoute:
    member_id: str
    instance_id: str
    endpoint: str
    token_env: str
    display_name: str = ""
    dm_user_ids: tuple[str, ...] = ()
    group_ids: tuple[str, ...] = ()
    group_prefixes: tuple[str, ...] = ()
    timeout_seconds: float = 180.0


@dataclass(frozen=True)
class LinePeerReceipt:
    response: str
    member_id: str
    instance_id: str
    raw_context_stored: bool
    external_action_performed: bool


def _as_string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _validate_endpoint(value: Any) -> str:
    endpoint = str(value or "").strip().rstrip("/")
    parsed = urlparse(endpoint)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("LINE peer endpoint must not contain userinfo")
    if parsed.scheme not in ({"http", "https"} if local else {"https"}):
        raise ValueError("LINE peer endpoint must use HTTPS outside loopback")
    if not local and not str(parsed.hostname or "").lower().endswith(".ts.net"):
        raise ValueError("LINE peer endpoint must use a private Tailscale HTTPS hostname")
    if not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("LINE peer endpoint must be an absolute URL without query or fragment")
    if parsed.path != "/v1/line-peer-relay":
        raise ValueError("LINE peer endpoint path must be /v1/line-peer-relay")
    return endpoint


def parse_line_peer_routes(raw: str | Mapping[str, Any] | None) -> dict[str, LinePeerRoute]:
    if raw in (None, ""):
        return {}
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("LINE peer routes must be valid JSON") from exc
    else:
        value = raw
    if not isinstance(value, Mapping):
        raise ValueError("LINE peer routes must be an object")

    routes: dict[str, LinePeerRoute] = {}
    claimed_dm_users: set[str] = set()
    claimed_group_prefixes: set[tuple[str, str]] = set()
    for name, config in value.items():
        if not isinstance(name, str) or not _ID_RE.fullmatch(name):
            raise ValueError("LINE peer route name is invalid")
        if not isinstance(config, Mapping):
            raise ValueError("LINE peer route must be an object")
        extra = set(config) - _ALLOWED_FIELDS
        if extra:
            raise ValueError(f"unsupported route field: {sorted(extra)[0]}")
        member_id = str(config.get("member_id") or "").strip()
        instance_id = str(config.get("instance_id") or "").strip()
        token_env = str(config.get("token_env") or "").strip()
        if not _ID_RE.fullmatch(member_id) or not _ID_RE.fullmatch(instance_id):
            raise ValueError("LINE peer member or instance identity is invalid")
        if not _ENV_RE.fullmatch(token_env):
            raise ValueError("LINE peer token_env must name a purpose-scoped Sinria token")
        dm_user_ids = _as_string_tuple(config.get("dm_user_ids"), "dm_user_ids")
        group_ids = _as_string_tuple(config.get("group_ids"), "group_ids")
        group_prefixes = _as_string_tuple(config.get("group_prefixes"), "group_prefixes")
        if not dm_user_ids and not (group_ids and group_prefixes):
            raise ValueError("LINE peer route must declare a DM identity or group prefix")
        duplicate_dm = claimed_dm_users.intersection(dm_user_ids)
        if duplicate_dm or len(set(dm_user_ids)) != len(dm_user_ids):
            raise ValueError("duplicate dm_user_id in LINE peer routes")
        group_claims = {(group_id, prefix) for group_id in group_ids for prefix in group_prefixes}
        if claimed_group_prefixes.intersection(group_claims):
            raise ValueError("duplicate group prefix in LINE peer routes")
        if len(set(group_ids)) != len(group_ids) or len(set(group_prefixes)) != len(group_prefixes):
            raise ValueError("duplicate group prefix in LINE peer route")
        try:
            timeout = float(config.get("timeout_seconds", 180.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("LINE peer timeout_seconds is invalid") from exc
        if not 1.0 <= timeout <= 300.0:
            raise ValueError("LINE peer timeout_seconds must be between 1 and 300")
        routes[name] = LinePeerRoute(
            member_id=member_id,
            instance_id=instance_id,
            endpoint=_validate_endpoint(config.get("endpoint")),
            token_env=token_env,
            display_name=str(config.get("display_name") or "").strip()[:80],
            dm_user_ids=dm_user_ids,
            group_ids=group_ids,
            group_prefixes=group_prefixes,
            timeout_seconds=timeout,
        )
        claimed_dm_users.update(dm_user_ids)
        claimed_group_prefixes.update(group_claims)
    return routes


def select_line_peer_route(
    routes: Mapping[str, LinePeerRoute],
    *,
    source_type: str,
    sender_user_id: str,
    chat_id: str,
    text: str,
) -> tuple[LinePeerRoute, str] | None:
    matches: list[tuple[LinePeerRoute, str]] = []
    if source_type == "user":
        for route in routes.values():
            if sender_user_id in route.dm_user_ids:
                matches.append((route, text))
    elif source_type == "group":
        for route in routes.values():
            if chat_id not in route.group_ids:
                continue
            for prefix in route.group_prefixes:
                if text == prefix or text.startswith(prefix + " ") or text.startswith(prefix + "\u3000"):
                    routed = text[len(prefix):].lstrip(" \u3000")
                    matches.append((route, routed))
                    break
    if len(matches) > 1:
        raise LinePeerProtocolError("LINE peer route is ambiguous")
    return matches[0] if matches else None


def _request_json(*, endpoint: str, token: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with build_opener(_NoProxyHandler(), _NoRedirectHandler()).open(
            request, timeout=timeout
        ) as response:
            value = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise LinePeerProtocolError("LINE peer backend connection failed") from exc
    if not isinstance(value, dict):
        raise LinePeerProtocolError("LINE peer backend returned an invalid receipt")
    return value


async def call_line_peer_backend(
    route: LinePeerRoute,
    *,
    text: str,
    conversation_ref: str,
    message_ref: str,
    source_type: str,
    sender_ref: str,
    request_fn: Callable[..., dict[str, Any]] = _request_json,
    token_lookup: Callable[[str], str | None] = os.getenv,
) -> LinePeerReceipt:
    token = str(token_lookup(route.token_env) or "").strip()
    if not token:
        raise LinePeerProtocolError(f"{route.token_env} is not configured")
    try:
        validate_line_peer_token(token)
    except ValueError as exc:
        raise LinePeerProtocolError(str(exc)) from exc
    if not text.strip() or len(text) > 20_000:
        raise LinePeerProtocolError("LINE peer message is empty or too large")
    payload = {
        "schemaVersion": "sinria.line-peer.v1",
        "memberId": route.member_id,
        "instanceId": route.instance_id,
        "conversationRef": conversation_ref,
        "messageRef": message_ref,
        "sourceType": source_type,
        "senderRef": sender_ref,
        "message": text,
        "rawContextStored": False,
        "externalActionAllowed": False,
    }
    value = await asyncio.to_thread(
        request_fn,
        endpoint=route.endpoint,
        token=token,
        payload=payload,
        timeout=route.timeout_seconds,
    )
    required_identity = (
        value.get("memberId") == route.member_id
        and value.get("instanceId") == route.instance_id
    )
    response = value.get("response")
    safe = value.get("rawContextStored") is False and value.get("externalActionPerformed") is False
    if value.get("ok") is not True or not required_identity or not safe:
        raise LinePeerProtocolError("LINE peer backend receipt failed identity or safety validation")
    if not isinstance(response, str) or not response.strip() or len(response) > 20_000:
        raise LinePeerProtocolError("LINE peer backend returned an invalid response")
    return LinePeerReceipt(
        response=response.strip(),
        member_id=route.member_id,
        instance_id=route.instance_id,
        raw_context_stored=False,
        external_action_performed=False,
    )
