"""Metadata-only, fail-closed peer collaboration runtime."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional
from sinria_consultation import validate_consultation

_SECRET = re.compile(r"(?i)(?:sk-[A-Za-z0-9_-]+|(?:token|secret|password|credential|authorization|bearer|api[_-]?key)\s*[:=]?\s*[^\s,;]+)")
_PHI = re.compile(r"(?is)\b(?:patient|medical\s+record|diagnosis|ssn|social\s+security|dob|date\s+of\s+birth|mrn)\s*[:#-]?[^\n;,.]{0,240}")
_ALLOWED_REF = re.compile(r"^(?:local|run)://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{1,200}$")
SAFE_PEER_ERROR_CODES = frozenset({
    "workspace_token_missing",
    "workspace_token_invalid",
    "workspace_token_refresh_failed",
    "workspace_source_access_denied",
    "workspace_source_unavailable",
    "consultation_execution_rejected",
})


def safe_failure_note(value: Any) -> str:
    code = str(value)
    return code if code in SAFE_PEER_ERROR_CODES else "peer execution failed"


def sanitize_summary(value: Any, *, limit: int = 500) -> str:
    """Return a bounded, single-line preview; this is not a PHI classifier."""
    text = "" if value is None else str(value)
    text = _SECRET.sub("[redacted]", text)
    text = _PHI.sub("[sensitive-redacted]", text)
    return " ".join(text.split())[:limit]


def _refs(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(ref)[:200] for ref in value if isinstance(ref, str) and _ALLOWED_REF.fullmatch(ref)]


def safe_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    """Build the only result shape allowed to cross the cloud boundary."""
    refs = _refs(value.get("refs"))
    consultation = validate_consultation(value.get("consultationMetadata"))
    body_ref = value.get("bodyRef")
    if isinstance(body_ref, Mapping):
        mode = body_ref.get("mode")
        ref = body_ref.get("ref")
        key_envelope_id = body_ref.get("keyEnvelopeId")
        if (mode not in {"local_only", "e2ee_envelope", "cloud_opt_in"}
                or not isinstance(ref, str) or not _ALLOWED_REF.fullmatch(ref)
                or not (key_envelope_id is None or isinstance(key_envelope_id, str))
                or (mode == "e2ee_envelope" and not key_envelope_id)):
            body_ref = None
        else:
            body_ref = {"mode": mode, "ref": ref[:200],
                        "keyEnvelopeId": key_envelope_id[:200] if key_envelope_id else None}
    else:
        body_ref = None
    return {
        "summary": sanitize_summary(value.get("summary", value.get("result", ""))),
        "refs": refs,
        **({"consultationMetadata": consultation} if consultation else {}),
        **({"bodyRef": body_ref} if body_ref else {}),
        # A sanitizer cannot prove absence of PHI. These are explicit provenance facts only.
        "safetyFlags": {"rawPrompt": False, "credentials": False, "rawContextStored": bool(value.get("rawContextStored", False)),
                         "externalActionPerformed": bool(value.get("externalActionPerformed", False))},
    }


@dataclass(frozen=True)
class ConversationEvent:
    event_id: str
    workspace_id: str
    space_id: str
    conversation_id: str
    kind: str
    author_kind: str
    author_member_id: Optional[str]
    author_instance_id: Optional[str]
    sanitized_preview: str
    consultation: Any = None
    body_ref: Any = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ConversationEvent":
        if not isinstance(payload, Mapping):
            raise ValueError("event payload must be an object")
        required = ("eventId", "workspaceId", "spaceId", "conversationId", "kind", "authorKind", "sanitizedPreview")
        if any(not isinstance(payload.get(k), str) or not payload[k].strip() for k in required):
            raise ValueError("event payload is missing required string fields")
        if "body" in payload or "prompt" in payload or "rawPrompt" in payload:
            raise ValueError("raw event content is forbidden")
        return cls(payload["eventId"], payload["workspaceId"], payload["spaceId"], payload["conversationId"],
                   payload["kind"], payload["authorKind"], payload.get("authorMemberId"), payload.get("authorInstanceId"),
                   sanitize_summary(payload["sanitizedPreview"]), validate_consultation(payload.get("consultationMetadata")), payload.get("bodyRef"))

    def callback_payload(self) -> dict[str, Any]:
        return {"eventId": self.event_id, "workspaceId": self.workspace_id, "spaceId": self.space_id,
                "conversationId": self.conversation_id, "kind": self.kind, "authorKind": self.author_kind,
                "authorMemberId": self.author_member_id, "authorInstanceId": self.author_instance_id,
                "sanitizedPreview": self.sanitized_preview, **({"consultationMetadata": self.consultation} if self.consultation else {}),
                **({"bodyRef": self.body_ref} if self.body_ref else {})}


@dataclass(frozen=True)
class ConversationRun:
    run_id: str
    workspace_id: str
    space_id: str
    conversation_id: str
    triggered_by_event_id: str
    source_member_id: Optional[str]
    target_member_id: str
    target_instance_id: Optional[str]
    status: str
    revision: int = 0
    human_relay_count: int = 0

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ConversationRun":
        if not isinstance(payload, Mapping) or "state" in payload:
            raise ValueError("run payload must use status, not state")
        required = ("runId", "workspaceId", "spaceId", "conversationId", "triggeredByEventId", "targetMemberId", "status")
        if any(not isinstance(payload.get(k), str) or not payload[k].strip() for k in required):
            raise ValueError("run payload is missing required fields")
        if not isinstance(payload.get("revision", 0), int) or not isinstance(payload.get("humanRelayCount", 0), int):
            raise ValueError("run counters must be integers")
        return cls(payload["runId"], payload["workspaceId"], payload["spaceId"], payload["conversationId"], payload["triggeredByEventId"],
                   payload.get("sourceMemberId"), payload["targetMemberId"], payload.get("targetInstanceId"), payload["status"],
                   payload.get("revision", 0), payload.get("humanRelayCount", 0))


class PeerCollaborationRunner:
    def __init__(self, transport: Any, identity: Any, *, target_member_id: str, target_instance_id: str,
                 executor: Callable, validator: Callable, heartbeat: Optional[Callable] = None,
                 mode: str = "executor", max_rounds: int = 3):
        if mode not in {"executor", "validator"} or max_rounds < 1:
            raise ValueError("invalid peer runner configuration")
        self.transport, self.identity = transport, identity
        self.target_member_id, self.target_instance_id = target_member_id, target_instance_id
        self.executor, self.validator, self.heartbeat, self.mode, self.max_rounds = executor, validator, heartbeat, mode, max_rounds
        self._attempts: dict[str, int] = {}


    @staticmethod
    def _key(run_id: str, action: str, attempt: int) -> str:
        return hashlib.sha256(f"sinria-peer:{run_id}:{action}:{attempt}".encode()).hexdigest()

    def poll(self) -> list[ConversationRun]:
        self.transport.sweep_conversation_runs(self.identity)
        result = self.transport.list_conversation_runs(self.identity, targetMemberId=self.target_member_id, targetInstanceId=self.target_instance_id)
        rows = result.get("runs")
        if not isinstance(rows, list):
            raise ValueError("run list response is invalid")
        matching = [
            run
            for row in rows
            for run in [ConversationRun.from_payload(row)]
            if run.target_member_id == self.target_member_id
            and (run.target_instance_id is None or run.target_instance_id == self.target_instance_id)
        ]
        return sorted(matching, key=lambda run: 0 if run.status == "queued" else 1)

    def _load_event(self, run: ConversationRun) -> ConversationEvent:
        result = self.transport.list_conversation_events(self.identity, conversationId=run.conversation_id)
        rows = result.get("events")
        if not isinstance(rows, list):
            raise ValueError("event list response is invalid")
        event_payload = next((row for row in rows if isinstance(row, Mapping) and row.get("eventId") == run.triggered_by_event_id), None)
        if event_payload is None:
            raise ValueError("triggering event is missing")
        event = ConversationEvent.from_payload(event_payload)
        if (event.event_id != run.triggered_by_event_id or event.workspace_id != run.workspace_id or event.space_id != run.space_id or event.conversation_id != run.conversation_id):
            raise ValueError("triggering event metadata does not match run")
        return event

    def _append(self, run: ConversationRun, kind: str, preview: str, action: str, attempt: int, *, body_ref: Any = None, consultation: Any = None) -> ConversationEvent:
        result = self.transport.append_conversation_event(self.identity, spaceId=run.space_id, conversationId=run.conversation_id,
            kind=kind, sanitizedPreview=sanitize_summary(preview), consultationMetadata=consultation, bodyRef=body_ref, idempotencyKey=self._key(run.run_id, action, attempt))
        return ConversationEvent.from_payload(result.get("event", result))

    def run_once(self) -> Optional[dict[str, Any]]:
        candidates = [r for r in self.poll() if r.status in {"queued", "failed_recoverable"}]
        if not candidates:
            return None
        run = candidates[0]
        attempt = self._attempts.get(run.run_id, 0) + 1
        self._attempts[run.run_id] = attempt

        try:
            claimed = self.transport.claim_conversation_run(self.identity, runId=run.run_id, idempotencyKey=self._key(run.run_id, "claim", attempt))
            run = ConversationRun.from_payload(claimed.get("run", claimed))
            event = self._load_event(run)
            notification_context = {
                "authorMemberId": event.author_member_id,
                "authorInstanceId": event.author_instance_id,
                "sanitizedPreview": event.sanitized_preview,
            }
            if self.heartbeat: self.heartbeat(run)
            if self.mode == "executor":
                result = self.executor(run, event.callback_payload())
                if not isinstance(result, Mapping): raise ValueError("executor must return an object")
                payload = safe_metadata(result)
                assistant = self._append(run, "assistant_message", payload["summary"], "assistant", attempt, body_ref=payload.get("bodyRef"), consultation=payload.get("consultationMetadata"))
                author = event.author_member_id
                if not author: raise ValueError("validation target author identity is absent")
                validation = self.transport.create_conversation_run(self.identity, spaceId=run.space_id, conversationId=run.conversation_id,
                    triggeredByEventId=assistant.event_id, targetMemberId=author, targetInstanceId=event.author_instance_id,
                    idempotencyKey=self._key(run.run_id, "validation", attempt))
                self.transport.complete_conversation_run(self.identity, runId=run.run_id, sanitizedStatusNote=payload["summary"], idempotencyKey=self._key(run.run_id, "complete", attempt))
                created = validation.get("run", validation)
                return {"runId": run.run_id, "status": "completed", "validationRunId": created.get("runId"), **payload}
            if event.kind != "assistant_message":
                self.transport.complete_conversation_run(
                    self.identity,
                    runId=run.run_id,
                    sanitizedStatusNote="unsupported_validator_event",
                    idempotencyKey=self._key(run.run_id, "complete", attempt),
                )
                return {
                    "runId": run.run_id,
                    "status": "decision_required",
                    "reason": "unsupported_validator_event",
                    **notification_context,
                }
            verdict = self.validator(run, event.callback_payload())
            if isinstance(verdict, Mapping): verdict = verdict.get("verdict")
            if verdict not in {"accepted", "revision_requested", "decision_required"}: raise ValueError("invalid validator verdict")
            if verdict == "accepted":
                self.transport.complete_conversation_run(self.identity, runId=run.run_id, sanitizedStatusNote=verdict, idempotencyKey=self._key(run.run_id, "complete", attempt))
                return {"runId": run.run_id, "status": "accepted", **notification_context}
            if verdict == "decision_required":
                self.transport.complete_conversation_run(self.identity, runId=run.run_id, sanitizedStatusNote=verdict, idempotencyKey=self._key(run.run_id, "complete", attempt))
                return {"runId": run.run_id, "status": verdict, **notification_context}
            events = self.transport.list_conversation_events(self.identity, conversationId=run.conversation_id).get("events", [])
            revision_count = sum(1 for item in events if isinstance(item, Mapping) and item.get("kind") == "user_message"
                                 and "revision requested" in str(item.get("sanitizedPreview", "")).lower())
            if revision_count < self.max_rounds:
                if not event.author_member_id: raise ValueError("revision target author identity is absent")
                revision = self._append(run, "user_message", "Revision requested by validator", "revision", attempt)
                created = self.transport.create_conversation_run(self.identity, spaceId=run.space_id, conversationId=run.conversation_id,
                    triggeredByEventId=revision.event_id, targetMemberId=event.author_member_id, targetInstanceId=event.author_instance_id,
                    idempotencyKey=self._key(run.run_id, "revision-run", attempt))
                self.transport.complete_conversation_run(self.identity, runId=run.run_id, sanitizedStatusNote=verdict, idempotencyKey=self._key(run.run_id, "complete", attempt))
                return {"runId": run.run_id, "status": verdict, "nextRunId": created.get("run", created).get("runId"), **notification_context}
            self.transport.complete_conversation_run(self.identity, runId=run.run_id, sanitizedStatusNote="decision_required", idempotencyKey=self._key(run.run_id, "complete", attempt))
            return {"runId": run.run_id, "status": "decision_required", **notification_context}
        except Exception as exc:
            try: self.transport.fail_conversation_run(self.identity, runId=run.run_id, sanitizedStatusNote=safe_failure_note(exc), idempotencyKey=self._key(run.run_id, "fail", attempt))
            except Exception: pass
            return {"runId": run.run_id, "status": "failed", "error": sanitize_summary(exc)}
