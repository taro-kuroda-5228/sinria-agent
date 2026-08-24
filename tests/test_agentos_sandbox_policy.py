"""Sandbox (execution environment) policy enforcement in the local dispatcher.

A routed Agent OS task may carry ``policy.executionEnvironment`` requiring the
claiming node to run inside the Workshop (LXD) sandbox. Healthcare agent OS
ids (medevidence, consent_agent) are sandbox-required even when the policy is
absent — the local plane mirrors the cloud-boundary hard invariant.
"""

import os
import re
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
    register_handler,
)

IDENTITY = LocalExecutionIdentity("medical_horizon", "taro", "taro-local")


@pytest.fixture(autouse=True)
def _isolate_sales_runner():
    """Pin the no-runner assumption (see test_sinria_agentos_handlers.py).

    The sales daemon registers its real outreach runner at import time; a
    daemon-loading test sharing this worker process would otherwise leak the
    registration in and dispatch would try to open the local Sales DB.
    """
    previous = handlers._SALES_RUNNER
    handlers.set_sales_outreach_runner(None)
    yield
    handlers.set_sales_outreach_runner(previous)


def _task(agent_os_id="sales_agent_os", task_kind="sales_outreach_plan", policy=None):
    return {
        "agentOsId": agent_os_id,
        "taskKind": task_kind,
        "instruction": "do the thing",
        "payload": {"instruction": "do the thing"},
        "policy": policy or {},
    }


@pytest.fixture
def probe_handler():
    """Register a throwaway handler that records the env it executed in."""
    seen = {}

    def _handler(task, identity):
        seen["terminal_env"] = os.environ.get("TERMINAL_ENV")
        seen["workshop_name"] = os.environ.get("TERMINAL_WORKSHOP_NAME")
        return {"status": "completed", "sanitizedSummary": "probe ok"}

    register_handler("probe_os", "probe_kind", _handler)
    yield seen
    handlers._HANDLERS.pop(("probe_os", "probe_kind"), None)


def test_unsandboxed_task_dispatches_normally(monkeypatch):
    monkeypatch.setattr(handlers, "_workshop_available", lambda: False)
    result = dispatch_agentos_task(_task(), IDENTITY)
    assert result["status"] == "waiting_review"


def test_workshop_required_but_unavailable_fails_recoverable(monkeypatch):
    monkeypatch.setattr(handlers, "_workshop_available", lambda: False)
    task = _task(
        policy={
            "executionEnvironment": {
                "sandbox": "workshop",
                "unsandboxedFallbackAllowed": False,
            }
        }
    )
    result = dispatch_agentos_task(task, IDENTITY)
    assert result["status"] == "failed_recoverable"
    assert "workshop" in result["sanitizedSummary"].lower()
    assert result["externalActionPerformed"] is False


def test_workshop_unavailable_with_fallback_runs_handler(monkeypatch):
    monkeypatch.setattr(handlers, "_workshop_available", lambda: False)
    task = _task(
        policy={
            "executionEnvironment": {
                "sandbox": "workshop",
                "unsandboxedFallbackAllowed": True,
            }
        }
    )
    result = dispatch_agentos_task(task, IDENTITY)
    assert result["status"] == "waiting_review"


def test_healthcare_task_requires_workshop_even_without_policy(monkeypatch):
    """Old envelopes without executionEnvironment still get the hard invariant."""
    monkeypatch.setattr(handlers, "_workshop_available", lambda: False)
    result = dispatch_agentos_task(
        _task("medevidence", "evidence_research"), IDENTITY
    )
    assert result["status"] == "failed_recoverable"
    assert "workshop" in result["sanitizedSummary"].lower()


def test_healthcare_task_cannot_opt_out_of_sandbox(monkeypatch):
    monkeypatch.setattr(handlers, "_workshop_available", lambda: False)
    task = _task(
        "consent_agent",
        "consent_draft_review",
        policy={
            "executionEnvironment": {
                "sandbox": "none",
                "unsandboxedFallbackAllowed": True,
            }
        },
    )
    result = dispatch_agentos_task(task, IDENTITY)
    assert result["status"] == "failed_recoverable"


def test_workshop_execution_sets_and_restores_terminal_env(monkeypatch, probe_handler):
    monkeypatch.setattr(handlers, "_workshop_available", lambda: True)
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.delenv("TERMINAL_WORKSHOP_NAME", raising=False)

    task = _task(
        "probe_os",
        "probe_kind",
        policy={
            "executionEnvironment": {
                "sandbox": "workshop",
                "workshopName": "hospital-1",
                "unsandboxedFallbackAllowed": False,
            }
        },
    )
    result = dispatch_agentos_task(task, IDENTITY)

    assert result["status"] == "completed"
    assert probe_handler["terminal_env"] == "workshop"
    assert probe_handler["workshop_name"] == "hospital-1"
    # Restored after the handler returns.
    assert os.environ.get("TERMINAL_ENV") == "local"
    assert os.environ.get("TERMINAL_WORKSHOP_NAME") is None


def test_sandbox_none_does_not_touch_terminal_env(monkeypatch, probe_handler):
    monkeypatch.setattr(handlers, "_workshop_available", lambda: True)
    monkeypatch.setenv("TERMINAL_ENV", "local")

    result = dispatch_agentos_task(_task("probe_os", "probe_kind"), IDENTITY)

    assert result["status"] == "completed"
    assert probe_handler["terminal_env"] == "local"


def test_workshop_required_but_no_name_resolvable_fails_recoverable(monkeypatch):
    """Fail closed BEFORE the handler runs when no workshop name can be resolved.

    Otherwise the handler would start, and only its first terminal call would
    error out with a confusing TERMINAL_WORKSHOP_NAME message mid-task.
    """
    monkeypatch.setattr(handlers, "_workshop_available", lambda: True)
    monkeypatch.delenv("SINRIA_WORKSHOP_NAME", raising=False)
    monkeypatch.delenv("TERMINAL_WORKSHOP_NAME", raising=False)
    task = _task(
        policy={
            "executionEnvironment": {
                "sandbox": "workshop",
                "unsandboxedFallbackAllowed": False,
            }
        }
    )
    result = dispatch_agentos_task(task, IDENTITY)
    assert result["status"] == "failed_recoverable"
    assert "workshop name" in result["sanitizedSummary"].lower()


def test_workshop_name_resolves_from_node_env(monkeypatch, probe_handler):
    monkeypatch.setattr(handlers, "_workshop_available", lambda: True)
    monkeypatch.setenv("SINRIA_WORKSHOP_NAME", "node-default")
    monkeypatch.delenv("TERMINAL_WORKSHOP_NAME", raising=False)

    task = _task(
        "probe_os",
        "probe_kind",
        policy={
            "executionEnvironment": {
                "sandbox": "workshop",
                "unsandboxedFallbackAllowed": False,
            }
        },
    )
    result = dispatch_agentos_task(task, IDENTITY)

    assert result["status"] == "completed"
    assert probe_handler["workshop_name"] == "node-default"


def test_sandbox_required_ids_match_cloud_boundary():
    """Drift guard: the Python tuple must stay in sync with cloud-boundary.mjs.

    The hard invariant lives in two languages (cloud plane + local plane);
    this test fails the build when one side adds/removes a healthcare id.
    """
    boundary_path = ROOT / "apps" / "company-os" / "lib" / "cloud-boundary.mjs"
    if not boundary_path.exists():
        pytest.skip("Company OS cloud boundary overlay is not included in this distribution")
    mjs = boundary_path.read_text(
        encoding="utf-8"
    )
    match = re.search(r"SANDBOX_REQUIRED_AGENT_OS_IDS\s*=\s*\[([^\]]*)\]", mjs)
    assert match, "SANDBOX_REQUIRED_AGENT_OS_IDS not found in cloud-boundary.mjs"
    js_ids = sorted(re.findall(r'"([^"]+)"', match.group(1)))
    assert js_ids == sorted(handlers.SANDBOX_REQUIRED_AGENT_OS_IDS)
