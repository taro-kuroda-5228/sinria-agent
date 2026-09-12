"""Tests for the purpose-scoped LINE peer relay running on a member Mac."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gateway.line_peer_relay_service import (
    LinePeerRelayError,
    LinePeerRelayStore,
    probe_line_peer_local_api,
    process_line_peer_relay,
    validate_line_peer_payload,
)


def _payload(**overrides):
    value = {
        "schemaVersion": "sinria.line-peer.v1",
        "memberId": "member_kikuchi",
        "instanceId": "inst_kikuchi_local",
        "conversationRef": "sha256:" + "a" * 64,
        "messageRef": "sha256:" + "b" * 64,
        "sourceType": "user",
        "senderRef": "sha256:" + "c" * 64,
        "message": "今日の予定を確認して",
        "rawContextStored": False,
        "externalActionAllowed": False,
    }
    value.update(overrides)
    return value


def test_relay_rejects_wrong_identity_or_cloud_storage_flags():
    with pytest.raises(LinePeerRelayError, match="identity"):
        validate_line_peer_payload(
            _payload(memberId="member_taro"),
            member_id="member_kikuchi",
            instance_id="inst_kikuchi_local",
        )
    with pytest.raises(LinePeerRelayError, match="safety"):
        validate_line_peer_payload(
            _payload(rawContextStored=True),
            member_id="member_kikuchi",
            instance_id="inst_kikuchi_local",
        )


def test_relay_rejects_raw_line_identifiers_in_metadata_refs():
    with pytest.raises(LinePeerRelayError, match="reference"):
        validate_line_peer_payload(
            _payload(senderRef="Ukikuchi"),
            member_id="member_kikuchi",
            instance_id="inst_kikuchi_local",
        )


def test_relay_calls_only_local_api_and_returns_verified_receipt(tmp_path):
    captured = {}

    def local_api_call(**kwargs):
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "菊地本人のSinria回答"}}]}

    with LinePeerRelayStore(tmp_path / "relay.sqlite3") as store:
        receipt = process_line_peer_relay(
            _payload(),
            member_id="member_kikuchi",
            instance_id="inst_kikuchi_local",
            local_api_url="http://127.0.0.1:8642",
            local_api_key="local-only-key",
            store=store,
            request_fn=local_api_call,
        )

    assert receipt == {
        "ok": True,
        "response": "菊地本人のSinria回答",
        "memberId": "member_kikuchi",
        "instanceId": "inst_kikuchi_local",
        "rawContextStored": False,
        "externalActionPerformed": False,
    }
    assert captured["url"] == "http://127.0.0.1:8642/v1/chat/completions"
    assert captured["token"] == "local-only-key"
    assert captured["headers"]["X-Hermes-Session-Key"].startswith("line-peer:")
    assert captured["headers"]["X-Hermes-Session-Id"].startswith("line_peer_")
    assert captured["headers"]["Idempotency-Key"] == _payload()["messageRef"]
    assert captured["payload"]["sinria_no_tools"] is True
    serialized = json.dumps(receipt, ensure_ascii=False)
    assert "local-only-key" not in serialized
    assert "今日の予定" not in serialized


def test_preflight_probes_authenticated_loopback_health():
    captured = {}

    def request_fn(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "platform": "api_server"}

    receipt = probe_line_peer_local_api(
        "http://127.0.0.1:8642",
        "local-only-key",
        request_fn=request_fn,
    )

    assert receipt == {"ok": True, "service": "sinria-local-api"}
    assert captured == {
        "url": "http://127.0.0.1:8642/health/detailed",
        "token": "local-only-key",
        "timeout": 10.0,
    }


def test_preflight_rejects_unhealthy_or_unreachable_local_api():
    with pytest.raises(LinePeerRelayError, match="health check failed"):
        probe_line_peer_local_api(
            "http://127.0.0.1:8642",
            "local-only-key",
            request_fn=lambda **_: {"status": "degraded"},
        )


def test_relay_refuses_non_loopback_agent_api(tmp_path):
    for local_api_url in (
        "https://external.example",
        "http://user:password@127.0.0.1:8642",
        "http://127.0.0.1:8642/v1",
    ):
        with LinePeerRelayStore(tmp_path / ("relay-" + str(len(local_api_url)) + ".sqlite3")) as store:
            with pytest.raises(LinePeerRelayError, match="loopback|userinfo|base path"):
                process_line_peer_relay(
                    _payload(),
                    member_id="member_kikuchi",
                    instance_id="inst_kikuchi_local",
                    local_api_url=local_api_url,
                    local_api_key="key",
                    store=store,
                    request_fn=lambda **_: {"choices": []},
                )


def test_concurrent_duplicate_is_executed_once(tmp_path):
    calls = 0
    calls_lock = threading.Lock()

    def local_api_call(**_):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return {"choices": [{"message": {"content": "cached answer"}}]}

    with LinePeerRelayStore(tmp_path / "relay.sqlite3") as store:
        def run_once():
            return process_line_peer_relay(
                _payload(), member_id="member_kikuchi", instance_id="inst_kikuchi_local",
                local_api_url="http://127.0.0.1:8642", local_api_key="local-only-key",
                store=store, request_fn=local_api_call,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: run_once(), range(2)))
    assert calls == 1
    assert [result["response"] for result in results] == ["cached answer", "cached answer"]


def test_duplicate_message_ref_returns_local_cached_answer_without_second_agent_turn(tmp_path):
    calls = []

    def local_api_call(**kwargs):
        calls.append(kwargs)
        return {"choices": [{"message": {"content": "一度だけ生成"}}]}

    db = tmp_path / "relay.sqlite3"
    with LinePeerRelayStore(db) as store:
        first = process_line_peer_relay(
            _payload(), member_id="member_kikuchi", instance_id="inst_kikuchi_local",
            local_api_url="http://127.0.0.1:8642", local_api_key="key",
            store=store, request_fn=local_api_call,
        )
        second = process_line_peer_relay(
            _payload(), member_id="member_kikuchi", instance_id="inst_kikuchi_local",
            local_api_url="http://127.0.0.1:8642", local_api_key="key",
            store=store, request_fn=local_api_call,
        )

    assert first == second
    assert len(calls) == 1
    assert (Path(db).stat().st_mode & 0o777) == 0o600


def test_reused_message_ref_with_different_payload_is_rejected(tmp_path):
    calls = 0

    def local_api_call(**_):
        nonlocal calls
        calls += 1
        return {"choices": [{"message": {"content": "first answer"}}]}

    first_payload = _payload()
    changed_payload = {**first_payload, "message": "different message"}
    with LinePeerRelayStore(tmp_path / "relay.sqlite3") as store:
        process_line_peer_relay(
            first_payload, member_id="member_kikuchi", instance_id="inst_kikuchi_local",
            local_api_url="http://127.0.0.1:8642", local_api_key="key",
            store=store, request_fn=local_api_call,
        )
        with pytest.raises(LinePeerRelayError, match="reused"):
            process_line_peer_relay(
                changed_payload, member_id="member_kikuchi", instance_id="inst_kikuchi_local",
                local_api_url="http://127.0.0.1:8642", local_api_key="key",
                store=store, request_fn=local_api_call,
            )
    assert calls == 1
