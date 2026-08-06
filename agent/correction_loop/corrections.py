"""Promotion of durable corrections into active constraints."""

from __future__ import annotations

from dataclasses import dataclass

from .evidence import ContextEvidence

_STALE_MARKERS = ("PR #", "commit ", "phase ", "merged", "done", "completed", "SHA", "issue #")


@dataclass(frozen=True)
class CorrectionConstraint:
    evidence_id: str
    constraint_text: str
    applies_to: list[str]
    priority: int
    human_approved: bool


class CorrectionRegistry:
    def __init__(self) -> None:
        self._constraints: dict[str, CorrectionConstraint] = {}
        self._superseded: set[str] = set()
        self._repeat_counts: dict[str, int] = {}

    def promote(self, evidence: ContextEvidence) -> CorrectionConstraint | None:
        if evidence.source_kind not in {"user_correction", "decision", "policy", "repeated_failure"}:
            return None
        if not evidence.is_active():
            return None
        if self._looks_stale(evidence.summary):
            return None
        self._superseded.update(evidence.supersedes or [])
        key = "|".join(sorted(evidence.applies_to)) + "::" + evidence.summary.strip().lower()
        self._repeat_counts[key] = self._repeat_counts.get(key, 0) + 1
        constraint = CorrectionConstraint(
            evidence_id=evidence.evidence_id,
            constraint_text=evidence.summary.strip(),
            applies_to=list(evidence.applies_to),
            priority=int(evidence.confidence * 100) + self._repeat_counts[key] * 10,
            human_approved=evidence.human_approved,
        )
        self._constraints[evidence.evidence_id] = constraint
        return constraint

    def active_constraints_for(self, key: str) -> list[CorrectionConstraint]:
        key_l = key.lower()
        active = [
            constraint for constraint in self._constraints.values()
            if constraint.evidence_id not in self._superseded
            and any(key_l == scope.lower() or key_l in scope.lower() or scope.lower() in key_l for scope in constraint.applies_to)
        ]
        return sorted(active, key=lambda constraint: constraint.priority, reverse=True)

    @staticmethod
    def _looks_stale(text: str) -> bool:
        text_l = text.lower()
        return any(marker.lower() in text_l for marker in _STALE_MARKERS)
