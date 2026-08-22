"""Metadata-only Company OS transport client for gateway collaboration commands."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Union
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen


class CompanyOsTransportError(RuntimeError):
    """A safe, user-displayable Company OS transport failure."""


@dataclass(frozen=True)
class CompanyOsTransportIdentity:
    transport_subject: str
    member_id: str
    instance_id: Optional[str] = None


class CompanyOsTransportClient:
    def __init__(self, base_url: str, *, token_env: str = "COMPANY_OS_BRIDGE_TOKEN", timeout: float = 10.0):
        normalized = (base_url or "").strip().rstrip("/")
        parsed = urlparse(normalized)
        if not normalized or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("company_os_base_url must be an absolute HTTP(S) URL")
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("company_os_base_url must use HTTPS outside localhost")
        self.base_url = normalized
        self.token_env = token_env
        self.timeout = timeout

    def _token(self) -> str:
        token = os.environ.get(self.token_env, "").strip()
        if not token:
            raise CompanyOsTransportError(f"{self.token_env} is not configured")
        return token

    def _request(self, method: str, path: str, *, transport_subject: str, query: Optional[Mapping[str, Union[str, Sequence[str]]]] = None, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        if query:
            url = f"{url}?{urlencode(query, doseq=True)}"
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=payload,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "X-Sinria-Transport-Subject": transport_subject,
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if payload is not None else {}),
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                detail = payload.get("error") if isinstance(payload, dict) else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                detail = None
            raise CompanyOsTransportError(
                f"Company OS rejected the request ({exc.code}): {detail or exc.reason}"
            ) from exc
        except Exception as exc:
            raise CompanyOsTransportError("Company OSへの接続または応答検証に失敗しました") from exc
        if not isinstance(data, dict) or data.get("ok") is not True:
            error = data.get("error") if isinstance(data, dict) else None
            raise CompanyOsTransportError(str(error or "Company OS rejected the request"))
        if data.get("sourceOfTruth") != "company_os":
            raise CompanyOsTransportError("Company OS source-of-truth marker is missing")
        return data

    def list_team(self, identity: CompanyOsTransportIdentity, *, task_id: Optional[str] = None) -> Dict[str, Any]:
        query: Dict[str, Union[str, Sequence[str]]] = {}
        if task_id:
            query["taskId"] = task_id
        return self._request("GET", "/api/agent-os/transport", transport_subject=identity.transport_subject, query=query)

    def canary(self, identity: CompanyOsTransportIdentity) -> Dict[str, Any]:
        """Verify authenticated POST routing without mutating Company OS state."""
        return self._request(
            "POST",
            "/api/agent-os/transport",
            transport_subject=identity.transport_subject,
            body={
                "action": "canary",
            },
        )

    def handoff(self, identity: CompanyOsTransportIdentity, *, task_id: str, target_member_id: str, target_instance_id: Optional[str] = None) -> Dict[str, Any]:
        current = self.list_team(identity, task_id=task_id).get("tasks", [])
        if len(current) != 1 or not current[0].get("updatedAt"):
            raise CompanyOsTransportError("Company OS task concurrency token is unavailable")
        return self._request(
            "POST",
            "/api/agent-os/transport",
            transport_subject=identity.transport_subject,
            body={
                "action": "handoff",
                "taskId": task_id,
                "targetMemberId": target_member_id,
                "targetInstanceId": target_instance_id,
                "expectedUpdatedAt": current[0]["updatedAt"],
            },
        )

    def decide_review(
        self,
        identity: CompanyOsTransportIdentity,
        *,
        review_id: str,
        approved: bool,
        actor_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        body = {
            "action": "approve" if approved else "deny",
            "reviewId": review_id,
        }
        if actor_id:
            body["actorId"] = actor_id
        if idempotency_key:
            body["idempotencyKey"] = idempotency_key
        return self._request(
            "POST",
            "/api/agent-os/transport",
            transport_subject=identity.transport_subject,
            body=body,
        )

    def read_review(self, identity: CompanyOsTransportIdentity, *, review_id: str) -> Dict[str, Any]:
        """Read the authoritative review after a decision."""
        return self._request(
            "GET",
            "/api/agent-os/transport",
            transport_subject=identity.transport_subject,
            query={"reviewId": review_id},
        )

    def conversation_runs(self, identity: CompanyOsTransportIdentity, *, action: str,
                          idempotency_key: Optional[str] = None, **fields: Any) -> Dict[str, Any]:
        allowed = {"create_conversation_run", "list_conversation_runs", "claim_conversation_run",
                   "heartbeat_conversation_run", "complete_conversation_run", "fail_conversation_run",
                   "sweep_conversation_runs", "list_conversation_events", "append_conversation_event"}
        if action not in allowed:
            raise ValueError("unsupported conversation run action")
        body = {"action": action, **{k: v for k, v in fields.items() if v is not None}}
        if idempotency_key:
            body["idempotencyKey"] = idempotency_key
        result = self._request("POST", "/api/agent-os/transport",
                               transport_subject=identity.transport_subject, body=body)
        forbidden = {"prompt", "rawprompt", "credentials", "credential", "phi", "patientdata", "output"}
        def unsafe(value: Any) -> bool:
            if isinstance(value, dict):
                return any(str(k).lower() in forbidden or unsafe(v) for k, v in value.items())
            return isinstance(value, list) and any(unsafe(v) for v in value)
        if unsafe(result):
            raise CompanyOsTransportError("Company OS returned unsafe conversation metadata")
        return result

    def create_conversation_run(self, identity, **fields):
        return self.conversation_runs(identity, action="create_conversation_run", **fields)
    def list_conversation_runs(self, identity, **fields):
        return self.conversation_runs(identity, action="list_conversation_runs", **fields)
    def claim_conversation_run(self, identity, **fields):
        return self.conversation_runs(identity, action="claim_conversation_run", **fields)
    def heartbeat_conversation_run(self, identity, **fields):
        return self.conversation_runs(identity, action="heartbeat_conversation_run", **fields)
    def complete_conversation_run(self, identity, **fields):
        return self.conversation_runs(identity, action="complete_conversation_run", **fields)
    def fail_conversation_run(self, identity, **fields):
        return self.conversation_runs(identity, action="fail_conversation_run", **fields)
    def sweep_conversation_runs(self, identity, **fields):
        return self.conversation_runs(identity, action="sweep_conversation_runs", **fields)
    def list_conversation_events(self, identity, **fields):
        return self.conversation_runs(identity, action="list_conversation_events", **fields)
    def append_conversation_event(self, identity, **fields):
        return self.conversation_runs(identity, action="append_conversation_event", **fields)
