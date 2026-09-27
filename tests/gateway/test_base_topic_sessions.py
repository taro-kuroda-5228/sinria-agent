"""Tests for BasePlatformAdapter topic-aware session handling."""

import asyncio
from types import SimpleNamespace

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, ProcessingOutcome, SendResult
from gateway.session import SessionSource, build_session_key


class DummyTelegramAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="fake-token"), Platform.TELEGRAM)
        self.sent = []
        self.typing = []
        self.processing_hooks = []

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        self.sent.append(
            {
                "chat_id": chat_id,
                "content": content,
                "reply_to": reply_to,
                "metadata": metadata,
            }
        )
        return SendResult(success=True, message_id="1")

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        self.typing.append({"chat_id": chat_id, "metadata": metadata})
        return None

    async def get_chat_info(self, chat_id: str):
        return {"id": chat_id}

    async def on_processing_start(self, event: MessageEvent) -> None:
        self.processing_hooks.append(("start", event.message_id))

    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:
        self.processing_hooks.append(("complete", event.message_id, outcome))


def _make_event(chat_id: str, thread_id: str, message_id: str = "1") -> MessageEvent:
    return MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id=chat_id,
            chat_type="group",
            thread_id=thread_id,
        ),
        message_id=message_id,
    )


class TestBasePlatformTopicSessions:
    @pytest.mark.asyncio
    async def test_handle_message_does_not_interrupt_different_topic(self, monkeypatch):
        adapter = DummyTelegramAdapter()
        adapter.set_message_handler(lambda event: asyncio.sleep(0, result=None))

        active_event = _make_event("-1001", "10")
        adapter._active_sessions[build_session_key(active_event.source)] = asyncio.Event()

        scheduled = []

        def fake_create_task(coro):
            scheduled.append(coro)
            coro.close()
            return SimpleNamespace()

        monkeypatch.setattr(asyncio, "create_task", fake_create_task)

        await adapter.handle_message(_make_event("-1001", "11"))

        assert len(scheduled) == 1
        assert adapter._pending_messages == {}

    @pytest.mark.asyncio
    async def test_handle_message_interrupts_same_topic(self, monkeypatch):
        adapter = DummyTelegramAdapter()
        adapter.set_message_handler(lambda event: asyncio.sleep(0, result=None))

        active_event = _make_event("-1001", "10")
        adapter._active_sessions[build_session_key(active_event.source)] = asyncio.Event()

        scheduled = []

        def fake_create_task(coro):
            scheduled.append(coro)
            coro.close()
            return SimpleNamespace()

        monkeypatch.setattr(asyncio, "create_task", fake_create_task)

        pending_event = _make_event("-1001", "10", message_id="2")
        await adapter.handle_message(pending_event)

        assert scheduled == []
        assert adapter.get_pending_message(build_session_key(pending_event.source)) == pending_event

    @pytest.mark.asyncio
    async def test_process_message_background_replies_in_same_topic(self):
        adapter = DummyTelegramAdapter()
        typing_calls = []

        async def handler(_event):
            await asyncio.sleep(0)
            return "ack"

        async def hold_typing(_chat_id, interval=2.0, metadata=None):
            typing_calls.append({"chat_id": _chat_id, "metadata": metadata})
            await asyncio.Event().wait()

        adapter.set_message_handler(handler)
        adapter._keep_typing = hold_typing

        event = _make_event("-1001", "17585")
        await adapter._process_message_background(event, build_session_key(event.source))

        assert adapter.sent == [
            {
                "chat_id": "-1001",
                "content": "ack",
                "reply_to": None,
                "metadata": {"thread_id": "17585", "notify": True},
            }
        ]
        assert typing_calls == [
            {
                "chat_id": "-1001",
                "metadata": {"thread_id": "17585"},
            }
        ]
        assert adapter.processing_hooks == [
            ("start", "1"),
            ("complete", "1", ProcessingOutcome.SUCCESS),
        ]

    @pytest.mark.asyncio
    async def test_process_message_background_marks_total_send_failure_unsuccessful(self):
        adapter = DummyTelegramAdapter()

        async def handler(_event):
            await asyncio.sleep(0)
            return "ack"

        async def failing_send(*_args, **_kwargs):
            return SendResult(success=False, error="send failed")

        async def hold_typing(_chat_id, interval=2.0, metadata=None):
            await asyncio.Event().wait()

        adapter.set_message_handler(handler)
        adapter.send = failing_send
        adapter._keep_typing = hold_typing

        event = _make_event("-1001", "17585")
        await adapter._process_message_background(event, build_session_key(event.source))

        assert adapter.processing_hooks == [
            ("start", "1"),
            ("complete", "1", ProcessingOutcome.FAILURE),
        ]

    @pytest.mark.asyncio
    async def test_required_text_failure_is_not_masked_by_successful_media(self):
        """Every required root-response component must succeed before callback release."""
        adapter = DummyTelegramAdapter()
        adapter.set_message_handler(lambda _event: asyncio.sleep(0, result="response"))
        adapter.extract_media = lambda content: ([("/tmp/report.pdf", False)], "response")
        adapter.extract_images = lambda content: ([], content)
        adapter.extract_local_files = lambda content: ([], content)

        async def hold_typing(chat_id, interval=2.0, metadata=None, stop_event=None):
            await asyncio.Event().wait()

        adapter._keep_typing = hold_typing
        attempts = []

        async def fail_text(chat_id, content, reply_to=None, metadata=None):
            attempts.append(content)
            return SendResult(success=False, error="permission denied")

        async def send_document(chat_id, file_path, caption=None, file_name=None, reply_to=None, metadata=None, **kwargs):
            return SendResult(success=True, message_id="media")

        adapter.send = fail_text
        adapter.send_document = send_document
        source = _make_event("-1001", "17585").source
        session_key = build_session_key(source)
        released = []
        adapter.register_post_delivery_callback(session_key, lambda: released.append(True))
        event = _make_event("-1001", "17585")

        await adapter._process_message_background(event, session_key)

        assert attempts, "required text delivery should be attempted"
        assert adapter.processing_hooks[-1] == ("complete", "1", ProcessingOutcome.FAILURE)
        assert released == []

    @pytest.mark.asyncio
    async def test_required_text_failure_is_not_masked_by_optional_tts(self, monkeypatch, tmp_path):
        """Successful auto-TTS is optional and cannot release a failed text callback."""
        import tools.tts_tool as tts_tool

        audio_path = tmp_path / "reply.mp3"
        audio_path.write_bytes(b"audio")
        monkeypatch.setattr(tts_tool, "check_tts_requirements", lambda: True)
        monkeypatch.setattr(
            tts_tool,
            "text_to_speech_tool",
            lambda **_kwargs: '{"file_path": "' + str(audio_path) + '"}',
        )

        adapter = DummyTelegramAdapter()
        adapter.set_message_handler(lambda _event: asyncio.sleep(0, result="response"))
        adapter._should_auto_tts_for_chat = lambda chat_id: True

        async def hold_typing(chat_id, interval=2.0, metadata=None, stop_event=None):
            await asyncio.Event().wait()

        adapter._keep_typing = hold_typing
        played = []

        async def play_tts(chat_id, audio_path, **kwargs):
            played.append(audio_path)
            return SendResult(success=True, message_id="audio")

        async def fail_text(*_args, **_kwargs):
            return SendResult(success=False, error="permission denied")

        adapter.play_tts = play_tts
        adapter.send = fail_text
        source = _make_event("-1001", "17585").source
        session_key = build_session_key(source)
        released = []
        adapter.register_post_delivery_callback(session_key, lambda: released.append(True))
        event = _make_event("-1001", "17585")
        event.message_type = MessageType.VOICE

        await adapter._process_message_background(event, session_key)

        assert played == [str(audio_path)]
        assert adapter.processing_hooks[-1] == ("complete", "1", ProcessingOutcome.FAILURE)
        assert released == []

    @pytest.mark.asyncio
    async def test_process_message_background_marks_exception_unsuccessful(self):
        adapter = DummyTelegramAdapter()

        async def handler(_event):
            await asyncio.sleep(0)
            raise RuntimeError("boom")

        async def hold_typing(_chat_id, interval=2.0, metadata=None):
            await asyncio.Event().wait()

        adapter.set_message_handler(handler)
        adapter._keep_typing = hold_typing

        event = _make_event("-1001", "17585")
        await adapter._process_message_background(event, build_session_key(event.source))

        assert adapter.processing_hooks == [
            ("start", "1"),
            ("complete", "1", ProcessingOutcome.FAILURE),
        ]

    @pytest.mark.asyncio
    async def test_process_message_background_marks_cancellation_unsuccessful(self):
        adapter = DummyTelegramAdapter()
        release = asyncio.Event()

        async def handler(_event):
            await release.wait()
            return "ack"

        async def hold_typing(_chat_id, interval=2.0, metadata=None):
            await asyncio.Event().wait()

        adapter.set_message_handler(handler)
        adapter._keep_typing = hold_typing

        event = _make_event("-1001", "17585")
        task = asyncio.create_task(adapter._process_message_background(event, build_session_key(event.source)))
        await asyncio.sleep(0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert adapter.processing_hooks == [
            ("start", "1"),
            ("complete", "1", ProcessingOutcome.FAILURE),
        ]

    @pytest.mark.asyncio
    async def test_cancel_background_tasks_marks_expected_cancellation_cancelled(self):
        adapter = DummyTelegramAdapter()
        release = asyncio.Event()

        async def handler(_event):
            await release.wait()
            return "ack"

        async def hold_typing(_chat_id, interval=2.0, metadata=None):
            await asyncio.Event().wait()

        adapter.set_message_handler(handler)
        adapter._keep_typing = hold_typing

        event = _make_event("-1001", "17585")
        await adapter.handle_message(event)
        await asyncio.sleep(0)

        await adapter.cancel_background_tasks()

        assert adapter.processing_hooks == [
            ("start", "1"),
            ("complete", "1", ProcessingOutcome.CANCELLED),
        ]
