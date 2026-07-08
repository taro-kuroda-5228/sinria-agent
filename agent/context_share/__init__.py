"""Context Share v2 primitives for Sinria intent continuity.

This package turns past corrections and institutional policy into bounded,
searchable context that can be injected before action without leaking raw
confidential content into shared surfaces.
"""

from .evidence import ContextEvidence, EvidenceLedger, SensitiveContextError
from .extraction import EvidenceCandidate, discover_session_evidence_candidates, extract_candidates_from_messages
from .intent_resolver import IntentResolver, IntentResolution, build_context_resolver_prompt, build_context_resolver_fallback_prompt
from .loop_metrics import GapRecurrence, LoopStatus, candidate_id_for_record, compute_loop_status
from .outcome_gap import PracticalOutcomeRecord, apply_practical_completion_guard, assess_practical_outcome, record_practical_outcome_and_candidates
from .review_queue import approve_candidate, load_review_candidates, write_review_candidates

__all__ = [
    "ContextEvidence",
    "EvidenceCandidate",
    "EvidenceLedger",
    "GapRecurrence",
    "LoopStatus",
    "SensitiveContextError",
    "IntentResolver",
    "IntentResolution",
    "PracticalOutcomeRecord",
    "approve_candidate",
    "apply_practical_completion_guard",
    "assess_practical_outcome",
    "build_context_resolver_prompt",
    "build_context_resolver_fallback_prompt",
    "candidate_id_for_record",
    "compute_loop_status",
    "discover_session_evidence_candidates",
    "extract_candidates_from_messages",
    "load_review_candidates",
    "record_practical_outcome_and_candidates",
    "write_review_candidates",
]
