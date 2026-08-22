from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from agent.correction_loop.extraction import EvidenceCandidate

from .policy import WorkspaceIdentity

_FORBIDDEN_KEYS = {
    "body", "rawbody", "content", "transcript", "messages", "subject",
    "recipient", "recipients", "email", "threadid", "messageid",
    "accesstoken", "refreshtoken", "token", "password", "credential",
    "patientname", "patientid", "phi", "pii",
}
_FALSE_ONLY = {
    "rawcontextstored", "rawevidencestored", "rawsourcestored",
    "rawmediastored", "rawprocedurebodystored", "patientdatastored",
    "externalactionperformed",
}
_SAFE_TEXT = re.compile(r"^[^@\n\r]{1,500}$")


def _normalized(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def validate_metadata_only(value: Any, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = _normalized(str(key))
            if normalized in _FORBIDDEN_KEYS:
                raise ValueError(f"raw or sensitive field forbidden at {path}.{key}")
            if normalized in _FALSE_ONLY and child is not False:
                raise ValueError(f"{path}.{key} must be false")
            validate_metadata_only(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_metadata_only(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if len(value) > 500 or not _SAFE_TEXT.match(value):
            raise ValueError(f"unsafe metadata text at {path}")


def validate_metadata_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a metadata-only transport payload."""
    if not isinstance(payload, dict):
        raise ValueError("metadata payload must be an object")
    validate_metadata_only(payload)
    return payload


def _stable_key(candidate_id: str, workspace_id: str) -> str:
    digest = hashlib.sha256(f"{workspace_id}:{candidate_id}".encode()).hexdigest()[:24]
    return f"sinria-org-{digest}"


def candidate_payloads(candidate: EvidenceCandidate, identity: WorkspaceIdentity) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = candidate.evidence
    allowed_targets = {
        "company_os", "correction_loop", "memory", "skill", "test", "runbook",
        "code", "operations", "sales", "service", "product", "compliance_safety",
    }
    targets = sorted({target for target in evidence.applies_to if target in allowed_targets})
    key = _stable_key(candidate.candidate_id, identity.workspace_id)
    observation_ref = f"obs-{key}"
    observation = {
        "kind": "observation",
        "workspaceId": identity.workspace_id,
        "observedByMemberId": identity.member_id,
        "observedByInstanceId": identity.instance_id,
        "sourceKind": "outcome",
        "domain": "operations",
        "sanitizedSummary": "A local outcome gap produced a review-gated organizational improvement candidate.",
        "outcomeSignal": "quality_improved",
        "sourceRefs": [],
        "rawContextStored": False,
        "rawSourceStored": False,
        "rawMediaStored": False,
        "patientDataStored": False,
        "externalActionPerformed": False,
    }
    asset = {
        "kind": "candidate",
        "workspaceId": identity.workspace_id,
        "proposedByMemberId": identity.member_id,
        "proposedByInstanceId": identity.instance_id,
        "assetKind": "good_practice",
        "title": f"Organizational improvement {key[-8:]}",
        "sanitizedPattern": "Repeated local outcome gap; inspect evidence on the originating Sinria instance.",
        "evidenceSummary": "Local evidence exists and remains on-device; Company OS stores metadata only.",
        "confidence": "high" if evidence.confidence >= 0.85 else "medium",
        "reuseTargets": targets,
        "sourceObservationIds": [observation_ref],
        "idempotencyKey": key,
        "humanApprovalRequired": True,
        "rawContextStored": False,
        "rawEvidenceStored": False,
        "rawSourceStored": False,
        "rawProcedureBodyStored": False,
        "externalActionPerformed": False,
    }
    validate_metadata_only(observation)
    validate_metadata_only(asset)
    # Make accidental unserializable values fail before any transport.
    json.dumps((observation, asset), sort_keys=True)
    return observation, asset
