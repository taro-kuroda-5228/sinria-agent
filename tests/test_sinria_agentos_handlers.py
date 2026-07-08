"""Tests for the local Sinria Agent OS handler registry (Tasks 10, 11, 16, 17)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sinria_agentos_handlers as handlers  # noqa: E402
from sinria_agentos_handlers import (  # noqa: E402
    LocalExecutionIdentity,
    dispatch_agentos_task,
    get_handler,
    registered_handler_keys,
    set_sales_outreach_runner,
)

IDENTITY = LocalExecutionIdentity("medical_horizon", "taro", "taro-local")


@pytest.fixture(autouse=True)
def _isolate_sales_runner():
    """Pin the no-runner assumption these tests are written against.

    The sales daemon registers its real outreach runner at import time; any
    test that loads the daemon module in the same worker process would leak
    that registration here and the handler would try to open the local Sales
    DB. Save/None/restore keeps each test hermetic regardless of collection
    order.
    """
    previous = handlers._SALES_RUNNER
    set_sales_outreach_runner(None)
    yield
    set_sales_outreach_runner(previous)


def test_dispatch_returns_recoverable_failure_for_unknown_handler():
    result = dispatch_agentos_task(
        {"agentOsId": "sales_agent_os", "taskKind": "unknown"},
        IDENTITY,
    )
    assert result["status"] == "failed_recoverable"
    assert result["externalActionPerformed"] is False
    assert result["rawLocalContextStored"] is False


def test_sales_outreach_handler_registered():
    assert get_handler("sales_agent_os", "sales_outreach_plan") is not None


def test_sales_outreach_plan_is_draft_safe_without_runner():
    result = dispatch_agentos_task(
        {
            "agentOsId": "sales_agent_os",
            "taskKind": "sales_outreach_plan",
            "payload": {"instruction": "病院を10件リサーチ", "maxTotal": 10},
        },
        IDENTITY,
    )
    assert result["status"] == "waiting_review"
    assert result["externalActionPerformed"] is False
    assert result["rawLocalContextStored"] is False


def test_sales_outreach_plan_uses_injected_runner():
    captured = {}

    def fake_runner(payload, identity):
        captured["payload"] = payload
        captured["identity"] = identity
        return {"answer_summary": "候補10件を探索し7件の下書きを作成", "draft_ids": ["d1", "d2"]}

    set_sales_outreach_runner(fake_runner)
    try:
        result = dispatch_agentos_task(
            {
                "agentOsId": "sales_agent_os",
                "taskKind": "sales_outreach_plan",
                "payload": {"instruction": "x"},
            },
            IDENTITY,
        )
    finally:
        set_sales_outreach_runner(None)

    assert result["status"] == "waiting_review"
    assert result["externalActionPerformed"] is False
    assert any(r["kind"] == "draft" for r in result["resultRefs"])
    assert captured["payload"]["instruction"] == "x"
    assert captured["identity"].member_id == "taro"


def test_missing_instruction_is_recoverable():
    result = dispatch_agentos_task(
        {"agentOsId": "sales_agent_os", "taskKind": "sales_outreach_plan", "payload": {}},
        IDENTITY,
    )
    assert result["status"] == "failed_recoverable"
    assert result["externalActionPerformed"] is False


def test_service_triage_handler_is_registered_and_no_action():
    handler = get_handler("service_agent_os", "service_triage")
    assert handler is not None
    result = handler({"payload": {"summary": "safe summary"}}, IDENTITY)
    assert result["externalActionPerformed"] is False
    assert result["rawLocalContextStored"] is False


def test_medevidence_and_consent_require_physician_authority():
    for key in (("medevidence", "evidence_research"), ("consent_agent", "consent_draft_review")):
        handler = get_handler(*key)
        assert handler is not None, f"missing handler {key}"
        result = handler({"payload": {}}, IDENTITY)
        assert result["requiredAuthority"] == "physician"
        assert result["humanApprovalRequired"] is True
        assert result["externalActionPerformed"] is False


def test_registered_keys_cover_multiple_agent_os():
    keys = set(registered_handler_keys())
    assert ("sales_agent_os", "sales_outreach_plan") in keys
    assert ("service_agent_os", "service_triage") in keys
    assert ("medevidence", "evidence_research") in keys
    assert ("consent_agent", "consent_draft_review") in keys


def test_dispatch_accepts_snake_case_task_keys():
    result = dispatch_agentos_task(
        {"agent_os_id": "service_agent_os", "task_kind": "service_triage", "payload": {}},
        IDENTITY,
    )
    assert result["externalActionPerformed"] is False
    assert result["status"] == "waiting_review"
