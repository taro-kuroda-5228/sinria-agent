"""Self-improvement candidate generation for Context Share v2."""

from __future__ import annotations

from dataclasses import dataclass

from .evidence import ContextEvidence


@dataclass(frozen=True)
class ImprovementCandidate:
    action: str
    rationale: str
    evidence_id: str
    human_review_required: bool


class ImprovementCandidateBuilder:
    def from_violation(self, *, violated_evidence: ContextEvidence, observed_action: str, repeat_count: int = 1) -> list[ImprovementCandidate]:
        high_risk = violated_evidence.sensitivity in {"confidential", "clinical", "secret_ref"} or violated_evidence.scope in {"org", "workspace"}
        actions = ["memory_replace", "skill_patch"]
        if repeat_count >= 2:
            actions.extend(["test_addition", "runbook_update"])
        elif high_risk:
            actions.append("runbook_update")
        candidates: list[ImprovementCandidate] = []
        for action in dict.fromkeys(actions):
            review = high_risk or action in {"test_addition", "runbook_update"} and high_risk
            candidates.append(ImprovementCandidate(
                action=action,
                rationale=(
                    f"Observed action violated prior correction '{violated_evidence.summary}'. "
                    f"Observed: {observed_action[:160]}"
                ),
                evidence_id=violated_evidence.evidence_id,
                human_review_required=review,
            ))
        return candidates
