"""Local-only searchable memory for allowlisted LINE company conversations."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest


def test_sanitized_memory_survives_reopen_and_is_searchable_without_raw_ids(tmp_path):
    from agent.line_conversation_memory import LineConversationMemoryStore

    db_path = tmp_path / "private" / "line" / "conversation-memory.sqlite3"
    store = LineConversationMemoryStore(db_path=db_path, retention_days=365)
    receipt = store.remember(
        group_id="C-secret-group",
        sender_user_id="U-secret-user",
        author_member_id="member-taro",
        webhook_event_id="evt-secret",
        message_id="msg-secret",
        timestamp_ms=1_800_000_000_000,
        summary="営業資料の構成について相談を開始した",
        topic="営業資料",
        memory_kind="discussion",
    )
    store.close()

    reopened = LineConversationMemoryStore(db_path=db_path, retention_days=365)
    result = reopened.search("営業資料", limit=5, now_ms=1_800_000_000_100)

    assert result[0]["summary"] == "営業資料の構成について相談を開始した"
    assert result[0]["author_member_id"] == "member-taro"
    assert result[0]["source_citation"] == receipt.source_citation
    assert result[0]["status"] == "active"
    assert oct(db_path.stat().st_mode & 0o777) == "0o600"
    assert oct(db_path.parent.stat().st_mode & 0o777) == "0o700"
    raw = db_path.read_bytes()
    assert b"C-secret-group" not in raw
    assert b"U-secret-user" not in raw
    assert b"evt-secret" not in raw
    assert b"msg-secret" not in raw


@pytest.mark.parametrize(
    "unsafe",
    [
        "患者ID: 12345の診療内容を確認した",
        "患者Aの症状と診療内容を確認した",
        "連絡先は taro@example.com",
        "電話番号は 090-1234-5678",
        "access token: secret-value",
    ],
)
def test_memory_rejects_sensitive_or_credential_summary(tmp_path, unsafe):
    from agent.line_conversation_memory import LineConversationMemoryStore

    store = LineConversationMemoryStore(db_path=tmp_path / "memory.sqlite3")
    with pytest.raises(ValueError, match="sensitive"):
        store.remember(
            group_id="Cgroup",
            sender_user_id="Usender",
            author_member_id="member-taro",
            webhook_event_id="evt-1",
            message_id="m-1",
            timestamp_ms=1,
            summary=unsafe,
            topic="業務",
            memory_kind="fact",
        )
    assert store.search("業務", now_ms=2) == []


def test_correction_supersedes_prior_memory_and_preserves_history(tmp_path):
    from agent.line_conversation_memory import LineConversationMemoryStore

    store = LineConversationMemoryStore(db_path=tmp_path / "memory.sqlite3")
    old = store.remember(
        group_id="Cgroup", sender_user_id="Usender", author_member_id="member-taro",
        webhook_event_id="evt-1", message_id="m-1", timestamp_ms=100,
        summary="営業資料は旧版を正本として扱う", topic="営業資料", memory_kind="decision",
    )
    new = store.remember(
        group_id="Cgroup", sender_user_id="Usender", author_member_id="member-taro",
        webhook_event_id="evt-2", message_id="m-2", timestamp_ms=200,
        summary="営業資料は共有ドライブ版を正本として扱う", topic="営業資料",
        memory_kind="correction", supersedes_id=old.memory_id,
    )

    active = store.search("営業資料", now_ms=300)
    history = store.search("営業資料", include_inactive=True, now_ms=300)
    assert [item["memory_id"] for item in active] == [new.memory_id]
    assert {item["status"] for item in history} == {"active", "superseded"}
    assert next(item for item in history if item["memory_id"] == old.memory_id)["superseded_by"] == new.memory_id


def test_scoped_search_prioritizes_time_author_group_and_any_keyword_match(tmp_path):
    from agent.line_conversation_memory import LineConversationMemoryStore

    store = LineConversationMemoryStore(db_path=tmp_path / "memory.sqlite3")
    store.remember(
        group_id="C-company", sender_user_id="Ukikuchi", author_member_id="member-kikuchi",
        webhook_event_id="evt-current", message_id="m-current", timestamp_ms=200,
        summary="OpenAI Codex の利用上限と fallback について相談した",
        topic="Sinria provider limit", memory_kind="status",
    )
    store.remember(
        group_id="C-other", sender_user_id="Utaro", author_member_id="member-taro",
        webhook_event_id="evt-old", message_id="m-old", timestamp_ms=100,
        summary="契約書とメドエビデンスの対応を相談した",
        topic="別件", memory_kind="discussion",
    )

    recent = store.search(
        "provider 契約書", now_ms=300, after_ms=150,
        author_member_id="member-kikuchi", memory_kinds=["status"], match="any",
    )
    assert [item["summary"] for item in recent] == ["OpenAI Codex の利用上限と fallback について相談した"]
    group_ref = recent[0]["group_ref"]
    assert group_ref.startswith("line-group:")
    assert store.search("", now_ms=300, group_ref=group_ref)[0]["created_at_ms"] == 200
    assert store.search("provider 契約書", now_ms=300, match="all") == []


def test_memory_coverage_exposes_only_timestamp_bounds(tmp_path):
    from agent.line_conversation_memory import LineConversationMemoryStore

    store = LineConversationMemoryStore(db_path=tmp_path / "memory.sqlite3")
    store.remember(
        group_id="C-secret", sender_user_id="U-secret", author_member_id="member-taro",
        webhook_event_id="evt", message_id="m", timestamp_ms=123,
        summary="共有資料について相談した", topic="資料", memory_kind="discussion",
    )
    assert store.coverage(now_ms=124) == {
        "count": 1, "earliest_created_at_ms": 123, "latest_created_at_ms": 123,
    }


def test_correction_lookup_can_be_scoped_to_source_group(tmp_path):
    from agent.line_conversation_memory import LineConversationMemoryStore

    store = LineConversationMemoryStore(db_path=tmp_path / "memory.sqlite3")
    group_a = store.remember(
        group_id="Cgroup-a", sender_user_id="Ua", author_member_id="member-a",
        webhook_event_id="evt-a", message_id="m-a", timestamp_ms=100,
        summary="営業資料は旧版を正本として扱う", topic="営業資料", memory_kind="decision",
    )
    store.remember(
        group_id="Cgroup-b", sender_user_id="Ub", author_member_id="member-b",
        webhook_event_id="evt-b", message_id="m-b", timestamp_ms=200,
        summary="営業資料のデザインを検討した", topic="営業資料", memory_kind="discussion",
    )

    scoped = store.search("営業資料", group_id="Cgroup-a", now_ms=300)

    assert [item["memory_id"] for item in scoped] == [group_a.memory_id]


def test_delete_tombstones_record_and_retention_purges_expired_content(tmp_path):
    from agent.line_conversation_memory import LineConversationMemoryStore

    day_ms = 86_400_000
    store = LineConversationMemoryStore(db_path=tmp_path / "memory.sqlite3", retention_days=1)
    assert store._conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
    deleted = store.remember(
        group_id="Cgroup", sender_user_id="Usender", author_member_id="member-taro",
        webhook_event_id="evt-delete", message_id="m-delete", timestamp_ms=day_ms,
        summary="削除対象の運用メモ", topic="運用", memory_kind="fact",
    )
    store.delete(deleted.memory_id, deleted_at_ms=day_ms + 1)
    assert store.search("削除対象", include_inactive=True, now_ms=day_ms + 2) == []

    expired = store.remember(
        group_id="Cgroup", sender_user_id="Usender", author_member_id="member-taro",
        webhook_event_id="evt-expire", message_id="m-expire", timestamp_ms=2 * day_ms,
        summary="期限切れになる運用メモ", topic="運用", memory_kind="fact",
    )
    assert store.search("期限切れ", now_ms=2 * day_ms + 1)
    assert store.purge_expired(now_ms=3 * day_ms + 1) == 1
    assert store.search("期限切れ", include_inactive=True, now_ms=3 * day_ms + 2) == []
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute("SELECT status, summary FROM memories WHERE memory_id = ?", (expired.memory_id,)).fetchone()
    assert row == ("deleted", "")


def test_search_tool_returns_only_bounded_sanitized_records(tmp_path, monkeypatch):
    from agent.line_conversation_memory import LineConversationMemoryStore
    from tools.line_conversation_memory_tool import line_conversation_search

    db_path = tmp_path / "conversation-memory.sqlite3"
    monkeypatch.setenv("SINRIA_LINE_CONVERSATION_MEMORY_DB", str(db_path))
    store = LineConversationMemoryStore(db_path=db_path)
    store.remember(
        group_id="Cgroup", sender_user_id="Usender", author_member_id="member-taro",
        webhook_event_id="evt-1", message_id="m-1", timestamp_ms=1_800_000_000_000,
        summary="製品ロードマップの優先順位を相談した", topic="製品ロードマップ",
        memory_kind="discussion",
    )
    store.close()

    payload = json.loads(line_conversation_search("ロードマップ", limit=100))
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["results"][0]["summary"] == "製品ロードマップの優先順位を相談した"
    assert "group_id" not in json.dumps(payload)
    assert "sender_user_id" not in json.dumps(payload)
    assert payload["limit"] == 20
