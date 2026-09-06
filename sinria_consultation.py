"""Strict consultation.v1 metadata contract shared by peer runtime and tools."""
from __future__ import annotations
import re
from typing import Any, Mapping

_ID = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
REQUEST_KEYS = {"schemaVersion", "type", "consultationId", "questionSummary", "sourceRefs", "humanDecisionRequired", "allowedOperations", "sensitivity", "rawContextStored", "externalActionPerformed"}
RESPONSE_KEYS = {"schemaVersion", "type", "consultationId", "recommendation", "sourceRefs", "confidence", "assumptions", "dissent", "unresolvedQuestions", "humanDecisionRequired", "allowedOperations", "sensitivity", "rawContextStored", "externalActionPerformed"}

def validate_consultation(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("consultation metadata must be an object")
    if value.get("schemaVersion") == "team-project.v1":
        # The existing Company OS event column is the generic collaboration
        # metadata envelope despite its historical consultation-only name.
        from sinria_team_project_transport import validate_team_project_metadata

        return validate_team_project_metadata(value)
    kind = value.get("type")
    allowed = REQUEST_KEYS if kind == "consultation_request" else RESPONSE_KEYS if kind == "consultation_response" else set()
    if not allowed or set(value) - allowed:
        raise ValueError("unsupported consultation metadata")
    if value.get("schemaVersion") != "consultation.v1" or not _ID.fullmatch(str(value.get("consultationId", ""))):
        raise ValueError("invalid consultation identity")
    key = "questionSummary" if kind == "consultation_request" else "recommendation"
    text = value.get(key)
    if not isinstance(text, str) or not text.strip() or len(text) > 500 or "\n" in text:
        raise ValueError(f"invalid {key}")
    refs = value.get("sourceRefs")
    if not isinstance(refs, list) or not 1 <= len(refs) <= 10:
        raise ValueError("invalid consultation sourceRefs")
    for ref in refs:
        if not isinstance(ref, Mapping) or set(ref) - {"provider", "resourceId", "range", "version", "title"} or ref.get("provider") != "google_workspace":
            raise ValueError("invalid consultation sourceRef")
        if not isinstance(ref.get("resourceId"), str) or not 1 <= len(ref["resourceId"]) <= 180:
            raise ValueError("invalid consultation resourceId")
    if kind == "consultation_response" and (not isinstance(value.get("confidence"), (int, float)) or not 0 <= value["confidence"] <= 1):
        raise ValueError("invalid consultation confidence")
    for name in ("assumptions", "dissent", "unresolvedQuestions"):
        items = value.get(name, [])
        if not isinstance(items, list) or len(items) > 8 or any(not isinstance(x, str) or len(x) > 240 or "\n" in x for x in items):
            raise ValueError(f"invalid consultation {name}")
    if value.get("sensitivity") != "internal" or value.get("rawContextStored") is not False or value.get("externalActionPerformed") is not False:
        raise ValueError("unsafe consultation boundary")
    ops = value.get("allowedOperations")
    if not isinstance(value.get("humanDecisionRequired"), bool) or not isinstance(ops, list) or not ops or any(x not in {"read", "draft"} for x in ops):
        raise ValueError("invalid consultation policy")
    return dict(value)
