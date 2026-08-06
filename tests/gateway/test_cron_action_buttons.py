"""Discord cron notifications route decisions back into the same conversation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cron.action_executor import ActionExecutionResult
from cron.action_runtime import CronActionState, CronActionStore
from gateway.config import Platform, PlatformConfig
from gateway.platforms.discord import CronActionView, DiscordAdapter


def _adapter() -> DiscordAdapter:
    adapter = DiscordAdapter.__new__(DiscordAdapter)
    adapter._platform = Platform.DISCORD
    adapter.config = PlatformConfig(enabled=True)
    adapter._allowed_user_ids = {"123"}
    adapter._allowed_role_ids = set()
    adapter._cron_action_notifications = {}
    adapter._resolved_cron_action_messages = set()
    adapter.handle_message = AsyncMock()
    adapter.execute_cron_action = AsyncMock()
    return adapter


def _interaction(user_id=123):
    user = SimpleNamespace(id=user_id, display_name="Taro", roles=[])
    channel = SimpleNamespace(id=777, name="ops", parent_id=None, guild=None)
    response = SimpleNamespace(send_message=AsyncMock())
    message = SimpleNamespace(id=999, content="persisted notification", edit=AsyncMock())
    return SimpleNamespace(
        user=user,
        channel=channel,
        channel_id=777,
        guild=None,
        response=response,
        message=message,
    )


def _durable_view(tmp_path):
    store = CronActionStore(tmp_path / "actions.db")
    action = store.create(
        action_id="action-123",
        profile="default",
        payload={
            "action_type": "continue_cron_run",
            "summary": "Resume the source cron run",
            "job_id": "job-42",
            "delivery": {
                "platform": "discord",
                "chat_id": "777",
                "message_id": "999",
            },
        },
        expires_at=9999999999.0,
    )
    waiting = store.transition(
        action.action_id,
        CronActionState.AWAITING_DECISION,
        expected_version=action.version,
    )
    adapter = _adapter()
    view = CronActionView(
        adapter=adapter,
        notification="safe notification",
        action_context={"action_id": waiting.action_id, "version": waiting.version},
        allowed_user_ids={"123"},
        action_store=store,
    )
    return adapter, store, waiting, view


@pytest.mark.asyncio
async def test_durable_approve_persists_decision_without_prompt_reinjection(tmp_path):
    adapter, store, waiting, view = _durable_view(tmp_path)
    await view._dispatch(_interaction(), "approve")

    assert store.get(waiting.action_id).state is CronActionState.APPROVED
    adapter.handle_message.assert_not_awaited()
    adapter.execute_cron_action.assert_awaited_once_with(
        waiting.action_id,
        channel_id="777",
        message=view.message,
    )
    assert all(waiting.action_id in child.custom_id for child in view.children)


@pytest.mark.asyncio
async def test_durable_duplicate_decision_is_reported_and_not_reexecuted(tmp_path):
    adapter, store, waiting, view = _durable_view(tmp_path)
    await view._dispatch(_interaction(), "approve")
    duplicate = _interaction()
    await view._dispatch(duplicate, "approve")

    assert store.get(waiting.action_id).state is CronActionState.APPROVED
    adapter.handle_message.assert_not_awaited()
    duplicate.response.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_adapter_executes_approved_action_and_reports_verified_readback(tmp_path, monkeypatch):
    adapter, store, waiting, _view = _durable_view(tmp_path)
    approved = store.decide(
        waiting.action_id,
        CronActionState.APPROVED,
        actor_id="123",
        expected_version=waiting.version,
    )
    adapter._cron_action_store = store
    channel = SimpleNamespace(send=AsyncMock())
    adapter._client = SimpleNamespace(
        get_channel=MagicMock(return_value=channel),
        fetch_channel=AsyncMock(return_value=channel),
    )
    adapter.execute_cron_action = DiscordAdapter.execute_cron_action.__get__(adapter, DiscordAdapter)
    monkeypatch.setattr(
        "cron.action_executor.scheduler_resume_runner",
        lambda action: ActionExecutionResult(
            success=True,
            output="provider readback ok\nSINRIA_ACTION_VERIFIED:action-123",
            verification_evidence={
                "provider_id": "p-1",
                "verification_marker": "SINRIA_ACTION_VERIFIED:action-123",
            },
        ),
    )
    message = SimpleNamespace(edit=AsyncMock())

    result = await adapter.execute_cron_action(
        approved.action_id,
        channel_id="777",
        message=message,
    )

    assert result.state is CronActionState.COMPLETED
    message.edit.assert_awaited_once_with(view=None)
    channel.send.assert_awaited_once()
    assert "readback確認まで完了" in channel.send.call_args.args[0]


@pytest.mark.asyncio
async def test_durable_unauthorized_decision_does_not_change_state(tmp_path):
    adapter, store, waiting, view = _durable_view(tmp_path)
    await view._dispatch(_interaction(user_id=999), "approve")

    assert store.get(waiting.action_id).state is CronActionState.AWAITING_DECISION
    adapter.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_durable_decision_rejects_a_different_discord_channel(tmp_path):
    adapter, store, waiting, view = _durable_view(tmp_path)
    interaction = _interaction()
    interaction.channel_id = 778
    interaction.channel.id = 778

    await view._dispatch(interaction, "approve")

    assert store.get(waiting.action_id).state is CronActionState.AWAITING_DECISION
    adapter.execute_cron_action.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()


def test_cron_action_view_has_required_japanese_buttons():
    view = CronActionView(
        adapter=_adapter(),
        notification="full notification",
        action_context={"job_id": "job-1"},
        allowed_user_ids={"123"},
    )

    labels = [child.label for child in view.children]
    assert labels == ["承認して進める", "却下", "詳細"]
    assert view.timeout is None


@pytest.mark.asyncio
async def test_persistent_cron_action_recovers_notification_from_discord_message():
    adapter = _adapter()
    adapter._build_slash_event = MagicMock(
        return_value=SimpleNamespace(text="", reply_to_text=None, reply_to_message_id=None)
    )
    interaction = _interaction()
    view = CronActionView(
        adapter=adapter,
        notification="",
        action_context={},
        allowed_user_ids={"123"},
    )

    await view._dispatch(interaction, "reject")

    event = adapter.handle_message.await_args.args[0]
    assert "persisted notification" in event.text
    assert event.reply_to_text == "persisted notification"


@pytest.mark.asyncio
async def test_cron_action_dispatch_binds_full_notification_and_same_channel():
    adapter = _adapter()
    adapter._build_slash_event = MagicMock(
        return_value=SimpleNamespace(
            text="",
            reply_to_text=None,
            reply_to_message_id=None,
        )
    )
    interaction = _interaction()
    notification = "**問題**\n" + ("root cause details " * 80)
    view = CronActionView(
        adapter=adapter,
        notification=notification,
        action_context={"job_id": "job-1", "job_name": "health-check"},
        allowed_user_ids={"123"},
    )

    await view._dispatch(interaction, "approve")

    event = adapter.handle_message.await_args.args[0]
    assert "承認して進める" in event.text
    assert notification in event.text
    assert event.reply_to_text == notification
    assert event.reply_to_message_id == "999"
    adapter._build_slash_event.assert_called_once()
    assert adapter._build_slash_event.call_args.args[0].channel_id == 777
    disabled_view = interaction.message.edit.await_args.kwargs["view"]
    assert all(child.disabled for child in disabled_view.children)


@pytest.mark.asyncio
async def test_cron_action_handoff_failure_keeps_buttons_retryable():
    adapter = _adapter()
    adapter._build_slash_event = MagicMock(
        return_value=SimpleNamespace(text="", reply_to_text=None, reply_to_message_id=None)
    )
    adapter.handle_message = AsyncMock(side_effect=RuntimeError("handoff failed"))
    interaction = _interaction()
    view = CronActionView(
        adapter=adapter,
        notification="full notification",
        action_context={"job_id": "job-1"},
        allowed_user_ids={"123"},
    )

    with pytest.raises(RuntimeError, match="handoff failed"):
        await view._dispatch(interaction, "reject")

    interaction.response.send_message.assert_awaited_once()
    interaction.message.edit.assert_not_awaited()
    assert "999" not in adapter._resolved_cron_action_messages


@pytest.mark.asyncio
async def test_cron_action_rejects_unauthorized_user():
    adapter = _adapter()
    interaction = _interaction(user_id=999)
    view = CronActionView(
        adapter=adapter,
        notification="notice",
        action_context={"job_id": "job-1"},
        allowed_user_ids={"123"},
    )

    await view._dispatch(interaction, "approve")

    adapter.handle_message.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_send_cron_action_preserves_full_context_for_every_chunk():
    adapter = _adapter()
    client = MagicMock()
    adapter._client = client
    adapter._cron_action_notifications = {}
    adapter._last_self_message_id = {}
    adapter._is_forum_parent = MagicMock(return_value=False)
    adapter.format_message = MagicMock(side_effect=lambda content: content)
    adapter.truncate_message = MagicMock(return_value=["part 1", "part 2"])

    channel = MagicMock()
    channel.send = AsyncMock(
        side_effect=[SimpleNamespace(id=101), SimpleNamespace(id=102)]
    )
    client.get_channel.return_value = channel

    result = await adapter.send_cron_action(
        chat_id="777",
        content="full notification",
        action_context={"job_id": "job-1"},
    )

    assert result.success is True
    assert adapter._cron_action_notifications == {
        "101": "full notification",
        "102": "full notification",
    }
    assert channel.send.await_args_list[0].kwargs["view"] is None
    assert channel.send.await_args_list[1].kwargs["view"] is not None


@pytest.mark.asyncio
async def test_send_cron_action_persists_final_message_binding(tmp_path):
    adapter, store, waiting, _view = _durable_view(tmp_path)
    adapter._cron_action_store = store
    client = MagicMock()
    adapter._client = client
    adapter._last_self_message_id = {}
    adapter._is_forum_parent = MagicMock(return_value=False)
    adapter.format_message = MagicMock(side_effect=lambda content: content)
    adapter.truncate_message = MagicMock(return_value=["part 1", "part 2"])
    channel = MagicMock()
    first_message = SimpleNamespace(id=101)
    final_message = SimpleNamespace(id=102, edit=AsyncMock())
    channel.send = AsyncMock(side_effect=[first_message, final_message])
    client.get_channel.return_value = channel
    context = {"action_id": waiting.action_id, "version": waiting.version}

    result = await adapter.send_cron_action(
        chat_id="777",
        content="full notification",
        action_context=context,
    )

    persisted = store.get(waiting.action_id)
    assert result.success is True
    assert persisted.payload["delivery"] == {
        "platform": "discord",
        "chat_id": "777",
        "thread_id": "",
        "message_id": "102",
    }
    assert context["version"] == persisted.version
    rebound_view = final_message.edit.await_args.kwargs["view"]
    assert rebound_view.action_version == persisted.version
    assert rebound_view.action_context["version"] == persisted.version
    assert all(
        f":{persisted.version}:" in str(child.custom_id)
        for child in rebound_view.children
    )
