"""Tests for /v1/runs endpoints: start, status, events, and stop.

Covers:
- POST /v1/runs — start a run (202)
- GET /v1/runs/{run_id} — poll run status
- GET /v1/runs/{run_id}/events — SSE event stream
- POST /v1/runs/{run_id}/stop — interrupt a running agent
- Auth, error handling, and cleanup
"""

import asyncio
import json
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms import api_server as api_server_module
from gateway.platforms.api_server import (
    APIServerAdapter,
    cors_middleware,
    security_headers_middleware,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(api_key: str = "") -> APIServerAdapter:
    """Create an adapter with optional API key."""
    extra = {}
    if api_key:
        extra["key"] = api_key
    config = PlatformConfig(enabled=True, extra=extra)
    adapter = APIServerAdapter(config)
    return adapter


def _create_runs_app(adapter: APIServerAdapter) -> web.Application:
    """Create an aiohttp app with /v1/runs routes registered."""
    mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
    app = web.Application(middlewares=mws)
    app["api_server_adapter"] = adapter
    app.router.add_post("/v1/runs", adapter._handle_runs)
    app.router.add_get("/v1/runs/{run_id}", adapter._handle_get_run)
    app.router.add_get("/v1/runs/{run_id}/events", adapter._handle_run_events)
    app.router.add_post("/v1/runs/{run_id}/approval", adapter._handle_run_approval)
    app.router.add_post("/v1/runs/{run_id}/stop", adapter._handle_stop_run)
    return app


def _make_slow_agent(**kwargs):
    """Create a mock agent that blocks in run_conversation until interrupted.

    Returns (mock_agent, agent_ready_event, interrupt_event) where
    agent_ready_event is set once run_conversation starts, and
    interrupt_event is set when interrupt() is called.
    """
    ready = threading.Event()
    interrupted = threading.Event()

    mock_agent = MagicMock()

    def _do_interrupt(message=None):
        interrupted.set()

    mock_agent.interrupt = MagicMock(side_effect=_do_interrupt)

    def _slow_run(user_message=None, conversation_history=None, task_id=None):
        ready.set()
        # Block until interrupt() is called
        interrupted.wait(timeout=10)
        return {"final_response": "interrupted"}

    mock_agent.run_conversation.side_effect = _slow_run
    mock_agent.session_prompt_tokens = 0
    mock_agent.session_completion_tokens = 0
    mock_agent.session_total_tokens = 0

    return mock_agent, ready, interrupted


def test_voice_runtime_instructions_include_fresh_local_clock():
    fixed_now = datetime(
        2026, 7, 26, 16, 4, 21,
        tzinfo=timezone(timedelta(hours=9), name="JST"),
    )

    rendered = api_server_module._build_voice_runtime_instructions(
        "Answer briefly.",
        now=fixed_now,
    )

    assert rendered.startswith("Answer briefly.\n\n")
    assert "2026-07-26T16:04:21+09:00" in rendered
    assert "JST" in rendered
    assert "not the session or conversation start time" in rendered


@pytest.fixture
def adapter():
    return _make_adapter()


@pytest.fixture
def auth_adapter():
    return _make_adapter(api_key="sk-secret")


# ---------------------------------------------------------------------------
# POST /v1/runs — start a run
# ---------------------------------------------------------------------------


class TestStartRun:
    @pytest.mark.asyncio
    async def test_start_returns_202(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 10
                mock_agent.session_completion_tokens = 5
                mock_agent.session_total_tokens = 15
                mock_create.return_value = mock_agent

                resp = await cli.post("/v1/runs", json={"input": "hello"})
                assert resp.status == 202
                data = await resp.json()
                assert data["status"] == "started"
                assert data["run_id"].startswith("run_")

                status_resp = await cli.get(f"/v1/runs/{data['run_id']}")
                assert status_resp.status == 200
                status = await status_resp.json()
                assert status["run_id"] == data["run_id"]
                assert status["status"] in {"queued", "running", "completed"}
                assert status["object"] == "sinria.run"

    @pytest.mark.asyncio
    async def test_start_attaches_sanitized_browser_receipts_to_agent(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "continue browser workflow",
                        "browser_receipts": [
                            {
                                "receipt_id": "wf-1:3:abc12345",
                                "action_type": "keypress",
                                "verified": True,
                                "readback_label": "Search | Sales Navigator",
                                "url": "https://example.invalid/secret",
                            },
                            {
                                "receipt_id": "bad",
                                "action_type": "click",
                                "verified": False,
                            },
                        ],
                    },
                )
                assert resp.status == 202
                for _ in range(20):
                    if hasattr(mock_agent, "_external_browser_receipts"):
                        break
                    await asyncio.sleep(0.01)

                assert mock_agent._external_browser_receipts == (
                    {
                        "receipt_id": "wf-1:3:abc12345",
                        "action_type": "keypress",
                        "verified": True,
                        "readback_label": "Search | Sales Navigator",
                    },
                )

    @pytest.mark.asyncio
    async def test_start_accepts_legacy_session_key_but_emits_sinria_header(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(auth_adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                resp = await cli.post(
                    "/v1/runs",
                    headers={
                        "Authorization": "Bearer sk-secret",
                        "X-Hermes-Session-Key": "legacy-g2-device",
                    },
                    json={"input": "harmless check"},
                )

        assert resp.status == 202
        assert resp.headers["X-Sinria-Session-Key"] == "legacy-g2-device"
        assert "X-Hermes-Session-Key" not in resp.headers

    @pytest.mark.asyncio
    async def test_start_prefers_canonical_session_key(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(auth_adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                resp = await cli.post(
                    "/v1/runs",
                    headers={
                        "Authorization": "Bearer sk-secret",
                        "X-Sinria-Session-Key": "canonical-device",
                        "X-Hermes-Session-Key": "legacy-device",
                    },
                    json={"input": "harmless check"},
                )

        assert resp.status == 202
        assert resp.headers["X-Sinria-Session-Key"] == "canonical-device"

    @pytest.mark.asyncio
    async def test_start_loads_persisted_history_for_session_id(self, adapter):
        persisted = [
            {"role": "user", "content": "remember this"},
            {"role": "assistant", "content": "remembered"},
        ]
        session_db = MagicMock()
        session_db.get_messages_as_conversation.return_value = persisted
        adapter._session_db = session_db
        app = _create_runs_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "continued"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "continue", "session_id": "g2-session"},
                )
                assert resp.status == 202
                run_id = (await resp.json())["run_id"]
                status = {"status": "started"}
                for _ in range(100):
                    status = await (await cli.get(f"/v1/runs/{run_id}")).json()
                    if status["status"] == "completed":
                        break
                    await asyncio.sleep(0.01)
                assert status["status"] == "completed"

        session_db.get_messages_as_conversation.assert_called_once_with("g2-session")
        assert mock_agent.run_conversation.call_args.kwargs["conversation_history"] == persisted

    @pytest.mark.asyncio
    async def test_start_invalid_json_returns_400(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_start_missing_input_returns_400(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/v1/runs", json={"model": "test"})
            assert resp.status == 400
            data = await resp.json()
            assert "input" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_start_empty_input_returns_400(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/v1/runs", json={"input": ""})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_start_propagates_bounded_max_iterations(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "hello", "max_iterations": 4},
                )
                assert resp.status == 202
                run_id = (await resp.json())["run_id"]
                status = {"status": "started"}
                for _ in range(100):
                    status = await (await cli.get(f"/v1/runs/{run_id}")).json()
                    if status["status"] == "completed":
                        break
                    await asyncio.sleep(0.01)

        assert status["status"] == "completed"
        assert mock_create.call_args.kwargs["max_iterations"] == 4

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [0, 91, 1.5, "4", True])
    async def test_start_rejects_invalid_max_iterations(self, adapter, value):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs",
                json={"input": "hello", "max_iterations": value},
            )
        assert resp.status == 400
        assert adapter._run_streams == {}
        assert adapter._run_statuses == {}

    @pytest.mark.asyncio
    async def test_start_invalid_history_does_not_allocate_run(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs",
                json={"input": "hello", "conversation_history": {"role": "user"}},
            )
        assert resp.status == 400
        assert adapter._run_streams == {}
        assert adapter._run_statuses == {}

    @pytest.mark.asyncio
    async def test_completed_run_streams_do_not_consume_concurrency(self, auth_adapter):
        for index in range(auth_adapter._MAX_CONCURRENT_RUNS):
            run_id = f"run_completed_{index}"
            auth_adapter._run_streams[run_id] = asyncio.Queue()
            auth_adapter._run_statuses[run_id] = {"status": "completed"}

        app = _create_runs_app(auth_adapter)
        mock_agent = MagicMock()
        mock_agent.run_conversation.return_value = {"final_response": "done"}
        mock_agent.session_prompt_tokens = 0
        mock_agent.session_completion_tokens = 0
        mock_agent.session_total_tokens = 0
        with patch.object(auth_adapter, "_create_agent", return_value=mock_agent):
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.post(
                    "/v1/runs",
                    headers={"Authorization": "Bearer sk-secret"},
                    json={"input": "next turn"},
                )
        assert resp.status == 202

    @pytest.mark.asyncio
    async def test_active_run_tasks_enforce_concurrency_limit(self, auth_adapter):
        for index in range(auth_adapter._MAX_CONCURRENT_RUNS):
            auth_adapter._active_run_tasks[f"run_active_{index}"] = object()

        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs",
                headers={"Authorization": "Bearer sk-secret"},
                json={"input": "one too many"},
            )
            data = await resp.json()
        assert resp.status == 429
        assert data["error"]["code"] == "rate_limit_exceeded"

    @pytest.mark.asyncio
    async def test_voice_profile_narrows_tools_and_iterations(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(auth_adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "fast"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_create.return_value = mock_agent
                resp = await cli.post(
                    "/v1/runs",
                    headers={"Authorization": "Bearer sk-secret"},
                    json={
                        "input": "find relevant context",
                        "instructions": "Answer briefly.",
                        "latency_profile": "voice",
                        "max_iterations": 6,
                    },
                )
                assert resp.status == 202
                await asyncio.sleep(0.05)

        kwargs = mock_create.call_args.kwargs
        assert kwargs["max_iterations"] == 2
        assert kwargs["enabled_toolsets"] == ["session_search"]
        assert kwargs["skip_memory"] is True
        assert kwargs["ephemeral_system_prompt"].startswith("Answer briefly.\n\n")
        assert "Current local date and time at run start:" in kwargs["ephemeral_system_prompt"]
        assert "not the session or conversation start time" in kwargs["ephemeral_system_prompt"]

    @pytest.mark.asyncio
    async def test_required_preflight_approval_denial_skips_agent_execution(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        mock_agent = MagicMock()
        mock_agent.run_conversation.return_value = {"final_response": "must not run"}
        mock_agent.session_prompt_tokens = 0
        mock_agent.session_completion_tokens = 0
        mock_agent.session_total_tokens = 0

        with (
            patch.object(auth_adapter, "_create_agent", return_value=mock_agent),
            patch(
                "tools.approval.request_gateway_approval",
                return_value={"approved": False, "message": "BLOCKED: Approval denied by user."},
            ),
        ):
            async with TestClient(TestServer(app)) as cli:
                response = await cli.post(
                    "/v1/runs",
                    headers={"Authorization": "Bearer sk-secret"},
                    json={"input": "delete the file", "require_approval": True},
                )
                assert response.status == 202
                run_id = (await response.json())["run_id"]
                status = {}
                for _ in range(20):
                    status_response = await cli.get(
                        f"/v1/runs/{run_id}",
                        headers={"Authorization": "Bearer sk-secret"},
                    )
                    status = await status_response.json()
                    if status.get("status") == "completed":
                        break
                    await asyncio.sleep(0.01)

        assert status["status"] == "completed"
        assert "not executed" in status["output"].lower()
        mock_agent.run_conversation.assert_not_called()

    @pytest.mark.asyncio
    async def test_voice_profile_reports_delay_then_completes_without_interrupting(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        ready = threading.Event()
        release = threading.Event()
        slow_agent = MagicMock()

        def _slow_run(user_message=None, conversation_history=None, task_id=None):
            ready.set()
            release.wait(timeout=2)
            return {"final_response": "completed after soft budget"}

        slow_agent.run_conversation.side_effect = _slow_run
        slow_agent.session_prompt_tokens = 0
        slow_agent.session_completion_tokens = 0
        slow_agent.session_total_tokens = 0

        with (
            patch.object(auth_adapter, "_create_agent", return_value=slow_agent),
            patch.object(auth_adapter, "_VOICE_RUN_SOFT_TIMEOUT_SECONDS", 0.05),
            patch.object(auth_adapter, "_VOICE_RUN_HARD_TIMEOUT_SECONDS", 1.0),
        ):
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.post(
                    "/v1/runs",
                    headers={"Authorization": "Bearer sk-secret"},
                    json={"input": "slow", "latency_profile": "voice"},
                )
                assert resp.status == 202
                run_id = (await resp.json())["run_id"]
                assert await asyncio.to_thread(ready.wait, 1.0)

                deadline = asyncio.get_running_loop().time() + 1.0
                while True:
                    status_response = await cli.get(
                        f"/v1/runs/{run_id}",
                        headers={"Authorization": "Bearer sk-secret"},
                    )
                    delayed_status = await status_response.json()
                    if delayed_status.get("last_event") == "run.delayed":
                        break
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.01)

                assert delayed_status["status"] == "running"
                slow_agent.interrupt.assert_not_called()

                release.set()
                deadline = asyncio.get_running_loop().time() + 1.0
                while True:
                    status_response = await cli.get(
                        f"/v1/runs/{run_id}",
                        headers={"Authorization": "Bearer sk-secret"},
                    )
                    status = await status_response.json()
                    if status.get("status") == "completed":
                        break
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.01)

                assert status["output"] == "completed after soft budget"
                slow_agent.interrupt.assert_not_called()

    @pytest.mark.asyncio
    async def test_voice_profile_interrupts_only_at_hard_wall_clock_budget(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        slow_agent, ready, interrupted = _make_slow_agent()
        with (
            patch.object(auth_adapter, "_create_agent", return_value=slow_agent),
            patch.object(auth_adapter, "_VOICE_RUN_SOFT_TIMEOUT_SECONDS", 0.01),
            patch.object(auth_adapter, "_VOICE_RUN_HARD_TIMEOUT_SECONDS", 0.05),
        ):
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.post(
                    "/v1/runs",
                    headers={"Authorization": "Bearer sk-secret"},
                    json={"input": "slow", "latency_profile": "voice"},
                )
                assert resp.status == 202
                run_id = (await resp.json())["run_id"]
                assert await asyncio.to_thread(ready.wait, 1.0)

                deadline = asyncio.get_running_loop().time() + 1.0
                while True:
                    status_response = await cli.get(
                        f"/v1/runs/{run_id}",
                        headers={"Authorization": "Bearer sk-secret"},
                    )
                    status = await status_response.json()
                    if status.get("status") == "failed":
                        break
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.01)

                assert "hard wall-clock budget" in status["error"]
                assert await asyncio.to_thread(interrupted.wait, 1.0)
                slow_agent.interrupt.assert_called_once()

    @pytest.mark.asyncio
    async def test_voice_timeout_keeps_uncooperative_executor_tracked_until_exit(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        ready = threading.Event()
        release = threading.Event()
        stubborn_agent = MagicMock()
        stubborn_agent.session_prompt_tokens = 0
        stubborn_agent.session_completion_tokens = 0
        stubborn_agent.session_total_tokens = 0

        def _stubborn_run(user_message=None, conversation_history=None, task_id=None):
            ready.set()
            release.wait(timeout=5)
            return {"final_response": "late exit"}

        stubborn_agent.run_conversation.side_effect = _stubborn_run
        try:
            with (
                patch.object(auth_adapter, "_create_agent", return_value=stubborn_agent),
                patch.object(auth_adapter, "_VOICE_RUN_SOFT_TIMEOUT_SECONDS", 0.005),
                patch.object(auth_adapter, "_VOICE_RUN_HARD_TIMEOUT_SECONDS", 0.01),
            ):
                async with TestClient(TestServer(app)) as cli:
                    resp = await cli.post(
                        "/v1/runs",
                        headers={"Authorization": "Bearer sk-secret"},
                        json={"input": "stubborn voice turn", "latency_profile": "voice"},
                    )
                    assert resp.status == 202
                    run_id = (await resp.json())["run_id"]
                    assert await asyncio.to_thread(ready.wait, 1)

                    status = {}
                    for _ in range(20):
                        status_resp = await cli.get(
                            f"/v1/runs/{run_id}",
                            headers={"Authorization": "Bearer sk-secret"},
                        )
                        status = await status_resp.json()
                        if status["status"] == "cancelling":
                            break
                        await asyncio.sleep(0.01)

                    assert status["status"] == "cancelling"
                    assert run_id in auth_adapter._active_run_tasks
                    assert run_id in auth_adapter._active_run_agents
                    assert not auth_adapter._active_run_tasks[run_id].done()

                    release.set()
                    for _ in range(50):
                        status_resp = await cli.get(
                            f"/v1/runs/{run_id}",
                            headers={"Authorization": "Bearer sk-secret"},
                        )
                        status = await status_resp.json()
                        if status["status"] == "failed":
                            break
                        await asyncio.sleep(0.01)

                    assert status["status"] == "failed"
                    assert run_id not in auth_adapter._active_run_tasks
                    assert run_id not in auth_adapter._active_run_agents
        finally:
            release.set()

    @pytest.mark.asyncio
    async def test_unknown_latency_profile_is_rejected(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs",
                headers={"Authorization": "Bearer sk-secret"},
                json={"input": "hello", "latency_profile": "turbo"},
            )
        assert resp.status == 400
        assert auth_adapter._run_streams == {}
        assert auth_adapter._run_statuses == {}

    @pytest.mark.asyncio
    async def test_start_requires_auth(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/v1/runs", json={"input": "hello"})
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_start_with_valid_auth(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(auth_adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "ok"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "hello"},
                    headers={"Authorization": "Bearer sk-secret"},
                )
                assert resp.status == 202


# ---------------------------------------------------------------------------
# GET /v1/runs/{run_id} — poll run status
# ---------------------------------------------------------------------------


class TestRunStatus:
    @pytest.mark.asyncio
    async def test_status_completed_run_includes_output_and_usage(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 4
                mock_agent.session_completion_tokens = 2
                mock_agent.session_total_tokens = 6
                mock_create.return_value = mock_agent

                resp = await cli.post("/v1/runs", json={"input": "hello"})
                data = await resp.json()
                run_id = data["run_id"]

                for _ in range(20):
                    status_resp = await cli.get(f"/v1/runs/{run_id}")
                    assert status_resp.status == 200
                    status = await status_resp.json()
                    if status["status"] == "completed":
                        break
                    await asyncio.sleep(0.05)

                assert status["status"] == "completed"
                assert status["output"] == "done"
                assert status["usage"]["total_tokens"] == 6
                assert status["last_event"] == "run.completed"

    @pytest.mark.asyncio
    async def test_status_reflects_explicit_session_id(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "hello", "session_id": "space-session"},
                )
                data = await resp.json()
                run_id = data["run_id"]

                for _ in range(20):
                    status_resp = await cli.get(f"/v1/runs/{run_id}")
                    status = await status_resp.json()
                    if status["status"] == "completed":
                        break
                    await asyncio.sleep(0.05)

                mock_agent.run_conversation.assert_called_once()
                assert mock_agent.run_conversation.call_args.kwargs["task_id"] == "space-session"
                assert status["session_id"] == "space-session"

    @pytest.mark.asyncio
    async def test_status_not_found_returns_404(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/runs/run_nonexistent")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_status_requires_auth(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/runs/run_any")
        assert resp.status == 401


# ---------------------------------------------------------------------------
# GET /v1/runs/{run_id}/events — SSE event stream
# ---------------------------------------------------------------------------


class TestRunEvents:
    @pytest.mark.asyncio
    async def test_events_stream_returns_completed(self, adapter):
        """Events stream should receive run.completed when agent finishes."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "Hello!"}
                mock_agent.session_prompt_tokens = 10
                mock_agent.session_completion_tokens = 5
                mock_agent.session_total_tokens = 15
                mock_create.return_value = mock_agent

                # Start run
                resp = await cli.post("/v1/runs", json={"input": "hello"})
                assert resp.status == 202
                data = await resp.json()
                run_id = data["run_id"]

                # Subscribe to events
                events_resp = await cli.get(f"/v1/runs/{run_id}/events")
                assert events_resp.status == 200
                body = await events_resp.text()

                # Should contain run.completed
                assert "run.completed" in body
                assert "Hello!" in body



    @pytest.mark.asyncio
    async def test_approval_response_without_pending_returns_409(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                resp = await cli.post("/v1/runs", json={"input": "hello"})
                data = await resp.json()
                run_id = data["run_id"]

                approval_resp = await cli.post(
                    f"/v1/runs/{run_id}/approval",
                    json={"choice": "once"},
                )
                assert approval_resp.status == 409
                approval_data = await approval_resp.json()
                assert approval_data["error"]["code"] in {
                    "approval_not_active",
                    "approval_not_pending",
                }

    @pytest.mark.asyncio
    async def test_approval_string_false_does_not_resolve_all(self, adapter):
        """Quoted false must not fan out approval resolution across the queue."""
        app = _create_runs_app(adapter)
        run_id = "run_bool_parse"
        adapter._run_statuses[run_id] = {"run_id": run_id, "status": "running"}
        adapter._run_approval_sessions[run_id] = "session-123"

        async with TestClient(TestServer(app)) as cli:
            with patch("tools.approval.resolve_gateway_approval", return_value=1) as mock_resolve:
                approval_resp = await cli.post(
                    f"/v1/runs/{run_id}/approval",
                    json={"choice": "once", "all": "false"},
                )

        assert approval_resp.status == 200
        mock_resolve.assert_called_once_with(
            "session-123",
            "once",
            resolve_all=False,
        )

    @pytest.mark.asyncio
    async def test_events_not_found_returns_404(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/runs/run_nonexistent/events")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_events_requires_auth(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/runs/run_any/events")
        assert resp.status == 401


# ---------------------------------------------------------------------------
# POST /v1/runs/{run_id}/stop — interrupt a running agent
# ---------------------------------------------------------------------------


class TestStopRun:
    @pytest.mark.asyncio
    async def test_real_approval_wait_is_journaled_resolved_and_completes(self, adapter):
        """Exercise the actual tool approval queue, not a mocked resolver."""
        import asyncio

        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()

                def _run_with_approval(**_kwargs):
                    from tools.approval import request_gateway_approval

                    decision = request_gateway_approval(
                        "delete local draft",
                        "delete a local-only Workspace draft",
                        pattern_key="workspace:delete-local-draft",
                    )
                    return {
                        "final_response": f"approval:{decision.get('approved', False)}"
                    }

                mock_agent.run_conversation.side_effect = _run_with_approval
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                start = await cli.post(
                    "/v1/runs",
                    json={"input": "approval please"},
                    headers={"Idempotency-Key": "approval-journal-1"},
                )
                assert start.status == 202
                run_id = (await start.json())["run_id"]

                status_data = None
                for _ in range(100):
                    status_resp = await cli.get(f"/v1/runs/{run_id}")
                    status_data = await status_resp.json()
                    if status_data["status"] == "waiting_for_approval":
                        break
                    await asyncio.sleep(0.02)
                assert status_data and status_data["status"] == "waiting_for_approval"
                persisted = None
                for _ in range(100):
                    persisted = adapter._get_journal().get_run(run_id)
                    if persisted and persisted["status"] == "waiting_for_approval":
                        break
                    await asyncio.sleep(0.02)
                assert persisted and persisted["status"] == "waiting_for_approval"

                approval = await cli.post(
                    f"/v1/runs/{run_id}/approval",
                    json={"choice": "once"},
                )
                assert approval.status == 200
                assert (await approval.json())["status"] == "running"

                completed = status_data
                for _ in range(100):
                    completed_resp = await cli.get(f"/v1/runs/{run_id}")
                    completed = await completed_resp.json()
                    if completed["status"] == "completed":
                        break
                    await asyncio.sleep(0.02)
                assert completed["status"] == "completed"
                assert completed["output"] == "approval:True"
                persisted = adapter._get_journal().get_run(run_id)
                assert persisted and persisted["status"] == "completed"

    @pytest.mark.asyncio
    async def test_stop_running_agent(self, adapter):
        """Stop should interrupt the agent and cancel the task."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent, agent_ready, _ = _make_slow_agent()
                mock_create.return_value = mock_agent

                # Start run
                resp = await cli.post("/v1/runs", json={"input": "hello"})
                assert resp.status == 202
                data = await resp.json()
                run_id = data["run_id"]

                # Wait for agent to start running in the thread
                agent_ready.wait(timeout=3.0)
                await asyncio.sleep(0.1)

                # Verify agent ref is stored
                assert run_id in adapter._active_run_agents

                # Stop the run
                stop_resp = await cli.post(f"/v1/runs/{run_id}/stop")
                assert stop_resp.status == 200
                stop_data = await stop_resp.json()
                assert stop_data["run_id"] == run_id
                assert stop_data["status"] == "stopping"

                # Agent interrupt should have been called
                mock_agent.interrupt.assert_called_once_with("Stop requested via API")

                status_resp = await cli.get(f"/v1/runs/{run_id}")
                assert status_resp.status == 200
                status_data = await status_resp.json()
                assert status_data["status"] in {"stopping", "cancelled"}

                # Refs should be cleaned up
                await asyncio.sleep(0.5)
                assert run_id not in adapter._active_run_agents
                assert run_id not in adapter._active_run_tasks

    @pytest.mark.asyncio
    async def test_stop_nonexistent_run_returns_404(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/v1/runs/run_nonexistent/stop")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_stop_requires_auth(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/v1/runs/run_any/stop")
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_stop_already_completed_run_returns_404(self, adapter):
        """Stopping a run that already finished should return 404 (refs cleaned up)."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                # Start and wait for completion
                resp = await cli.post("/v1/runs", json={"input": "hello"})
                assert resp.status == 202
                data = await resp.json()
                run_id = data["run_id"]

                await asyncio.sleep(0.3)

                # Run should be done, refs cleaned up
                assert run_id not in adapter._active_run_agents

                # Stop should return 404
                stop_resp = await cli.post(f"/v1/runs/{run_id}/stop")
                assert stop_resp.status == 404

    @pytest.mark.asyncio
    async def test_stop_interrupt_exception_does_not_crash(self, adapter):
        """If agent.interrupt() raises, stop should still succeed."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent, agent_ready, _ = _make_slow_agent()
                # Override the interrupt side_effect to raise
                mock_agent.interrupt = MagicMock(side_effect=RuntimeError("interrupt failed"))
                mock_create.return_value = mock_agent

                resp = await cli.post("/v1/runs", json={"input": "hello"})
                assert resp.status == 202
                data = await resp.json()
                run_id = data["run_id"]

                agent_ready.wait(timeout=3.0)
                await asyncio.sleep(0.1)

                stop_resp = await cli.post(f"/v1/runs/{run_id}/stop")
                assert stop_resp.status == 200
                stop_data = await stop_resp.json()
                assert stop_data["status"] == "stopping"

    @pytest.mark.asyncio
    async def test_stop_sends_sentinel_to_events_stream(self, adapter):
        """After stop, the events stream should close."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent, agent_ready, _ = _make_slow_agent()
                mock_create.return_value = mock_agent

                # Start run
                resp = await cli.post("/v1/runs", json={"input": "hello"})
                assert resp.status == 202
                data = await resp.json()
                run_id = data["run_id"]

                agent_ready.wait(timeout=3.0)
                await asyncio.sleep(0.1)

                # Subscribe to events in background
                events_task = asyncio.ensure_future(
                    cli.get(f"/v1/runs/{run_id}/events")
                )

                await asyncio.sleep(0.1)

                # Stop the run
                stop_resp = await cli.post(f"/v1/runs/{run_id}/stop")
                assert stop_resp.status == 200

                # Events stream should close
                events_resp = await asyncio.wait_for(events_task, timeout=5.0)
                assert events_resp.status == 200
                body = await events_resp.text()
                # Stream should have received run.failed and closed
                assert "run.failed" in body or "stream closed" in body
