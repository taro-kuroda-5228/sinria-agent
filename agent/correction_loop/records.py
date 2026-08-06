"""Validated, advisory-only correction records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

_FORBIDDEN_FIELDS = frozenset({
    "action_class",
    "allow",
    "approval_required",
    "block",
    "blocked",
    "deny",
    "effect",
    "enforcement",
    "require",
})


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{field} must be a sequence of non-empty strings")
    return tuple(item.strip() for item in value)


@dataclass(frozen=True)
class CorrectionRecord:
    correction_id: str
    fingerprint: str
    scope: str
    trigger_signature: tuple[str, ...]
    mistake_class: str
    checks: tuple[str, ...]
    fix_steps: tuple[str, ...]
    verification_steps: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    confidence: str
    created_at: str
    superseded_by: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "CorrectionRecord":
        forbidden = _FORBIDDEN_FIELDS.intersection(payload)
        if forbidden:
            names = ", ".join(sorted(forbidden))
            raise ValueError(f"execution-control field is forbidden: {names}")
        scalar_fields = (
            "correction_id", "fingerprint", "scope", "mistake_class",
            "confidence", "created_at",
        )
        values: dict[str, Any] = {}
        for field in scalar_fields:
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
            values[field] = value.strip()
        superseded_by = payload.get("superseded_by")
        if superseded_by is not None and (not isinstance(superseded_by, str) or not superseded_by.strip()):
            raise ValueError("superseded_by must be null or a non-empty string")
        return cls(
            **values,
            trigger_signature=_strings(payload.get("trigger_signature"), "trigger_signature"),
            checks=_strings(payload.get("checks"), "checks"),
            fix_steps=_strings(payload.get("fix_steps"), "fix_steps"),
            verification_steps=_strings(payload.get("verification_steps"), "verification_steps"),
            evidence_refs=_strings(payload.get("evidence_refs"), "evidence_refs"),
            superseded_by=superseded_by.strip() if isinstance(superseded_by, str) else None,
        )
