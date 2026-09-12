"""Contract tests for opt-in LINE -> Company OS task intake."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.gateway._plugin_adapter_loader import load_plugin_adapter


_line = load_plugin_adapter("line")


def test_task_intake_disabled_by_default(monkeypatch):
    monkeypatch.delenv("LINE_TASK_INTAKE_GROUPS", raising=False)
    adapter = _line.LineAdapter(type("Cfg", (), {"extra": {}})())
    assert adapter.task_intake_groups == set()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("@Sinria 提案資料を更新して", "提案資料を更新して"),
        ("@Sinria\u3000提案資料を更新して", "提案資料を更新して"),
        ("@Sinria", ""),
        ("@SinriaBot 提案資料を更新して", None),
        ("前置き @Sinria 提案資料を更新して", None),
        ("<@&1506049462001467605> 提案資料を更新して", None),
    ],
)
def test_explicit_task_prefix_must_match_at_start_and_on_a_boundary(text, expected):
    assert _line.extract_line_task_invocation(
        {"type": "text", "text": text},
        prefixes=("@Sinria",),
        bot_user_id="Ubot",
    ) == expected


def test_native_line_self_mention_at_start_invokes_task():
    message = {
        "type": "text", "text": "任意表示 提案資料を更新して",
        "mention": {"mentionees": [
            {"index": 0, "length": 4, "type": "user", "isSelf": True},
        ]},
    }
    assert _line.extract_line_task_invocation(
        message, prefixes=("@Sinria",), bot_user_id="Ubot"
    ) == "提案資料を更新して"


def test_unverified_or_quoted_marker_does_not_invoke():
    unverified = {
        "type": "text", "text": "@Other 提案資料を更新して",
        "mention": {"mentionees": [
            {"index": 0, "length": 6, "type": "user", "isSelf": False},
        ]},
    }
    quoted = {
        "type": "text", "text": "了解です",
        "quotedMessageId": "message-containing-old-marker",
    }
    assert _line.extract_line_task_invocation(
        unverified, prefixes=("@Sinria",), bot_user_id="Ubot"
    ) is None
    assert _line.extract_line_task_invocation(
        quoted, prefixes=("@Sinria",), bot_user_id="Ubot"
    ) is None


def test_parse_no_task_decision_is_silent():
    decision = _line.parse_task_intake_decision(
        '{"kind":"none","reason":"雑談"}'
    )
    assert decision is None


def test_parse_clear_task_requires_sanitized_summary_only():
    decision = _line.parse_task_intake_decision(
        json.dumps(
            {
                "kind": "task",
                "summary": "提案資料を金曜日までに更新する",
                "assignee": "other_participant",
                "priority": "normal",
            },
            ensure_ascii=False,
        )
    )
    assert decision.summary == "提案資料を金曜日までに更新する"
    assert decision.assignee == "other_participant"
    assert decision.priority == "normal"


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        '{"kind":"task","summary":""}',
        '{"kind":"task","summary":"患者ID: 12345を確認","assignee":"other_participant"}',
        '{"kind":"task","summary":"a@example.comへ送信","assignee":"other_participant"}',
    ],
)
def test_parse_task_decision_fails_closed_for_invalid_or_sensitive_output(payload):
    with pytest.raises(ValueError):
        _line.parse_task_intake_decision(payload)


def test_direct_japanese_request_routes_to_other_participant():
    decision = _line.TaskIntakeDecision(
        summary="提案書を更新", assignee="sender", priority="normal"
    )
    routed = _line.enforce_task_assignee(decision, "明日までに提案書を更新してください")
    assert routed.assignee == "other_participant"


def test_explicit_japanese_commitment_routes_to_sender():
    decision = _line.TaskIntakeDecision(
        summary="提案書を更新", assignee="other_participant", priority="normal"
    )
    routed = _line.enforce_task_assignee(decision, "提案書は私が明日更新します")
    assert routed.assignee == "sender"


def test_local_evidence_is_private_and_payload_is_metadata_only(tmp_path):
    evidence = _line.store_line_task_evidence(
        root=tmp_path,
        group_id="Cgroup",
        sender_user_id="Usender",
        message_id="m-123",
        webhook_event_id="evt-123",
        text="菊地さん、患者Aの資料を確認してください",
        timestamp_ms=1_700_000_000_000,
    )
    path = Path(evidence.path)
    assert path.exists()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    assert evidence.ref.startswith("local://line/task-intake/")

    payload = _line.build_company_os_task_payload(
        summary="資料を確認する",
        evidence_ref=evidence.ref,
        idempotency_key=evidence.idempotency_key,
        workspace_id="medical-horizon",
        requester_member_id="member-taro",
        requester_instance_id="instance-taro",
        target_member_id="member-kikuchi",
        target_instance_id="instance-kikuchi",
        priority="normal",
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "患者A" not in serialized
    assert payload["instruction"] == "資料を確認する"
    assert payload["payload"] == {"sourceRef": evidence.ref, "sourcePlatform": "line"}
    assert payload["humanApprovalRequired"] is False
    assert payload["externalActionAllowed"] is False
    assert payload["externalEgressAllowed"] is False


def test_same_line_message_has_stable_idempotency_key(tmp_path):
    kwargs = dict(
        root=tmp_path,
        group_id="Cgroup",
        sender_user_id="Usender",
        message_id="m-123",
        webhook_event_id="evt-123",
        text="資料を更新してください",
        timestamp_ms=1_700_000_000_000,
    )
    first = _line.store_line_task_evidence(**kwargs)
    second = _line.store_line_task_evidence(**kwargs)
    assert first.idempotency_key == second.idempotency_key
    assert Path(first.path).read_text() == Path(second.path).read_text()


def test_target_resolution_uses_other_known_participant():
    mapping = {
        "Utaro": {"member_id": "member-taro", "instance_id": "instance-taro"},
        "Ukikuchi": {"member_id": "member-kikuchi", "instance_id": "instance-kikuchi"},
    }
    target = _line.resolve_task_target(
        sender_user_id="Utaro",
        assignee="other_participant",
        participant_mapping=mapping,
    )
    assert target == ("member-kikuchi", "instance-kikuchi")


def test_target_resolution_fails_closed_when_other_is_ambiguous():
    mapping = {
        "Utaro": {"member_id": "member-taro", "instance_id": "instance-taro"},
        "Ua": {"member_id": "member-a", "instance_id": "instance-a"},
        "Ub": {"member_id": "member-b", "instance_id": "instance-b"},
    }
    with pytest.raises(ValueError, match="unambiguous"):
        _line.resolve_task_target(
            sender_user_id="Utaro",
            assignee="other_participant",
            participant_mapping=mapping,
        )


def test_task_prompt_treats_chat_as_data_and_requires_strict_json():
    prompt = _line.build_task_intake_prompt(
        group_id="Cgroup", sender_user_id="Utaro", message_id="m-123"
    )
    assert "untrusted conversation data" in prompt
    assert '"kind":"none"' in prompt
    assert '"kind":"task"' in prompt
    assert "Do not call tools" in prompt
    assert "Direct requests" in prompt
    assert "same language" in prompt


@pytest.mark.asyncio
async def test_no_task_model_output_is_suppressed(monkeypatch, tmp_path):
    cfg = type("Cfg", (), {"extra": {
        "task_intake_groups": ["Cgroup"],
        "task_participants": {
            "Utaro": {"member_id": "member-taro", "instance_id": "instance-taro"},
            "Ukikuchi": {"member_id": "member-kikuchi", "instance_id": "instance-kikuchi"},
        },
        "task_evidence_root": str(tmp_path),
    }})()
    adapter = _line.LineAdapter(cfg)
    adapter._client = AsyncMock()
    adapter._task_contexts["Cgroup"] = {
        "sender_user_id": "Utaro", "message_id": "m1", "webhook_event_id": "e1",
        "text": "今日は暑いですね", "timestamp_ms": 1,
    }

    result = await adapter.send("Cgroup", '{"kind":"none","reason":"雑談"}')

    assert result.success is True
    adapter._client.reply.assert_not_awaited()
    adapter._client.push.assert_not_awaited()
    assert "Cgroup" not in adapter._task_contexts


@pytest.mark.asyncio
async def test_clear_task_posts_metadata_then_sends_short_receipt(tmp_path):
    captured = []
    async def writer(payload):
        captured.append(payload)
        return {"ok": True, "taskId": "task-123", "status": "queued"}

    cfg = type("Cfg", (), {"extra": {
        "task_intake_groups": ["Cgroup"],
        "task_participants": {
            "Utaro": {"member_id": "member-taro", "instance_id": "instance-taro"},
            "Ukikuchi": {"member_id": "member-kikuchi", "instance_id": "instance-kikuchi"},
        },
        "task_evidence_root": str(tmp_path),
        "task_workspace_id": "medical-horizon",
        "task_writer": writer,
    }})()
    adapter = _line.LineAdapter(cfg)
    adapter._client = AsyncMock()
    adapter._client.reply.return_value = {"ok": True}
    adapter._reply_tokens["Cgroup"] = ("reply-token", 9_999_999_999.0)
    adapter._task_contexts["Cgroup"] = {
        "sender_user_id": "Utaro", "message_id": "m1", "webhook_event_id": "e1",
        "text": "菊地さん、提案資料を金曜までに更新してください", "timestamp_ms": 1,
    }
    output = json.dumps({
        "kind": "task", "summary": "提案資料を金曜日までに更新する",
        "assignee": "other_participant", "priority": "normal",
    }, ensure_ascii=False)

    result = await adapter.send("Cgroup", output)

    assert result.success is True
    assert len(captured) == 1
    serialized = json.dumps(captured[0], ensure_ascii=False)
    assert "菊地さん" not in serialized
    assert "sourceRef" in serialized
    assert captured[0]["targetMemberId"] == "member-kikuchi"
    sent = adapter._client.reply.await_args.args[1][0]["text"]
    assert sent == "✅ タスク登録: 提案資料を金曜日までに更新する"


def test_prepare_inbound_task_context_only_for_configured_text_group(tmp_path):
    cfg = type("Cfg", (), {"extra": {
        "task_intake_groups": ["Cgroup"],
        "task_invocation_prefixes": [],
        "task_evidence_root": str(tmp_path),
    }})()
    adapter = _line.LineAdapter(cfg)
    event = {
        "webhookEventId": "evt-1", "timestamp": 123,
        "source": {"type": "group", "groupId": "Cgroup", "userId": "Utaro"},
        "message": {"type": "text", "id": "m1", "text": "資料を更新してください"},
    }

    prompt = adapter._prepare_task_intake(event)

    assert prompt and "strict JSON" not in prompt
    assert adapter._task_contexts["Cgroup"]["message_id"] == "m1"


def test_configured_invocation_prefix_requires_mapped_sender_and_strips_marker(tmp_path):
    cfg = type("Cfg", (), {"extra": {
        "task_intake_groups": ["Cgroup"],
        "task_invocation_prefixes": ["@Sinria"],
        "task_participants": {
            "Utaro": {"member_id": "member-taro", "instance_id": "instance-taro"},
        },
        "task_evidence_root": str(tmp_path),
    }})()
    adapter = _line.LineAdapter(cfg)
    base = {"timestamp": 123, "source": {"type": "group", "groupId": "Cgroup"}}

    assert adapter._prepare_task_intake({
        **base,
        "webhookEventId": "evt-unmapped",
        "source": {**base["source"], "userId": "Uunknown"},
        "message": {"type": "text", "id": "m-unmapped", "text": "@Sinria 更新して"},
    }) is None
    assert adapter._prepare_task_intake({
        **base,
        "webhookEventId": "evt-plain",
        "source": {**base["source"], "userId": "Utaro"},
        "message": {"type": "text", "id": "m-plain", "text": "通常会話"},
    }) is None
    prompt = adapter._prepare_task_intake({
        **base,
        "webhookEventId": "evt-task",
        "source": {**base["source"], "userId": "Utaro"},
        "message": {"type": "text", "id": "m-task", "text": "@Sinria 更新して"},
    })

    assert prompt is not None
    assert "verified explicit Sinria invocation" in prompt
    assert adapter._task_contexts["Cgroup"]["text"] == "更新して"
    assert all(
        item["sender_user_id"] == "Utaro"
        for item in adapter._task_conversation_history["Cgroup"]
    )


@pytest.mark.asyncio
async def test_task_intake_group_never_emits_typing_or_slow_response_bubble(tmp_path):
    cfg = type("Cfg", (), {"extra": {
        "task_intake_groups": ["Cgroup"],
        "task_evidence_root": str(tmp_path),
    }})()
    adapter = _line.LineAdapter(cfg)
    adapter._client = AsyncMock()

    await adapter.send_typing("Cgroup")
    await adapter._keep_typing("Cgroup")

    adapter._client.loading.assert_not_awaited()
    adapter._client.reply.assert_not_awaited()
    adapter._client.push.assert_not_awaited()


@pytest.mark.asyncio
async def test_setup_notice_is_suppressed_without_consuming_task_context(tmp_path):
    cfg = type("Cfg", (), {"extra": {
        "task_intake_groups": ["Cgroup"], "task_evidence_root": str(tmp_path),
    }})()
    adapter = _line.LineAdapter(cfg)
    adapter._client = AsyncMock()
    adapter._task_contexts["Cgroup"] = {"message_id": "m1"}

    result = await adapter.send("Cgroup", "📬 No home channel is set for Line.")

    assert result.success is True
    assert adapter._task_contexts["Cgroup"]["message_id"] == "m1"
    adapter._client.reply.assert_not_awaited()
    adapter._client.push.assert_not_awaited()


@pytest.mark.asyncio
async def test_configured_group_bypasses_agent_and_uses_local_classifier(tmp_path):
    cfg = type("Cfg", (), {"extra": {
        "allowed_groups": ["Cgroup"],
        "task_intake_groups": ["Cgroup"],
        "task_invocation_prefixes": [],
        "task_intake_local_model": "qwen3.5:9b",
        "task_evidence_root": str(tmp_path),
    }})()
    adapter = _line.LineAdapter(cfg)
    adapter._client = AsyncMock()
    adapter.handle_message = AsyncMock()
    adapter._classify_task_intake_locally = AsyncMock(
        return_value='{"kind":"none"}'
    )
    adapter._handle_task_intake_response = AsyncMock(
        return_value=_line.SendResult(success=True)
    )
    event = {
        "type": "message", "webhookEventId": "evt-1", "replyToken": "reply-1",
        "timestamp": 1,
        "source": {"type": "group", "groupId": "Cgroup", "userId": "Utaro"},
        "message": {"type": "text", "id": "m1", "text": "資料を更新してください"},
    }

    await adapter._handle_message_event(event)

    adapter.handle_message.assert_not_awaited()
    adapter._classify_task_intake_locally.assert_awaited_once()
    adapter._handle_task_intake_response.assert_awaited_once()
    assert "Cgroup" not in adapter._task_contexts


def test_local_classifier_rejects_non_loopback_url(tmp_path):
    cfg = type("Cfg", (), {"extra": {
        "task_intake_local_model": "qwen3.5:9b",
        "task_intake_local_url": "https://api.example.com",
        "task_evidence_root": str(tmp_path),
    }})()
    adapter = _line.LineAdapter(cfg)

    with pytest.raises(RuntimeError, match="loopback"):
        __import__("asyncio").run(adapter._classify_task_intake_locally("秘密", "system"))


def test_local_classifier_request_contract_disables_thinking():
    source = Path(str(_line.__file__)).read_text(encoding="utf-8")
    classifier = source[
        source.index("async def _classify_task_intake_locally"):
        source.index("async def _handle_postback_event")
    ]
    assert '"think": False' in classifier
    assert "timeout=120.0" in classifier
