"""Durable completion delivery for LINE-origin Agent OS tasks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.line_task_completion import (
    LineTaskCompletionStore,
    enqueue_line_task_completion,
)
from tests.gateway._plugin_adapter_loader import load_plugin_adapter


_line = load_plugin_adapter("line")


def _evidence(tmp_path: Path, *, group_id: str = "Cgroup") -> tuple[str, dict]:
    digest = hashlib.sha256(b"line-completion-evidence").hexdigest()
    root = tmp_path / "private" / "line" / "task-intake"
    root.mkdir(parents=True, mode=0o700)
    path = root / f"{digest}.json"
    value = {
        "schemaVersion": "sinria.line-task-evidence.v1",
        "groupId": group_id,
        "senderUserId": "Usender",
        "messageId": "m-source",
        "webhookEventId": "evt-source",
        "timestampMs": 1,
        "text": "@Sinria 調査して",
    }
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    path.chmod(0o600)
    return digest, value


def test_worker_enqueues_sanitized_terminal_result_from_private_line_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("SINRIA_HOME", str(tmp_path))
    digest, _ = _evidence(tmp_path)
    outbox = tmp_path / "private" / "line" / "completions.sqlite3"
    task = {"payload": {"sourceRef": f"local://line/task-intake/{digest}"}}

    first = enqueue_line_task_completion(
        task, task_id="task-1", status="completed",
        sanitized_summary="調査を完了しました", store_path=outbox,
    )
    second = enqueue_line_task_completion(
        task, task_id="task-1", status="completed",
        sanitized_summary="調査を完了しました", store_path=outbox,
    )

    assert first == second
    with LineTaskCompletionStore(outbox) as store:
        pending = store.pending()
        assert len(pending) == 1
        assert pending[0].conversation_id == "Cgroup"
        assert pending[0].source_message_id == "m-source"
        assert pending[0].summary == "調査を完了しました"
    assert oct(outbox.stat().st_mode & 0o777) == "0o600"


def test_worker_ignores_non_line_or_unsafe_evidence_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("SINRIA_HOME", str(tmp_path))
    assert enqueue_line_task_completion(
        {"payload": {"sourceRef": "https://example.test/raw"}},
        task_id="task-1", status="completed", sanitized_summary="done",
    ) is None


@pytest.mark.asyncio
async def test_adapter_delivers_only_final_result_once_to_allowlisted_origin(tmp_path):
    completion_db = tmp_path / "task-completions.sqlite3"
    cfg = type("Cfg", (), {"extra": {
        "task_intake_groups": ["Cgroup"],
        "task_completion_db": str(completion_db),
        "human_confirmation_db": str(tmp_path / "human-confirmation.sqlite3"),
    }})()
    adapter = _line.LineAdapter(cfg)
    adapter._client = AsyncMock()
    adapter._send_text_chunks = AsyncMock(return_value=SimpleNamespace(success=True))
    store = adapter._task_completion_store
    assert store is not None
    store.enqueue(
        task_id="task-1", conversation_id="Cgroup", source_message_id="m-source",
        status="completed", summary="調査を完了しました",
    )

    assert await adapter._drain_task_completion_outbox() == 1
    assert await adapter._drain_task_completion_outbox() == 0
    adapter._send_text_chunks.assert_awaited_once_with(
        "Cgroup", "調査を完了しました", force_push=True,
    )


@pytest.mark.asyncio
async def test_adapter_parks_indeterminate_delivery_and_blocks_unknown_group(tmp_path):
    completion_db = tmp_path / "task-completions.sqlite3"
    cfg = type("Cfg", (), {"extra": {
        "task_intake_groups": ["Cgroup"],
        "task_completion_db": str(completion_db),
        "human_confirmation_db": str(tmp_path / "human-confirmation.sqlite3"),
    }})()
    adapter = _line.LineAdapter(cfg)
    adapter._client = AsyncMock()
    adapter._send_text_chunks = AsyncMock(return_value=SimpleNamespace(success=False))
    store = adapter._task_completion_store
    assert store is not None
    store.enqueue(
        task_id="task-retry", conversation_id="Cgroup", source_message_id="m-1",
        status="failed_recoverable", summary="再試行が必要です",
    )
    store.enqueue(
        task_id="task-forged", conversation_id="Cunknown", source_message_id="m-2",
        status="completed", summary="送信してはいけない結果",
    )

    assert await adapter._drain_task_completion_outbox() == 0
    pending = store.pending()
    assert {item.task_id for item in pending} == {"task-forged"}
    state = store.connection.execute(
        "SELECT state FROM deliveries WHERE task_id='task-retry'"
    ).fetchone()["state"]
    assert state == "indeterminate"
    adapter._send_text_chunks.assert_awaited_once_with(
        "Cgroup", "未完了: 再試行が必要です", force_push=True,
    )


@pytest.mark.asyncio
async def test_adapter_parks_exception_as_indeterminate_without_blind_retry(tmp_path):
    completion_db = tmp_path / "task-completions.sqlite3"
    cfg = type("Cfg", (), {"extra": {
        "task_intake_groups": ["Cgroup"],
        "task_completion_db": str(completion_db),
        "human_confirmation_db": str(tmp_path / "human-confirmation.sqlite3"),
    }})()
    adapter = _line.LineAdapter(cfg)
    adapter._client = AsyncMock()
    adapter._send_text_chunks = AsyncMock(side_effect=TimeoutError("unknown effect"))
    store = adapter._task_completion_store
    assert store is not None
    delivery_id = store.enqueue(
        task_id="task-timeout", conversation_id="Cgroup", source_message_id="m-3",
        status="completed", summary="完了しました",
    )

    assert await adapter._drain_task_completion_outbox() == 0
    assert store.pending() == []
    state = store.connection.execute(
        "SELECT state FROM deliveries WHERE delivery_id=?", (delivery_id,)
    ).fetchone()["state"]
    assert state == "indeterminate"
    assert adapter._send_text_chunks.await_count == 1
