from unittest.mock import AsyncMock

import pytest

from plugins.platforms.line import adapter as line


def test_quote_relationship_is_role_only_and_contains_no_platform_ids(tmp_path):
    cfg = type("Cfg", (), {"extra": {
        "task_intake_groups": ["Cgroup"],
        "task_evidence_root": str(tmp_path),
    }})()
    adapter = line.LineAdapter(cfg)
    adapter._prepare_task_intake({
        "webhookEventId": "evt-1", "timestamp": 123,
        "source": {"type": "group", "groupId": "Cgroup", "userId": "Utaro"},
        "message": {"type": "text", "id": "m1", "text": "日程を決めよう"},
    })
    adapter._prepare_task_intake({
        "webhookEventId": "evt-2", "timestamp": 124,
        "source": {"type": "group", "groupId": "Cgroup", "userId": "Ukikuchi"},
        "message": {"type": "text", "id": "m2", "text": "了解です！", "quotedMessageId": "m1"},
    })

    rendered = adapter._task_contexts["Cgroup"]["classifier_text"]
    assert "[sender replying_to=other_participant] 了解です！" in rendered
    assert all(raw not in rendered for raw in ("m1", "m2", "Utaro", "Ukikuchi", "Cgroup"))


@pytest.mark.asyncio
async def test_webhook_exact_quoted_reply_advances_only_human_reply_state(tmp_path):
    cfg = type("Cfg", (), {"extra": {
        "allowed_groups": ["Cgroup"],
        "human_confirmation_db": str(tmp_path / "human-confirmation.sqlite3"),
    }})()
    adapter = line.LineAdapter(cfg)
    adapter._client = AsyncMock()
    adapter.handle_message = AsyncMock()
    store = adapter._human_confirmation_store
    assert store is not None
    pending = store.register(
        conversation_id="Cgroup", source_type="group", expected_sender_id="Ukikuchi",
        expected_reply="Sinria接続準備完了", outbound_message_id="m-out",
        purpose="peer_onboarding", now_ms=1000,
    )

    await adapter._handle_message_event({
        "type": "message", "webhookEventId": "evt-confirm", "replyToken": "reply-confirm",
        "timestamp": 1500,
        "source": {"type": "group", "groupId": "Cgroup", "userId": "Ukikuchi"},
        "message": {
            "type": "text", "id": "m-in", "text": "Sinria接続準備完了",
            "quotedMessageId": "m-out",
        },
    })

    receipt = store.get(pending.confirmation_id, now_ms=1500)
    assert receipt.status == "human_replied"
    assert receipt.human_confirmed is True
