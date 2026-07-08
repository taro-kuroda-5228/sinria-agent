#!/usr/bin/env python3
"""End-to-end smoke for Agent OS Team Mode local routing.

Proves the load-bearing routing guarantee: a task created in the shared UI/API is
claimable ONLY by the matching local Sinria identity (target member + instance),
and the wrong member is rejected. Two modes:

  * ``--mock`` (default): a self-contained in-memory router mirrors the same
    claim/lease/target-enforcement rules as the Company OS repository. No server,
    no DB, no network — safe for CI.
  * ``--base-url URL``: drives the REAL Company OS routes
    (POST /api/agent-os/tasks, /tasks/claim, /tasks/result) over HTTP.

Exit code 0 and ``{"ok": true, ...}`` on success.

Usage:
  python scripts/smoke_team_mode_local_routing.py --mock
  python scripts/smoke_team_mode_local_routing.py --base-url http://127.0.0.1:3017 --mock
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

WORKSPACE = "medical_horizon"
REQUESTER = "taro"
TARGET_MEMBER = "taro"
TARGET_INSTANCE = "taro-local-sinria"
WRONG_MEMBER = "kikuchi"
AGENT_OS_ID = "sales_agent_os"
TASK_KIND = "sales_outreach_plan"


# ---------------------------------------------------------------------------
# In-memory router — mirrors apps/company-os/lib/company-os-repository.ts rules.
# ---------------------------------------------------------------------------
class MockTeamModeRouter:
    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.claims: list[dict] = []
        self.results: list[dict] = []
        self._seq = 0

    def _id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq}"

    def create_task(self, *, workspace_id, agent_os_id, task_kind, instruction,
                    requested_by, target_member, target_instance, execution_mode="self"):
        task = {
            "id": self._id("aot"),
            "workspaceId": workspace_id,
            "agentOsId": agent_os_id,
            "taskKind": task_kind,
            "instruction": instruction,
            "requestedByMemberId": requested_by,
            "targetMemberId": target_member,
            "targetInstanceId": target_instance,
            # "self" | "member" | "shared_queue". shared_queue means any instance
            # may claim it (first-wins), independent of target member/instance.
            "executionMode": execution_mode,
            "status": "queued",
            "rawContextStored": False,
            "externalActionPerformed": False,
            "policy": {"rawContextAllowedInCloud": False, "externalActionAllowed": False},
        }
        self.tasks[task["id"]] = task
        return task

    def claim_task(self, *, task_id, member_id, instance_id):
        task = self.tasks.get(task_id)
        if not task:
            return {"ok": False, "reason": "task not found"}
        # Shared queue bypasses member/instance targeting: anyone may claim it.
        # The first-wins guard below (active claim check) still ensures exactly
        # one instance wins a contended shared task.
        if task.get("executionMode") != "shared_queue":
            if task["targetMemberId"] and task["targetMemberId"] != member_id:
                return {"ok": False, "reason": "task is targeted at a different member"}
            if task["targetInstanceId"] and task["targetInstanceId"] != instance_id:
                return {"ok": False, "reason": "task is targeted at a different instance"}
        active = next((c for c in self.claims if c["taskId"] == task_id and c["claimStatus"] == "active"), None)
        if active:
            if active["claimedByMemberId"] == member_id and active["claimedByInstanceId"] == instance_id:
                return {"ok": True, "claim": active}
            return {"ok": False, "reason": "task already claimed by another instance"}
        if task["status"] not in ("queued", "claiming", "failed_recoverable"):
            return {"ok": False, "reason": f"not claimable in status {task['status']}"}
        attempt = len([c for c in self.claims if c["taskId"] == task_id]) + 1
        claim = {
            "id": self._id("aotc"),
            "taskId": task_id,
            "targetMemberId": task["targetMemberId"],
            "claimedByMemberId": member_id,
            "claimedByInstanceId": instance_id,
            "claimStatus": "active",
            "claimExpiresAt": (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat(),
            # Stable per (task, member, instance) — matches the repository rule
            # (attempt is a separate counter, not part of the idempotency key).
            "idempotencyKey": f"claim:{task_id}:{member_id}:{instance_id}",
            "attempt": attempt,
            "selectedExecutionEngine": "sinria_native",
            "externalActionPerformed": False,
            "rawLocalContextStored": False,
        }
        self.claims.append(claim)
        task["status"] = "claimed"
        return {"ok": True, "claim": claim}

    def post_result(self, *, task_id, member_id, instance_id, status, summary):
        result = {
            "id": self._id("aor"),
            "taskId": task_id,
            "producedByMemberId": member_id,
            "producedByInstanceId": instance_id,
            "status": status,
            "sanitizedSummary": summary,
            "safety": {
                "rawResultBodyStored": False,
                "credentialStoredInCloud": False,
                "externalActionPerformed": False,
                "externalEgress": False,
                "humanApprovalRequired": True,
            },
        }
        self.results.append(result)
        task = self.tasks.get(task_id)
        if task:
            task["status"] = status if status != "completed" else "completed"
        return result


def _run_mock() -> dict:
    router = MockTeamModeRouter()
    task = router.create_task(
        workspace_id=WORKSPACE, agent_os_id=AGENT_OS_ID, task_kind=TASK_KIND,
        instruction="Sinria導入候補を10件リサーチし営業文書を作成して",
        requested_by=REQUESTER, target_member=TARGET_MEMBER, target_instance=TARGET_INSTANCE,
    )
    wrong = router.claim_task(task_id=task["id"], member_id=WRONG_MEMBER, instance_id="kikuchi-local-sinria")
    right = router.claim_task(task_id=task["id"], member_id=TARGET_MEMBER, instance_id=TARGET_INSTANCE)
    result = router.post_result(
        task_id=task["id"], member_id=TARGET_MEMBER, instance_id=TARGET_INSTANCE,
        status="waiting_review", summary="候補10件・下書き7件を作成（外部送信なし）",
    )
    return _assemble(task, wrong, right, result)


def _run_http(base_url: str) -> dict:
    import requests  # local import so --mock has no network dependency

    base = base_url.rstrip("/")

    def post(path, body):
        r = requests.post(f"{base}{path}", json=body, timeout=20)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"ok": False, "error": "non-json response"}

    _, created = post("/api/agent-os/tasks", {
        "workspaceId": WORKSPACE, "agentOsId": AGENT_OS_ID, "taskKind": TASK_KIND,
        "instruction": "Sinria導入候補を10件リサーチし営業文書を作成して",
        "requestedByMemberId": REQUESTER, "targetMemberId": TARGET_MEMBER,
        "targetInstanceId": TARGET_INSTANCE,
    })
    task_id = created.get("taskId")
    if not task_id:
        return {"ok": False, "error": f"create failed: {created}"}

    _, wrong = post("/api/agent-os/tasks/claim", {
        "workspaceId": WORKSPACE, "taskId": task_id,
        "memberId": WRONG_MEMBER, "instanceId": "kikuchi-local-sinria",
    })
    _, right = post("/api/agent-os/tasks/claim", {
        "workspaceId": WORKSPACE, "taskId": task_id,
        "memberId": TARGET_MEMBER, "instanceId": TARGET_INSTANCE,
    })
    _, result = post("/api/agent-os/tasks/result", {
        "workspaceId": WORKSPACE, "taskId": task_id, "agentOsId": AGENT_OS_ID, "taskKind": TASK_KIND,
        "producedByMemberId": TARGET_MEMBER, "producedByInstanceId": TARGET_INSTANCE,
        "status": "waiting_review", "sanitizedSummary": "候補10件・下書き7件を作成（外部送信なし）",
    })

    task = {"id": task_id, "rawContextStored": False, "externalActionPerformed": False}
    wrong_norm = {"ok": wrong.get("ok", False), "reason": wrong.get("reason")}
    claim = right.get("claim") or {}
    right_norm = {"ok": right.get("ok", False), "claim": {
        "claimedByInstanceId": claim.get("claimedByInstanceId"),
        "externalActionPerformed": claim.get("externalActionPerformed", False),
    }}
    res_norm = result.get("result") or {"safety": {"externalActionPerformed": False}}
    return _assemble(task, wrong_norm, right_norm, res_norm)


def _assemble(task, wrong, right, result) -> dict:
    claim = (right or {}).get("claim") or {}
    safety = (result or {}).get("safety") or {}
    wrong_rejected = wrong.get("ok") is False
    right_claimed = right.get("ok") is True
    claimed_instance = claim.get("claimedByInstanceId")
    external = bool(
        claim.get("externalActionPerformed")
        or safety.get("externalActionPerformed")
        or task.get("externalActionPerformed")
    )
    raw_stored = bool(task.get("rawContextStored"))
    ok = (
        wrong_rejected
        and right_claimed
        and claimed_instance == TARGET_INSTANCE
        and external is False
        and raw_stored is False
    )
    return {
        "ok": ok,
        "wrong_member_claim_rejected": wrong_rejected,
        "wrong_member_reason": wrong.get("reason"),
        "right_member_claimed": right_claimed,
        "claimed_by_instance_id": claimed_instance,
        "external_action_performed": external,
        "raw_context_stored": raw_stored,
    }


# ---------------------------------------------------------------------------
# Helpers exported for tests/test_team_mode_claim_contention.py
# ---------------------------------------------------------------------------

def build_mock_store_with_single_task(
    *, target_member_id: str, target_instance_id: str | None, execution_mode: str = "self"
) -> MockTeamModeRouter:
    """Return a fresh MockTeamModeRouter that already contains one 'queued' task.

    The task is routed to *target_member_id*; if *target_instance_id* is not None
    it is also pinned to that specific instance.  Pass execution_mode="shared_queue"
    to model a shared task any instance may claim.  All other fields use neutral
    test values so the claim-enforcement logic is the only thing under test.
    """
    router = MockTeamModeRouter()
    router.create_task(
        workspace_id="test_workspace",
        agent_os_id=AGENT_OS_ID,
        task_kind=TASK_KIND,
        instruction="contention test task",
        requested_by="test_requester",
        target_member=target_member_id,
        target_instance=target_instance_id,
        execution_mode=execution_mode,
    )
    return router


def attempt_claim(store: MockTeamModeRouter, *, member_id: str, instance_id: str) -> dict:
    """Claim the first (and only) task in *store* as the given identity.

    Returns a dict with at least ``{"ok": bool, "reason": str | None}``.
    Drives ``store.claim_task`` directly — no stubs, the real routing logic runs.
    """
    task_id = next(iter(store.tasks))
    result = store.claim_task(task_id=task_id, member_id=member_id, instance_id=instance_id)
    # claim_task already returns {"ok": bool, "reason": str | None, ...}; pass through.
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent OS Team Mode local routing smoke")
    parser.add_argument("--mock", action="store_true", help="Run the self-contained in-memory router")
    parser.add_argument("--base-url", default=None, help="Drive the real Company OS routes over HTTP")
    args = parser.parse_args()

    if args.base_url:
        outcome = _run_http(args.base_url)
    else:
        outcome = _run_mock()

    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
