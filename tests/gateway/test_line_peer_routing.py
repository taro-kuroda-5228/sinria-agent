"""Contract tests for one LINE front door routing to member-owned Sinria."""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import logging
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.gateway._plugin_adapter_loader import load_plugin_adapter

from gateway.line_peer_routing import (
    _NoRedirectHandler,
    _NoProxyHandler,
    LinePeerProtocolError,
    LinePeerRoute,
    call_line_peer_backend,
    parse_line_peer_routes,
    select_line_peer_route,
)
from gateway.line_peer_relay_service import (
    _NoRedirectHandler as _RelayNoRedirectHandler,
    _NoProxyHandler as _RelayNoProxyHandler,
)


_line = load_plugin_adapter("line")
_PURPOSE_TOKEN = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")


@pytest.mark.parametrize("handler_class", [_NoRedirectHandler, _RelayNoRedirectHandler])
def test_relay_http_clients_disable_redirects(handler_class):
    handler = handler_class()
    assert handler.redirect_request(
        None, None, 302, "Found", {}, "https://elsewhere.invalid"
    ) is None


@pytest.mark.parametrize("handler_class", [_NoProxyHandler, _RelayNoProxyHandler])
def test_relay_http_clients_ignore_environment_proxies(handler_class):
    assert handler_class().proxies == {}


def _route(**overrides):
    values = {
        "member_id": "member_kikuchi",
        "instance_id": "inst_kikuchi_local",
        "endpoint": "https://kikuchi-mac.example.ts.net/v1/line-peer-relay",
        "token_env": "SINRIA_LINE_PEER_KIKUCHI_TOKEN",
        "display_name": "菊地さん",
        "dm_user_ids": ("Ukikuchi",),
        "group_ids": ("Cteam",),
        "group_prefixes": ("@菊地Sinria",),
    }
    values.update(overrides)
    return LinePeerRoute(**values)


def test_parse_routes_rejects_inline_secret_and_insecure_remote_endpoint():
    with pytest.raises(ValueError, match="unsupported route field"):
        parse_line_peer_routes(json.dumps({
            "kikuchi": {
                "member_id": "member_kikuchi",
                "instance_id": "inst_kikuchi_local",
                "endpoint": "https://peer.example.ts.net/v1/line-peer-relay",
                "token_env": "SINRIA_LINE_PEER_KIKUCHI_TOKEN",
                "token": "must-not-be-here",
            }
        }))

    with pytest.raises(ValueError, match="HTTPS"):
        parse_line_peer_routes(json.dumps({
            "kikuchi": {
                "member_id": "member_kikuchi",
                "instance_id": "inst_kikuchi_local",
                "endpoint": "http://192.168.1.20:8765/v1/line-peer-relay",
                "token_env": "SINRIA_LINE_PEER_KIKUCHI_TOKEN",
                "dm_user_ids": ["Ukikuchi"],
            }
        }))

    for endpoint in (
        "https://public.example/v1/line-peer-relay",
        "https://user:password@peer.example.ts.net/v1/line-peer-relay",
    ):
        with pytest.raises(ValueError, match="private Tailscale|userinfo"):
            parse_line_peer_routes(json.dumps({
                "kikuchi": {
                    "member_id": "member_kikuchi",
                    "instance_id": "inst_kikuchi_local",
                    "endpoint": endpoint,
                    "token_env": "SINRIA_LINE_PEER_KIKUCHI_TOKEN",
                    "dm_user_ids": ["Ukikuchi"],
                }
            }))


def test_parse_routes_rejects_ambiguous_dm_identity_across_routes():
    base = {
        "member_id": "member_kikuchi", "instance_id": "inst_kikuchi_local",
        "endpoint": "https://peer.example.ts.net/v1/line-peer-relay",
        "token_env": "SINRIA_LINE_PEER_KIKUCHI_TOKEN", "dm_user_ids": ["Ukikuchi"],
    }
    config = {
        "kikuchi-a": base,
        "kikuchi-b": {**base, "member_id": "member_other", "instance_id": "inst_other"},
    }
    with pytest.raises(ValueError, match="duplicate dm_user_id"):
        parse_line_peer_routes(json.dumps(config))


def test_parse_routes_rejects_ambiguous_group_prefix_across_routes():
    base = {
        "member_id": "member_kikuchi", "instance_id": "inst_kikuchi_local",
        "endpoint": "https://peer.example.ts.net/v1/line-peer-relay",
        "token_env": "SINRIA_LINE_PEER_KIKUCHI_TOKEN",
        "group_ids": ["Cteam"], "group_prefixes": ["@菊地Sinria"],
    }
    config = {
        "kikuchi-a": base,
        "kikuchi-b": {**base, "member_id": "member_other", "instance_id": "inst_other"},
    }
    with pytest.raises(ValueError, match="duplicate group prefix"):
        parse_line_peer_routes(json.dumps(config))


def test_dm_routes_by_verified_sender_identity():
    selected = select_line_peer_route(
        {"kikuchi": _route()},
        source_type="user",
        sender_user_id="Ukikuchi",
        chat_id="Ukikuchi",
        text="今日の予定を教えて",
    )
    assert selected is not None
    route, routed_text = selected
    assert route.member_id == "member_kikuchi"
    assert routed_text == "今日の予定を教えて"


def test_group_requires_exact_allowlisted_prefix_and_strips_only_that_prefix():
    routes = {"kikuchi": _route()}
    assert select_line_peer_route(
        routes,
        source_type="group",
        sender_user_id="Utaro",
        chat_id="Cteam",
        text="今日の予定を教えて",
    ) is None
    assert select_line_peer_route(
        routes,
        source_type="group",
        sender_user_id="Utaro",
        chat_id="Cother",
        text="@菊地Sinria 今日の予定を教えて",
    ) is None

    selected = select_line_peer_route(
        routes,
        source_type="group",
        sender_user_id="Utaro",
        chat_id="Cteam",
        text="@菊地Sinria 今日の予定を教えて",
    )
    assert selected is not None
    assert selected[1] == "今日の予定を教えて"


def test_exact_group_prefix_without_body_is_selected_for_fail_closed_notice():
    selected = select_line_peer_route(
        {"kikuchi": _route()}, source_type="group", sender_user_id="Utaro",
        chat_id="Cteam", text="@菊地Sinria",
    )
    assert selected is not None
    route, routed_text = selected
    assert route.member_id == "member_kikuchi"
    assert routed_text == ""


def test_ambiguous_group_prefix_fails_closed():
    routes = {
        "a": _route(member_id="member_a", instance_id="inst_a"),
        "b": _route(member_id="member_b", instance_id="inst_b"),
    }
    with pytest.raises(LinePeerProtocolError, match="ambiguous"):
        select_line_peer_route(
            routes,
            source_type="group",
            sender_user_id="Utaro",
            chat_id="Cteam",
            text="@菊地Sinria help",
        )


def test_backend_call_uses_purpose_token_and_validates_receipt(monkeypatch):
    captured = {}

    def fake_request(*, endpoint, token, payload, timeout):
        captured.update(endpoint=endpoint, token=token, payload=payload, timeout=timeout)
        return {
            "ok": True,
            "response": "菊地Sinriaからの回答",
            "memberId": "member_kikuchi",
            "instanceId": "inst_kikuchi_local",
            "rawContextStored": False,
            "externalActionPerformed": False,
        }

    monkeypatch.setenv("SINRIA_LINE_PEER_KIKUCHI_TOKEN", _PURPOSE_TOKEN)
    receipt = asyncio.run(call_line_peer_backend(
        _route(),
        text="確認してください",
        conversation_ref="sha256:conversation",
        message_ref="sha256:message",
        source_type="user",
        sender_ref="sha256:sender",
        request_fn=fake_request,
    ))

    assert receipt.response == "菊地Sinriaからの回答"
    assert captured["token"] == _PURPOSE_TOKEN
    assert captured["payload"]["memberId"] == "member_kikuchi"
    assert captured["payload"]["rawContextStored"] is False
    assert "sender_user_id" not in captured["payload"]


@pytest.mark.parametrize("weak_token", ["too-short", "abcdefgh" * 6])
def test_backend_rejects_weak_purpose_token(monkeypatch, weak_token):
    monkeypatch.setenv("SINRIA_LINE_PEER_KIKUCHI_TOKEN", weak_token)
    with pytest.raises(LinePeerProtocolError, match="token"):
        asyncio.run(call_line_peer_backend(
            _route(), text="確認", conversation_ref="sha256:conversation",
            message_ref="sha256:message", source_type="user", sender_ref="sha256:sender",
            request_fn=lambda **_: {},
        ))


@pytest.mark.parametrize("unsafe", [
    {"ok": True, "response": "x", "memberId": "member_taro", "instanceId": "inst_kikuchi_local", "rawContextStored": False, "externalActionPerformed": False},
    {"ok": True, "response": "x", "memberId": "member_kikuchi", "instanceId": "inst_kikuchi_local", "rawContextStored": True, "externalActionPerformed": False},
    {"ok": True, "response": "x", "memberId": "member_kikuchi", "instanceId": "inst_kikuchi_local", "rawContextStored": False, "externalActionPerformed": True},
])
def test_backend_receipt_identity_and_safety_flags_fail_closed(monkeypatch, unsafe):
    monkeypatch.setenv("SINRIA_LINE_PEER_KIKUCHI_TOKEN", _PURPOSE_TOKEN)
    with pytest.raises(LinePeerProtocolError):
        asyncio.run(call_line_peer_backend(
            _route(),
            text="確認",
            conversation_ref="sha256:conversation",
            message_ref="sha256:message",
            source_type="user",
            sender_ref="sha256:sender",
            request_fn=lambda **_: unsafe,
        ))


def test_backend_rejects_response_larger_than_line_delivery_capacity(monkeypatch):
    monkeypatch.setenv("SINRIA_LINE_PEER_KIKUCHI_TOKEN", _PURPOSE_TOKEN)
    oversized = {
        "ok": True, "response": "x" * 20_001,
        "memberId": "member_kikuchi", "instanceId": "inst_kikuchi_local",
        "rawContextStored": False, "externalActionPerformed": False,
    }
    with pytest.raises(LinePeerProtocolError, match="invalid response"):
        asyncio.run(call_line_peer_backend(
            _route(), text="確認", conversation_ref="sha256:conversation",
            message_ref="sha256:message", source_type="user", sender_ref="sha256:sender",
            request_fn=lambda **_: oversized,
        ))


def test_lifecycle_log_does_not_expose_raw_source_identity(caplog):
    caplog.set_level(logging.DEBUG)
    adapter = _peer_adapter(_route())
    adapter.allow_all = True
    raw_user_id = "U-private-line-identity"

    asyncio.run(adapter._dispatch_event({
        "type": "follow", "webhookEventId": "follow-event",
        "source": {"type": "user", "userId": raw_user_id},
    }))

    assert raw_user_id not in caplog.text
    assert "follow" in caplog.text


def test_duplicate_webhook_log_does_not_expose_raw_event_id(caplog):
    caplog.set_level(logging.DEBUG)
    adapter = _peer_adapter(_route())
    event_id = "raw-webhook-event-identifier"
    adapter._dedup.is_duplicate(event_id)

    asyncio.run(adapter._dispatch_event({
        "type": "message", "webhookEventId": event_id,
        "source": {"type": "user", "userId": "Ukikuchi"},
        "message": {"type": "text", "id": "message-duplicate", "text": "hello"},
    }))

    assert event_id not in caplog.text


def test_peer_delivery_network_error_log_does_not_expose_exception_text(caplog):
    caplog.set_level(logging.DEBUG)
    adapter = _peer_adapter(_route())
    adapter._client = type("Client", (), {
        "push": AsyncMock(side_effect=RuntimeError("raw-request-identifier")),
    })()

    result = asyncio.run(adapter._send_text_chunks("Ukikuchi", "response", force_push=True))

    assert result.success is False
    assert "raw-request-identifier" not in caplog.text


def test_line_adapter_loads_peer_routes_from_environment(monkeypatch):
    raw = json.dumps({"kikuchi": {
        "member_id": "member_kikuchi", "instance_id": "inst_kikuchi_local",
        "endpoint": "https://kikuchi-mac.example.ts.net/v1/line-peer-relay",
        "token_env": "SINRIA_LINE_PEER_KIKUCHI_TOKEN", "dm_user_ids": ["Ukikuchi"],
    }})
    monkeypatch.setenv("LINE_PEER_ROUTES_JSON", raw)

    adapter = _line.LineAdapter(type("Cfg", (), {"extra": {}})())

    assert adapter.peer_routes["kikuchi"].member_id == "member_kikuchi"


def _peer_adapter(route, *, group=False):
    config = {
        "member_id": route.member_id, "instance_id": route.instance_id,
        "endpoint": route.endpoint, "token_env": route.token_env,
        "display_name": route.display_name,
    }
    if group:
        config.update(group_ids=list(route.group_ids), group_prefixes=list(route.group_prefixes))
    else:
        config["dm_user_ids"] = list(route.dm_user_ids)
    return _line.LineAdapter(type("Cfg", (), {"extra": {"peer_routes": {"kikuchi": config}}})())


def test_line_dm_is_answered_by_selected_peer_not_local_agent(monkeypatch):
    route = _route()
    adapter = _peer_adapter(route)
    peer_call = AsyncMock(return_value=type("Receipt", (), {
        "response": "菊地Sinriaからの回答", "member_id": route.member_id,
        "instance_id": route.instance_id,
    })())
    local_call = AsyncMock()
    outbound = AsyncMock(return_value=type("Result", (), {"success": True})())
    monkeypatch.setattr(_line, "call_line_peer_backend", peer_call)
    monkeypatch.setattr(adapter, "handle_message", local_call)
    monkeypatch.setattr(adapter, "_send_text_chunks", outbound)

    asyncio.run(adapter._handle_message_event({
        "type": "message", "webhookEventId": "evt-1", "timestamp": 1_700_000_000_000,
        "replyToken": "reply-1", "source": {"type": "user", "userId": "Ukikuchi"},
        "message": {"type": "text", "id": "message-1", "text": "確認して"},
    }))

    local_call.assert_not_awaited()
    outbound.assert_awaited_once_with("Ukikuchi", "菊地Sinriaからの回答", force_push=False)
    kwargs = peer_call.await_args.kwargs
    assert kwargs["text"] == "確認して"
    assert kwargs["conversation_ref"].startswith("sha256:")
    assert kwargs["message_ref"].startswith("sha256:")
    assert kwargs["sender_ref"].startswith("sha256:")
    serialized = json.dumps(kwargs, default=str, ensure_ascii=False)
    assert "Ukikuchi" not in serialized
    assert "message-1" not in serialized


def test_mapped_dm_media_fails_closed_without_wrong_local_or_peer_text_turn(monkeypatch):
    adapter = _peer_adapter(_route())
    local_call = AsyncMock()
    peer_call = AsyncMock()
    outbound = AsyncMock(return_value=type("Result", (), {"success": True})())
    monkeypatch.setattr(adapter, "handle_message", local_call)
    monkeypatch.setattr(_line, "call_line_peer_backend", peer_call)
    monkeypatch.setattr(adapter, "_send_text_chunks", outbound)
    media_download = AsyncMock(return_value=("/private/raw-image.jpg", "image/jpeg"))
    monkeypatch.setattr(adapter, "_download_media", media_download)

    asyncio.run(adapter._handle_message_event({
        "type": "message", "webhookEventId": "evt-media", "replyToken": "reply-media",
        "source": {"type": "user", "userId": "Ukikuchi"},
        "message": {"type": "image", "id": "message-media"},
    }))

    local_call.assert_not_awaited()
    peer_call.assert_not_awaited()
    media_download.assert_not_awaited()
    assert "テキスト" in outbound.await_args.args[1]
    assert "/private/raw-image.jpg" not in outbound.await_args.args[1]


def test_mapped_dm_without_immutable_event_identity_fails_closed(monkeypatch):
    adapter = _peer_adapter(_route())
    local_call = AsyncMock()
    peer_call = AsyncMock()
    outbound = AsyncMock(return_value=type("Result", (), {"success": True})())
    monkeypatch.setattr(adapter, "handle_message", local_call)
    monkeypatch.setattr(_line, "call_line_peer_backend", peer_call)
    monkeypatch.setattr(adapter, "_send_text_chunks", outbound)
    asyncio.run(adapter._handle_message_event({
        "type": "message", "source": {"type": "user", "userId": "Ukikuchi"},
        "message": {"type": "text", "text": "確認"},
    }))
    peer_call.assert_not_awaited()
    local_call.assert_not_awaited()
    assert "識別" in outbound.await_args.args[1]


def test_peer_turns_are_serialized_per_conversation(monkeypatch):
    adapter = _peer_adapter(_route())
    active = 0
    max_active = 0

    async def peer_call(*_args, **_kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.03)
        active -= 1
        return type("Receipt", (), {"response": "ok"})()

    monkeypatch.setattr(_line, "call_line_peer_backend", peer_call)
    monkeypatch.setattr(adapter, "_send_text_chunks", AsyncMock(return_value=type("Result", (), {"success": True})()))

    async def run_both():
        await asyncio.gather(*[
            adapter._handle_message_event({
                "type": "message", "webhookEventId": f"evt-{index}", "replyToken": f"reply-{index}",
                "source": {"type": "user", "userId": "Ukikuchi"},
                "message": {"type": "text", "id": f"message-{index}", "text": f"turn {index}"},
            })
            for index in range(2)
        ])

    asyncio.run(run_both())
    assert max_active == 1


def test_line_peer_failure_does_not_fall_back_to_wrong_local_identity(monkeypatch):
    adapter = _peer_adapter(_route())
    local_call = AsyncMock()
    outbound = AsyncMock(return_value=type("Result", (), {"success": True})())
    monkeypatch.setattr(
        _line, "call_line_peer_backend",
        AsyncMock(side_effect=LinePeerProtocolError("offline")),
    )
    monkeypatch.setattr(adapter, "handle_message", local_call)
    monkeypatch.setattr(adapter, "_send_text_chunks", outbound)

    asyncio.run(adapter._handle_message_event({
        "type": "message", "webhookEventId": "evt-2", "replyToken": "reply-2",
        "source": {"type": "user", "userId": "Ukikuchi"},
        "message": {"type": "text", "id": "message-2", "text": "確認して"},
    }))

    local_call.assert_not_awaited()
    assert "菊地さんのSinria" in outbound.await_args.args[1]
    assert "接続" in outbound.await_args.args[1]


def test_peer_send_failure_returns_retryable_webhook_and_releases_dedup(monkeypatch):
    adapter = _peer_adapter(_route())
    monkeypatch.setattr(_line, "verify_line_signature", lambda *_: True)
    monkeypatch.setattr(
        _line, "call_line_peer_backend",
        AsyncMock(return_value=type("Receipt", (), {"response": "菊地Sinria回答"})()),
    )
    outbound = AsyncMock(side_effect=[
        type("Result", (), {"success": False, "error": "network"})(),
        type("Result", (), {"success": True, "error": None})(),
    ])
    monkeypatch.setattr(adapter, "_send_text_chunks", outbound)
    event = {
        "type": "message", "webhookEventId": "evt-retry", "replyToken": "reply-retry",
        "source": {"type": "user", "userId": "Ukikuchi"},
        "message": {"type": "text", "id": "message-retry", "text": "確認"},
    }
    body = json.dumps({"events": [event]}).encode()

    class Request:
        headers = {"X-Line-Signature": "test"}

        async def read(self):
            return body

    first = asyncio.run(adapter._handle_webhook(Request()))
    second = asyncio.run(adapter._handle_webhook(Request()))

    assert first.status == 503
    assert second.status == 200
    assert outbound.await_count == 2


def test_failed_delivery_blocks_newer_turn_until_original_retry_succeeds(monkeypatch):
    adapter = _peer_adapter(_route())
    adapter.allow_all = True
    monkeypatch.setattr(_line, "verify_line_signature", lambda *_: True)
    peer_call = AsyncMock(return_value=type("Receipt", (), {"response": "peer answer"})())
    monkeypatch.setattr(_line, "call_line_peer_backend", peer_call)
    outbound = AsyncMock(side_effect=[
        type("Result", (), {"success": False, "error": "network"})(),
        type("Result", (), {"success": True, "error": None})(),
        type("Result", (), {"success": True, "error": None})(),
    ])
    monkeypatch.setattr(adapter, "_send_text_chunks", outbound)

    def request(event_id, message_id, text):
        body = json.dumps({"events": [{
            "type": "message", "webhookEventId": event_id, "replyToken": "reply",
            "source": {"type": "user", "userId": "Ukikuchi"},
            "message": {"type": "text", "id": message_id, "text": text},
        }]}).encode()

        class Request:
            headers = {"X-Line-Signature": "test"}

            async def read(self):
                return body

        return Request()

    first = request("evt-order-1", "message-order-1", "first")
    second = request("evt-order-2", "message-order-2", "second")
    assert asyncio.run(adapter._handle_webhook(first)).status == 503
    assert asyncio.run(adapter._handle_webhook(second)).status == 503
    assert peer_call.await_count == 1
    assert asyncio.run(adapter._handle_webhook(first)).status == 200
    assert asyncio.run(adapter._handle_webhook(second)).status == 200
    assert peer_call.await_count == 3


def test_addressed_empty_group_message_never_falls_through_to_front_door_agent(monkeypatch):
    adapter = _peer_adapter(_route(), group=True)
    local_call = AsyncMock()
    peer_call = AsyncMock()
    outbound = AsyncMock(return_value=type("Result", (), {"success": True})())
    monkeypatch.setattr(adapter, "handle_message", local_call)
    monkeypatch.setattr(_line, "call_line_peer_backend", peer_call)
    monkeypatch.setattr(adapter, "_send_text_chunks", outbound)

    asyncio.run(adapter._handle_message_event({
        "type": "message", "webhookEventId": "evt-empty-group",
        "source": {"type": "group", "groupId": "Cteam", "userId": "Utaro"},
        "message": {"type": "text", "id": "message-empty-group", "text": "@菊地Sinria"},
    }))

    peer_call.assert_not_awaited()
    local_call.assert_not_awaited()
    assert "内容" in outbound.await_args.args[1]


def test_unaddressed_group_message_stays_on_existing_group_path(monkeypatch):
    adapter = _peer_adapter(_route(), group=True)
    local_call = AsyncMock()
    peer_call = AsyncMock()
    monkeypatch.setattr(adapter, "handle_message", local_call)
    monkeypatch.setattr(_line, "call_line_peer_backend", peer_call)

    asyncio.run(adapter._handle_message_event({
        "type": "message", "webhookEventId": "evt-3",
        "source": {"type": "group", "groupId": "Cteam", "userId": "Utaro"},
        "message": {"type": "text", "id": "message-3", "text": "通常の共有会話"},
    }))

    peer_call.assert_not_awaited()
    local_call.assert_awaited_once()


def test_offline_e2e_line_front_door_to_member_relay_and_back(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / "scripts" / "sinria-line-peer-relay.py"
    spec = importlib.util.spec_from_file_location("line_peer_relay_e2e", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = module.create_server(
        host="127.0.0.1", port=0, relay_token=_PURPOSE_TOKEN,
        member_id="member_kikuchi", instance_id="inst_kikuchi_local",
        local_api_url="http://127.0.0.1:8642", local_api_key="local-key",
        state_path=tmp_path / "relay.sqlite3",
        request_fn=lambda **_: {"choices": [{"message": {"content": "菊地実機経路の回答"}}]},
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SINRIA_LINE_PEER_KIKUCHI_TOKEN", _PURPOSE_TOKEN)
    adapter = _line.LineAdapter(type("Cfg", (), {"extra": {"peer_routes": {"kikuchi": {
        "display_name": "菊地さん", "member_id": "member_kikuchi",
        "instance_id": "inst_kikuchi_local",
        "endpoint": f"http://127.0.0.1:{server.server_address[1]}/v1/line-peer-relay",
        "token_env": "SINRIA_LINE_PEER_KIKUCHI_TOKEN", "dm_user_ids": ["Ukikuchi"],
    }}}})())
    local_call = AsyncMock()
    outbound = AsyncMock(return_value=type("Result", (), {"success": True})())
    monkeypatch.setattr(adapter, "handle_message", local_call)
    monkeypatch.setattr(adapter, "_send_text_chunks", outbound)
    try:
        asyncio.run(adapter._handle_message_event({
            "type": "message", "webhookEventId": "evt-e2e", "replyToken": "reply-e2e",
            "source": {"type": "user", "userId": "Ukikuchi"},
            "message": {"type": "text", "id": "message-e2e", "text": "実経路確認"},
        }))
        local_call.assert_not_awaited()
        outbound.assert_awaited_once_with("Ukikuchi", "菊地実機経路の回答", force_push=False)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
