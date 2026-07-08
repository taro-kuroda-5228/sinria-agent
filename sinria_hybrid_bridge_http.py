"""HTTP cloud-event adapters for Sinria Hybrid Agent Bridge.

The adapter targets Supabase/PostgREST-compatible APIs used by Vercel/Supabase
ChatOps apps. It is small and explicit so credentials stay in headers/env and
never in cloud task payloads or object reprs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import requests

from sinria_hybrid_bridge import BridgeTaskEnvelope, BridgeTaskStatus
from sinria_hybrid_bridge_transports import bridge_task_from_postgrest_row


def _normalize_base_url(url: str) -> str:
    return url.rstrip("/")


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
        self, *, workspace_id: str, member_id: str, limit: int = 1
    ) -> list[dict[str, Any]]:
        """Tasks targeted at this member that are still claimable."""
        url = (
            f"{self.rest_base}/agent_os_tasks"
            f"?workspace_id=eq.{workspace_id}"
            f"&target_member_id=eq.{member_id}"
            f"&status=in.(queued,failed_recoverable)"
            f"&order=created_at.asc&limit={int(limit)}"
        )
        response = self.session.get(url, headers=self._headers())
        response.raise_for_status()
        return response.json() or []

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
    ) -> None:
        """Post a SANITIZED result back to cloud. Raw bodies/diffs stay local."""
        result_row = {
            "result_id": f"aor_{task_id}_{instance_id}",
            "workspace_id": workspace_id,
            "task_id": task_id,
            "agent_os_id": agent_os_id,
            "task_kind": task_kind,
            "produced_by_member_id": member_id,
            "produced_by_instance_id": instance_id,
            "status": status,
            "sanitized_summary": sanitized_summary,
            "result_refs": result_refs or [],
            "external_egress": bool(external_egress),
            "human_approval_required": bool(human_approval_required),
            "raw_result_body_stored": False,
            "credential_stored_in_cloud": False,
            "external_action_performed": False,
        }
        response = self.session.post(
            f"{self.rest_base}/agent_os_task_results",
            headers=self._headers(prefer="resolution=merge-duplicates,return=representation"),
            json=result_row,
        )
        response.raise_for_status()
        task_status = status if status in {"completed", "waiting_review", "failed_recoverable"} else "waiting_review"
        self.session.patch(
            f"{self.rest_base}/agent_os_tasks?task_id=eq.{task_id}",
            headers=self._headers(),
            json={"status": task_status},
        ).raise_for_status()

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
