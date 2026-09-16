"""HTTP cloud-event adapters for Sinria Hybrid Agent Bridge.

Production Agent OS execution uses the canonical Company OS HTTP API.  The
Supabase/PostgREST adapter remains for explicit legacy compatibility only.
Both adapters keep credentials in headers/env and never in cloud task payloads
or object reprs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import urlencode

import requests

from sinria_hybrid_bridge import BridgeTaskEnvelope, BridgeTaskStatus
from sinria_hybrid_bridge_transports import bridge_task_from_postgrest_row


def _normalize_base_url(url: str) -> str:
    return url.rstrip("/")


@dataclass(repr=False)
class CompanyOsApiCloudEventStore:
    """Canonical Company OS API adapter for the local Agent OS worker.

    The API owns task, claim, result, and review state.  This adapter deliberately
    does not address Supabase tables directly; only sanitized metadata crosses the
    boundary.  GET state is cached for the duration of one worker tick so durable
    approval and attempt calculations use the same snapshot as task selection.
    """

    base_url: str
    auth_value: Any = None
    transport_subject: str | None = None
    workspace_id: str | None = None
    member_id: str | None = None
    instance_id: str | None = None
    session: Any = requests
    timeout: float = 20.0

    def __post_init__(self) -> None:
        self.base_url = _normalize_base_url(self.base_url)
        self._state: dict[str, list[dict[str, Any]]] = {"tasks": [], "claims": [], "results": []}
        self._claim_ids_by_task: dict[tuple[str, str], str] = {}

    def __repr__(self) -> str:
        return f"CompanyOsApiCloudEventStore(base_url={self.base_url!r})"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth_value:
            headers["Authorization"] = f"Bearer {self.auth_value}"
        if self.transport_subject:
            headers["x-sinria-transport-subject"] = self.transport_subject
        if self.workspace_id:
            headers["x-sinria-workspace-id"] = self.workspace_id
        if self.member_id:
            headers["x-sinria-member-id"] = self.member_id
        if self.instance_id:
            headers["x-sinria-instance-id"] = self.instance_id
        return headers


    @staticmethod
    def _check(response: Any) -> dict[str, Any]:
        response.raise_for_status()
        payload = response.json() or {}
        if payload.get("ok") is False:
            raise RuntimeError(str(payload.get("error") or payload.get("reason") or "company-os error"))
        return payload

    def fetch_pending_agent_os_tasks(
        self, *, workspace_id: str, member_id: str, instance_id: str | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        query = {"targetMemberId": member_id}
        if instance_id:
            query["targetInstanceId"] = instance_id
        payload = self._check(
            self.session.get(
                f"{self.base_url}/api/agent-os/tasks",
                headers=self._headers(),
                params=query,
                timeout=self.timeout,
            )
        )
        self._state = {
            "tasks": list(payload.get("tasks") or []),
            "claims": list(payload.get("claims") or []),
            "results": list(payload.get("results") or []),
        }
        eligible = {"queued", "failed_recoverable", "approved_for_execution"}
        return [
            task
            for task in self._state["tasks"]
            if str(task.get("workspaceId") or "") == workspace_id
            and str(task.get("targetMemberId") or "") == member_id
            and str(task.get("status") or "") in eligible
        ][:limit]

    def has_approved_agent_os_review(self, *, workspace_id: str, task_id: str) -> bool:
        if any(
            str(task.get("id") or task.get("taskId") or "") == task_id
            and str(task.get("workspaceId") or "") == workspace_id
            and str(task.get("status") or "") == "approved_for_execution"
            for task in self._state["tasks"]
        ):
            return True
        return any(
            str(result.get("taskId") or "") == task_id
            and str(result.get("workspaceId") or "") == workspace_id
            and isinstance(result.get("safety"), Mapping)
            and result["safety"].get("humanApprovalRequired") is False
            for result in self._state["results"]
        )

    def next_agent_os_task_attempt(self, *, workspace_id: str, task_id: str) -> int:
        attempts = [
            int(claim.get("attempt") or 0)
            for claim in self._state["claims"]
            if str(claim.get("taskId") or "") == task_id
            and str(claim.get("workspaceId") or "") == workspace_id
        ]
        return max(attempts, default=0) + 1

    def claim_agent_os_task(self, **kwargs: Any) -> dict[str, Any] | None:
        body = {
            "workspaceId": kwargs["workspace_id"],
            "taskId": kwargs["task_id"],
            "memberId": kwargs["member_id"],
            "instanceId": kwargs["instance_id"],
            "selectedExecutionEngine": kwargs.get("selected_execution_engine"),
        }
        payload = self._check(
            self.session.post(
                f"{self.base_url}/api/agent-os/tasks/claim",
                headers=self._headers(),
                json=body,
                timeout=self.timeout,
            )
        )
        claim = payload.get("claim")
        if isinstance(claim, Mapping):
            if (
                str(claim.get("workspaceId") or "") != str(kwargs["workspace_id"])
                or str(claim.get("taskId") or "") != str(kwargs["task_id"])
            ):
                raise RuntimeError("company-os claim scope mismatch")
            claim_id = str(claim.get("claimId") or claim.get("id") or "").strip()
            if claim_id:
                key = (str(kwargs["workspace_id"]), str(kwargs["task_id"]))
                self._claim_ids_by_task[key] = claim_id
            return dict(claim)
        return None

    def renew_agent_os_task_claim_lease(self, **kwargs: Any) -> dict[str, Any] | None:
        workspace_id = str(kwargs["workspace_id"])
        task_id = str(kwargs["task_id"])
        claim_id = str(kwargs.get("claim_id") or "").strip()
        if not claim_id:
            claim_id = self._claim_ids_by_task.get((workspace_id, task_id))
        if not claim_id:
            claim_id = next(
                (
                    str(claim.get("claimId") or claim.get("id") or "").strip()
                    for claim in self._state["claims"]
                    if str(claim.get("taskId") or "") == task_id
                    and str(claim.get("workspaceId") or "") == workspace_id
                    and int(claim.get("attempt") or 0) == int(kwargs.get("attempt") or 0)
                ),
                "",
            )
        if not claim_id:
            return None
        body = {
            "workspaceId": kwargs["workspace_id"],
            "claimId": claim_id,
            "memberId": kwargs["member_id"],
            "instanceId": kwargs["instance_id"],
            "leaseSeconds": int(kwargs.get("lease_seconds") or 300),
        }
        payload = self._check(
            self.session.post(
                f"{self.base_url}/api/agent-os/tasks/claim/renew",
                headers=self._headers(),
                json=body,
                timeout=self.timeout,
            )
        )
        claim = payload.get("claim")
        if isinstance(claim, Mapping):
            if (
                str(claim.get("workspaceId") or "") != workspace_id
                or str(claim.get("taskId") or "") != task_id
            ):
                raise RuntimeError("company-os renewed claim scope mismatch")
            return dict(claim)
        return None

    def post_agent_os_task_result(self, **kwargs: Any) -> dict[str, Any]:
        body = {
            "workspaceId": kwargs["workspace_id"],
            "taskId": kwargs["task_id"],
            "agentOsId": kwargs["agent_os_id"],
            "taskKind": kwargs["task_kind"],
            "producedByMemberId": kwargs["member_id"],
            "producedByInstanceId": kwargs["instance_id"],
            "status": kwargs["status"],
            "sanitizedSummary": kwargs["sanitized_summary"],
            "resultRefs": kwargs.get("result_refs") or [],
            "externalEgress": bool(kwargs.get("external_egress", False)),
            "humanApprovalRequired": bool(kwargs.get("human_approval_required", True)),
        }
        return self._check(
            self.session.post(
                f"{self.base_url}/api/agent-os/tasks/result",
                headers=self._headers(),
                json=body,
                timeout=self.timeout,
            )
        )

    def record_bridge_status(
        self,
        *,
        status: str = "online",
        capabilities: list[str] | None = None,
        sanitized_summary: str = "Sinria worker heartbeat",
    ) -> dict[str, Any]:
        if not self.workspace_id or not self.member_id or not self.instance_id:
            raise RuntimeError("worker identity is required for bridge heartbeat")
        return self._check(
            self.session.post(
                f"{self.base_url}/api/bridge/status",
                headers=self._headers(),
                json={
                    "workspaceId": self.workspace_id,
                    "memberId": self.member_id,
                    "instanceId": self.instance_id,
                    "status": status,
                    "capabilities": capabilities or ["agent-os-worker"],
                    "sanitizedSummary": sanitized_summary,
                },
                timeout=self.timeout,
            )
        )

    def ensure_agent_os_review_request(self, **kwargs: Any) -> None:
        # /api/agent-os/tasks/result atomically creates the linked review for a
        # waiting_review result, so a second request would duplicate it.
        del kwargs


@dataclass(frozen=True, repr=False)
class SupabaseRestCloudEventStore:
    base_url: str
    auth_value: str
    session: Any = None
    schema: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _normalize_base_url(self.base_url))
        if self.session is None:
            object.__setattr__(self, "session", requests.Session())

    def __repr__(self) -> str:
        return f"SupabaseRestCloudEventStore(base_url={self.base_url!r}, auth_hidden=True)"

    @property
    def rest_base(self) -> str:
        return f"{self.base_url}/rest/v1"

    def _headers(self, *, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.auth_value,
            "Authorization": f"Bearer {self.auth_value}",
            "Content-Type": "application/json",
        }
        if self.schema:
            headers["Accept-Profile"] = self.schema
            headers["Content-Profile"] = self.schema
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def fetch_pending_tasks(self, *, limit: int = 1) -> list[BridgeTaskEnvelope]:
        url = f"{self.rest_base}/agent_tasks?status=eq.pending&order=created_at.asc&limit={int(limit)}"
        response = self.session.get(url, headers=self._headers())
        response.raise_for_status()
        rows = response.json() or []
        return [self._task_from_row(row) for row in rows]

    def claim_task(self, task_id: str, *, run_id: str, sinria_instance_id: str, attempt: int) -> None:
        patch_url = f"{self.rest_base}/agent_tasks?id=eq.{task_id}"
        response = self.session.patch(
            patch_url,
            headers=self._headers(prefer="return=representation"),
            json={"status": BridgeTaskStatus.CLAIMED.value},
        )
        response.raise_for_status()
        run_response = self.session.post(
            f"{self.rest_base}/agent_runs",
            headers=self._headers(prefer="return=representation"),
            json={
                "id": run_id,
                "task_id": task_id,
                "sinria_instance_id": sinria_instance_id,
                "attempt": attempt,
                "status": BridgeTaskStatus.CLAIMED.value,
            },
        )
        run_response.raise_for_status()

    def mark_task_status(self, task_id: str, status: BridgeTaskStatus) -> None:
        response = self.session.patch(
            f"{self.rest_base}/agent_tasks?id=eq.{task_id}",
            headers=self._headers(),
            json={"status": status.value},
        )
        response.raise_for_status()

    def post_result(self, *, run_id: str, task_id: str, result_text: str, requires_review: bool) -> None:
        response = self.session.post(
            f"{self.rest_base}/agent_results",
            headers=self._headers(prefer="return=representation"),
            json={
                "id": f"result_{run_id}",
                "run_id": run_id,
                "result_text": result_text,
                "result_json": {"source": "on_prem_sinria_bridge"},
                "requires_review": requires_review,
            },
        )
        response.raise_for_status()
        self.mark_task_status(task_id, BridgeTaskStatus.COMPLETED)
        self.session.patch(
            f"{self.rest_base}/agent_runs?id=eq.{run_id}",
            headers=self._headers(),
            json={"status": BridgeTaskStatus.COMPLETED.value},
        ).raise_for_status()

    def create_review_request(self, *, run_id: str, task_id: str, required_role: str, reason: str) -> None:
        response = self.session.post(
            f"{self.rest_base}/review_requests",
            headers=self._headers(prefer="return=representation"),
            json={
                "id": f"review_{run_id}",
                "run_id": run_id,
                "requested_to": required_role,
                "status": "pending",
                "decision_comment": reason,
            },
        )
        response.raise_for_status()
        self.mark_task_status(task_id, BridgeTaskStatus.WAITING_REVIEW)

    # ------------------------------------------------------------------
    # Agent OS Team Mode routing (generic envelope → local Sinria execution)
    #
    # These methods are metadata-only and member/instance scoped. They never
    # carry raw context, credentials, raw drafts or raw diffs into the cloud —
    # only sanitized routing identity, lease/idempotency, and safe summaries.
    # ------------------------------------------------------------------

    def fetch_pending_agent_os_tasks(
        self, *, workspace_id: str, member_id: str, instance_id: str | None = None, limit: int = 1
    ) -> list[dict[str, Any]]:
        """Tasks targeted at this member/instance that are still claimable."""
        url = (
            f"{self.rest_base}/agent_os_tasks"
            f"?workspace_id=eq.{workspace_id}"
            f"&target_member_id=eq.{member_id}"
            f"&status=in.(queued,failed_recoverable,approved_for_execution)"
            f"&order=created_at.asc&limit={int(limit)}"
        )
        if instance_id:
            url += f"&target_instance_id=eq.{instance_id}"
        response = self.session.get(url, headers=self._headers())
        response.raise_for_status()
        return response.json() or []

    def next_agent_os_task_attempt(self, *, workspace_id: str, task_id: str) -> int:
        """Return the next monotonic claim attempt for one task.

        Historical expired/failed claims are immutable evidence. A retry must
        use a new idempotency key instead of merging into attempt 1.
        """
        query = urlencode(
            {
                "workspace_id": f"eq.{workspace_id}",
                "task_id": f"eq.{task_id}",
                "select": "attempt",
                "order": "attempt.desc",
                "limit": "1",
            }
        )
        response = self.session.get(
            f"{self.rest_base}/agent_os_task_claims?{query}",
            headers=self._headers(),
        )
        response.raise_for_status()
        rows = response.json() or []
        if not rows:
            return 1
        return max(1, int(rows[0].get("attempt") or 0) + 1)

    def claim_agent_os_task(
        self,
        *,
        workspace_id: str,
        task_id: str,
        member_id: str,
        instance_id: str,
        agent_os_id: str,
        task_kind: str,
        target_member_id: str,
        attempt: int = 1,
        lease_seconds: int = 300,
        selected_execution_engine: str = "sinria_native",
    ) -> dict[str, Any] | None:
        """Claim a routed task for this member+instance (idempotent, lease-bound).

        Only the targeted member/instance may claim; the DB partial unique index
        enforces one active lease per task. Returns the created/active claim row,
        or None if the cloud rejected the claim.
        """
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(lease_seconds))).isoformat()
        # Stable per (task, member, instance) so the active-idempotency partial
        # unique index actually rejects a duplicate concurrent claim; attempt is a
        # separate counter (a released/expired claim frees the key for re-use).
        idempotency_key = f"claim:{task_id}:{member_id}:{instance_id}"
        claim_row = {
            "claim_id": f"aotc_{task_id}_{instance_id}_{attempt}",
            "workspace_id": workspace_id,
            "task_id": task_id,
            "agent_os_id": agent_os_id,
            "task_kind": task_kind,
            "target_member_id": target_member_id,
            "claimed_by_member_id": member_id,
            "claimed_by_instance_id": instance_id,
            "claim_status": "active",
            "claim_expires_at": expires_at,
            "idempotency_key": idempotency_key,
            "attempt": int(attempt),
            "selected_execution_engine": selected_execution_engine,
            "raw_local_context_stored": False,
            "external_action_performed": False,
        }
        response = self.session.post(
            f"{self.rest_base}/agent_os_task_claims",
            headers=self._headers(prefer="resolution=merge-duplicates,return=representation"),
            json=claim_row,
        )
        response.raise_for_status()
        # Move the task into the claimed state (metadata only).
        self.session.patch(
            f"{self.rest_base}/agent_os_tasks?task_id=eq.{task_id}",
            headers=self._headers(),
            json={"status": "claimed"},
        ).raise_for_status()
        rows = response.json() or []
        return rows[0] if isinstance(rows, list) and rows else claim_row

    def renew_agent_os_task_claim(
        self, *, claim_id: str, member_id: str, instance_id: str, lease_seconds: int = 300
    ) -> None:
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(lease_seconds))).isoformat()
        response = self.session.patch(
            f"{self.rest_base}/agent_os_task_claims"
            f"?claim_id=eq.{claim_id}"
            f"&claimed_by_member_id=eq.{member_id}"
            f"&claimed_by_instance_id=eq.{instance_id}"
            f"&claim_status=eq.active",
            headers=self._headers(),
            json={"claim_expires_at": expires_at},
        )
        response.raise_for_status()

    def post_agent_os_task_result(
        self,
        *,
        workspace_id: str,
        task_id: str,
        agent_os_id: str,
        task_kind: str,
        member_id: str,
        instance_id: str,
        status: str,
        sanitized_summary: str,
        result_refs: list[dict[str, Any]] | None = None,
        external_egress: bool = False,
        human_approval_required: bool = True,
        attempt: int | None = None,
    ) -> None:
        """Post a SANITIZED result back to cloud. Raw bodies/diffs stay local.

        ``attempt`` scopes the row id per claim attempt; without it, retries
        upsert-merge into the first attempt's row and later outcomes become
        invisible (created_at stays at attempt 1).
        """
        # Never fall back to a direct table write: it bypasses task/claim
        # validation and would be unable to distinguish legacy duplicate rows.
        result_input = {
            "workspaceId": workspace_id,
            "taskId": task_id,
            "agentOsId": agent_os_id,
            "taskKind": task_kind,
            "producedByMemberId": member_id,
            "producedByInstanceId": instance_id,
            "status": status,
            "sanitizedSummary": sanitized_summary,
            "resultRefs": result_refs or [],
            "externalEgress": bool(external_egress),
            "humanApprovalRequired": bool(human_approval_required),
        }
        response = self.session.post(
            f"{self.rest_base}/rpc/record_agent_os_task_result_v1",
            headers=self._headers(prefer="return=representation"),
            json={"p_input": result_input},
        )
        response.raise_for_status()
        payload = response.json()
        return payload[0] if isinstance(payload, list) and payload else payload

    def renew_agent_os_task_claim_lease(
        self,
        *,
        workspace_id: str,
        task_id: str,
        member_id: str,
        instance_id: str,
        lease_seconds: int = 600,
    ) -> None:
        """Extend this member+instance's active lease while a run is in flight."""
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(lease_seconds))).isoformat()
        self.session.patch(
            f"{self.rest_base}/agent_os_task_claims"
            f"?workspace_id=eq.{workspace_id}"
            f"&task_id=eq.{task_id}"
            f"&claimed_by_member_id=eq.{member_id}"
            f"&claimed_by_instance_id=eq.{instance_id}"
            f"&claim_status=eq.active",
            headers=self._headers(),
            json={"claim_expires_at": expires_at},
        ).raise_for_status()

    def has_approved_agent_os_review(
        self, *, workspace_id: str, task_id: str
    ) -> bool:
        """Read durable proof that this task already entered an approved execution."""
        response = self.session.get(
            f"{self.rest_base}/agent_os_task_results"
            f"?workspace_id=eq.{workspace_id}"
            f"&task_id=eq.{task_id}"
            f"&human_approval_required=eq.false"
            f"&select=result_id"
            f"&limit=1",
            headers=self._headers(),
        )
        response.raise_for_status()
        return bool(response.json())

    def ensure_agent_os_review_request(
        self,
        *,
        workspace_id: str,
        task_id: str,
        agent_os_id: str,
        task_kind: str,
        member_id: str,
        instance_id: str,
        required_authority: str = "self",
        sanitized_summary: str = "",
    ) -> None:
        """Guarantee exactly one open ReviewRequest linked to a paused task.

        The direct-PostgREST worker path bypasses the company-os repository, so
        the waiting_review → linked-review invariant must also be enforced here
        (approval in the Sheets 承認待ち tab resumes the task via the API).
        """
        existing = self.session.get(
            f"{self.rest_base}/review_requests"
            f"?workspace_id=eq.{workspace_id}"
            f"&task_id=eq.{task_id}"
            f"&select=review_id,status"
            f"&limit=1",
            headers=self._headers(),
        )
        existing.raise_for_status()
        if existing.json():
            return
        # Map the task authority onto the review Role vocabulary (same mapping
        # as company-os-types.reviewAuthorityForTask): physician has no Role
        # equivalent yet and escalates to owner rather than silently widening.
        authority = {
            "admin": "admin",
            "owner": "owner",
            "physician": "owner",
        }.get(str(required_authority), "reviewer")
        row = {
            "review_id": f"review_{task_id}",
            "workspace_id": workspace_id,
            "requested_by_member_id": member_id,
            "requested_by_instance_id": instance_id,
            "required_authority": authority,
            "operation_type": f"agent_os_task:{agent_os_id}:{task_kind}",
            "sanitized_summary": sanitized_summary or "human approval required",
            "status": "waiting",
            "task_id": task_id,
            "raw_payload_stored": False,
            "external_action_performed": False,
        }
        response = self.session.post(
            f"{self.rest_base}/review_requests",
            headers=self._headers(
                prefer="resolution=ignore-duplicates,return=representation"
            ),
            json=row,
        )
        response.raise_for_status()

    def record_knowledge_asset_observation(self, **kwargs: Any) -> dict[str, Any]:
        row = {
            "observation_id": kwargs["observation_id"],
            "workspace_id": kwargs["workspace_id"],
            "observed_by_member_id": kwargs["observed_by_member_id"],
            "observed_by_instance_id": kwargs["observed_by_instance_id"],
            "source_kind": kwargs.get("source_kind", "outcome"),
            "domain": kwargs.get("domain", "sales"),
            "sanitized_summary": kwargs["sanitized_summary"],
            "outcome_signal": kwargs.get("outcome_signal", "unknown"),
            "source_refs": kwargs.get("source_refs") or [],
            "raw_source_stored": False,
            "raw_media_stored": False,
            "patient_data_stored": False,
            "external_action_performed": False,
        }
        response = self.session.post(
            f"{self.rest_base}/knowledge_asset_observations",
            headers=self._headers(prefer="resolution=merge-duplicates,return=representation"),
            json=row,
        )
        response.raise_for_status()
        rows = response.json() or []
        return rows[0] if isinstance(rows, list) and rows else row

    def record_knowledge_asset_candidate(self, **kwargs: Any) -> dict[str, Any]:
        row = {
            "asset_id": kwargs["asset_id"],
            "workspace_id": kwargs["workspace_id"],
            "proposed_by_member_id": kwargs["proposed_by_member_id"],
            "proposed_by_instance_id": kwargs["proposed_by_instance_id"],
            "asset_kind": kwargs.get("asset_kind", "playbook_candidate"),
            "title": kwargs["title"],
            "sanitized_pattern": kwargs["sanitized_pattern"],
            "evidence_summary": kwargs["evidence_summary"],
            "confidence": kwargs.get("confidence", "medium"),
            "status": kwargs.get("status", "candidate"),
            "reuse_targets": kwargs.get("reuse_targets") or [],
            "source_observation_ids": kwargs.get("source_observation_ids") or [],
            "human_approval_required": True,
            "raw_evidence_stored": False,
            "raw_source_stored": False,
            "raw_procedure_body_stored": False,
            "external_action_performed": False,
        }
        response = self.session.post(
            f"{self.rest_base}/knowledge_asset_candidates",
            headers=self._headers(prefer="resolution=merge-duplicates,return=representation"),
            json=row,
        )
        response.raise_for_status()
        rows = response.json() or []
        return rows[0] if isinstance(rows, list) and rows else row

    def record_improvement_candidate(self, **kwargs: Any) -> dict[str, Any]:
        row = {
            "candidate_id": kwargs["candidate_id"],
            "workspace_id": kwargs["workspace_id"],
            "proposed_by_member_id": kwargs["proposed_by_member_id"],
            "proposed_by_instance_id": kwargs.get("proposed_by_instance_id"),
            "title": kwargs["title"],
            "sanitized_summary": kwargs["sanitized_summary"],
            "category": kwargs.get("category", "process"),
            "status": kwargs.get("status", "proposed"),
            "human_approval_required": True,
            "raw_evidence_stored": False,
            "skill_body_stored": False,
            "external_action_performed": False,
        }
        response = self.session.post(
            f"{self.rest_base}/improvement_candidates",
            headers=self._headers(prefer="resolution=merge-duplicates,return=representation"),
            json=row,
        )
        response.raise_for_status()
        rows = response.json() or []
        return rows[0] if isinstance(rows, list) and rows else row

    @staticmethod
    def _task_from_row(row: Mapping[str, Any]) -> BridgeTaskEnvelope:
        return bridge_task_from_postgrest_row(row)
