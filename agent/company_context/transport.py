"""Durable, metadata-only worker transport for the existing Company OS routes.

This module is deliberately an adapter, not a second task API.  The HTTP
implementation is injected so the entrypoint and deterministic E2E tests use
the same state machine.  The local journal contains only retry metadata.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Protocol, cast

from .bridge import validate_metadata_payload
from .policy import WorkspaceIdentity
from .state import LocalSyncState


class HttpClient(Protocol):
    def request(self, method: str, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> dict[str, Any]: ...


class TransportOffline(ConnectionError):
    pass


_METADATA_KEYS = {
    "workspaceId", "agentOsId", "taskKind", "title", "instruction", "payload",
    "requestedByMemberId", "requestedByInstanceId", "targetMemberId", "targetInstanceId",
    "memberId", "instanceId", "producedByMemberId", "producedByInstanceId", "taskId",
    "claimId", "reviewId", "resultId", "status", "sanitizedSummary", "resultRefs",
    "humanApprovalRequired", "externalEgress", "externalActionPerformed", "rawContextAllowedInCloud",
    "revision", "expectedRevision", "claimAttempt", "attempt", "leaseSeconds", "claimExpiresAt",
    "idempotencyKey", "transportSubject", "bridgeMemberId", "bridgeInstanceId",
    "selectedExecutionEngine", "action", "decision",
}


def _metadata(value: dict[str, Any]) -> dict[str, Any]:
    """Reject unknown/raw fields before they can enter HTTP or durable state."""
    if not isinstance(value, dict):
        raise ValueError("transport payload must be an object")
    clean = {k: v for k, v in value.items() if k in _METADATA_KEYS}
    if set(clean) != set(value):
        raise ValueError("transport accepts metadata-only fields")
    # The shared candidate validator intentionally rejects words such as
    # ``transport`` as raw-content keys.  ``transportSubject`` is a protocol
    # identity, so validate it as a scalar while applying the same validator to
    # the content-bearing subset.
    protocol = {"transportSubject", "action", "decision"}
    validate_metadata_payload({k: v for k, v in clean.items() if k not in protocol})
    for key in protocol:
        if key in clean and not isinstance(clean[key], str):
            raise ValueError(f"{key} must be a scalar metadata value")
    return clean


class CompanyOsTransport:
    """Worker-side binding to /api/agent-os/*; all writes are idempotent."""

    def __init__(self, base_url: str, *, identity: WorkspaceIdentity, bridge_token: str,
                 state: LocalSyncState, http: HttpClient | Callable[..., dict[str, Any]],
                 clock: Callable[[], float] = time.time) -> None:
        if not bridge_token:
            raise ValueError("bridge credential is required")
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Company OS transport requires an absolute HTTPS URL")
        self.base_url = base_url.rstrip("/")
        self.identity = identity
        self.bridge_token = bridge_token
        self.state = state
        self.http = http
        self.clock = clock


    def _call(self, method: str, path: str, body: dict[str, Any], key: str) -> dict[str, Any]:
        body = dict(body)
        body.setdefault("workspaceId", self.identity.workspace_id)
        body.setdefault("bridgeMemberId", self.identity.member_id)
        body.setdefault("bridgeInstanceId", self.identity.instance_id)
        body = _metadata(body)
        headers = {"Authorization": f"Bearer {self.bridge_token}", "Idempotency-Key": key,
                   "X-Sinria-Workspace": self.identity.workspace_id, "X-Sinria-Member": self.identity.member_id,
                   "X-Sinria-Instance": self.identity.instance_id}
        try:
            if hasattr(self.http, "request"):
                result = cast(HttpClient, self.http).request(method, self.base_url + path, headers=headers, json=body)
            else:
                result = cast(Callable[..., dict[str, Any]], self.http)(method, self.base_url + path, headers, body)
            if not isinstance(result, dict):
                raise TransportOffline("invalid transport response")
            return result
        except (TimeoutError, ConnectionError, OSError) as exc:
            # Only this small envelope is durable; never exception text/body.
            self.state.record(key, operation=path, status="retry", next_attempt_at=self.clock(),
                              revision=body.get("revision"), claim_attempt=body.get("claimAttempt"))
            raise TransportOffline("Company OS unavailable") from exc

    def create(self, *, task_kind: str, title: str, instruction: str, agent_os_id: str,
               idempotency_key: str, revision: int = 1, human_approval_required: bool = True) -> dict[str, Any]:
        body = {"workspaceId": self.identity.workspace_id, "agentOsId": agent_os_id, "taskKind": task_kind,
                "title": title, "instruction": instruction, "requestedByMemberId": self.identity.member_id,
                "requestedByInstanceId": self.identity.instance_id, "targetMemberId": self.identity.member_id,
                "targetInstanceId": self.identity.instance_id, "idempotencyKey": idempotency_key,
                "revision": revision, "humanApprovalRequired": human_approval_required}
        result = self._call("POST", "/api/agent-os/tasks", body, idempotency_key)
        self.state.record(idempotency_key, operation="create", status="accepted", revision=revision,
                          task_id=result.get("taskId"))
        return result

    def claim(self, task_id: str, *, idempotency_key: str, revision: int, lease_seconds: int = 300) -> dict[str, Any]:
        body = {"workspaceId": self.identity.workspace_id, "taskId": task_id, "memberId": self.identity.member_id,
                "instanceId": self.identity.instance_id, "revision": revision, "expectedRevision": revision,
                "leaseSeconds": lease_seconds, "idempotencyKey": idempotency_key}
        result = self._call("POST", "/api/agent-os/tasks/claim", body, idempotency_key)
        claim = result.get("claim") or {}
        self.state.record(idempotency_key, operation="claim", status="claimed" if result.get("ok") else "rejected",
                          revision=revision, task_id=task_id, claim_id=claim.get("claimId"),
                          claim_attempt=claim.get("attempt"))
        return result

    def renew(self, claim_id: str, *, idempotency_key: str, revision: int) -> dict[str, Any]:
        body = {"claimId": claim_id, "memberId": self.identity.member_id, "instanceId": self.identity.instance_id,
                "revision": revision, "expectedRevision": revision, "idempotencyKey": idempotency_key}
        return self._call("POST", "/api/agent-os/tasks/claim/renew", body, idempotency_key)

    def result(self, *, task_id: str, agent_os_id: str, task_kind: str, status: str,
               summary: str, result_refs: list[str], idempotency_key: str, revision: int,
               claim_attempt: int) -> dict[str, Any]:
        body = {"workspaceId": self.identity.workspace_id, "taskId": task_id, "agentOsId": agent_os_id,
                "taskKind": task_kind, "producedByMemberId": self.identity.member_id,
                "producedByInstanceId": self.identity.instance_id, "status": status,
                "sanitizedSummary": summary, "resultRefs": result_refs, "revision": revision,
                "claimAttempt": claim_attempt, "idempotencyKey": idempotency_key,
                "externalEgress": False, "humanApprovalRequired": status == "waiting_review"}
        return self._call("POST", "/api/agent-os/tasks/result", body, idempotency_key)

    def readback(self, *, task_id: str, idempotency_key: str) -> dict[str, Any]:
        return self._call("GET", "/api/agent-os/transport?taskId=" + task_id +
                          "&transportSubject=" + self.identity.member_id,
                          {"workspaceId": self.identity.workspace_id, "taskId": task_id,
                           "transportSubject": self.identity.member_id, "idempotencyKey": idempotency_key}, idempotency_key)

    def approval(self, review_id: str, *, approve: bool, idempotency_key: str, revision: int) -> dict[str, Any]:
        body = {"action": "approve" if approve else "deny", "reviewId": review_id,
                "transportSubject": self.identity.member_id, "revision": revision,
                "idempotencyKey": idempotency_key}
        return self._call("POST", "/api/agent-os/transport", body, idempotency_key)


class UrllibJsonClient:
    """Minimal production HTTP client; response bodies are never persisted."""

    def __init__(self, *, timeout: float = 15.0) -> None:
        self.timeout = timeout

    def request(self, method: str, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> dict[str, Any]:
        import json as json_module
        from urllib import request

        payload = json_module.dumps(json, separators=(",", ":")).encode("utf-8")
        req = request.Request(
            url,
            data=payload,
            method=method,
            headers={**headers, "Content-Type": "application/json", "Accept": "application/json"},
        )
        with request.urlopen(req, timeout=self.timeout) as response:  # nosec B310: explicit operator URL
            decoded = json_module.loads(response.read().decode("utf-8"))
        if not isinstance(decoded, dict):
            raise TransportOffline("invalid transport response")
        return decoded


class FakeHttp:
    """Dependency-injected protocol fake, including lost responses and revoke."""
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.tasks: dict[str, dict[str, Any]] = {}
        self.results: dict[str, dict[str, Any]] = {}
        self.fail_next: str | None = None
        self._seq = 0

    def request(self, method: str, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, url, dict(json)))
        fault = self.fail_next
        if fault in {"offline", "lost_response"}:
            self.fail_next = None
            if fault == "offline":
                raise ConnectionError("offline")
        path = url.split("?", 1)[0]
        if path.endswith("/tasks") and method == "POST":
            key = json["idempotencyKey"]; task = self.tasks.setdefault(key, {"taskId": "task-1", "revision": json.get("revision", 1), "status": "queued", **json})
            out = {"ok": True, "taskId": task["taskId"], "status": task["status"], "task": task}
        elif path.endswith("/tasks/claim") and method == "POST":
            task = next(t for t in self.tasks.values() if t["taskId"] == json["taskId"])
            task["status"] = "claimed"; self._seq += 1
            out = {"ok": True, "claim": {"claimId": "claim-1", "attempt": self._seq, "taskId": task["taskId"], "claimExpiresAt": "future"}}
        elif path.endswith("/tasks/claim/renew"):
            out = {"ok": True, "claim": {"claimId": json["claimId"], "attempt": 1, "claimExpiresAt": "future"}}
        elif path.endswith("/tasks/result"):
            task = next(t for t in self.tasks.values() if t["taskId"] == json["taskId"])
            task["status"] = json["status"]
            self.results[json["taskId"]] = dict(json); out = {"ok": True, "resultId": "result-1", "result": json}
        elif path.endswith("/transport") and method == "GET":
            out = {"ok": True, "tasks": list(self.tasks.values()), "readbacks": list(self.results.values())}
        elif path.endswith("/transport"):
            out = {"ok": True, "review": {"reviewId": json.get("reviewId"), "decision": json.get("action")}}
        else:
            raise ValueError("unsupported fake route")
        if fault == "lost_response":
            raise TimeoutError("lost response")
        return out

    def revoke(self, task_id: str) -> None:
        for task in self.tasks.values():
            if task["taskId"] == task_id: task["status"] = "revoked"
