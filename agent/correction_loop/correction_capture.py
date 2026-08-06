"""Open-world sanitized correction capture for Sinria Correction Loop v2.

The fixed extraction rules in `extraction.py` can only ever re-emit their four
pre-written summaries, so genuinely new user corrections never became durable
memory. This module captures the correction *sentence itself* — bounded,
deterministically detected, sanitization-checked — as a review-gated candidate.

Safety contract:
- Only a single bounded excerpt (≤160 chars) of the user's own message is kept,
  and only if it passes `contains_sensitive_text` screening; otherwise the
  capture is dropped entirely (nothing raw is stored on rejection).
- Candidates are always `human_approved=False` and carry a dedicated
  `extraction_reason` that `auto_triage` refuses to auto-approve, so freeform
  text reaches the durable evidence store only through explicit human review.
- `applies_to` keys come from the same `derive_topic_keys` space the resolver
  queries, so an approved correction is retrievable for related future requests.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence import ContextEvidence
from .extraction import EvidenceCandidate
from .topics import derive_topic_keys
from .review_queue import append_candidate_deduplicated
from agent.privacy.sanitization import contains_sensitive_text

EXTRACTION_REASON = "sanitized_correction_capture_v1"

_MAX_EXCERPT_CHARS = 160
_MIN_EXCERPT_CHARS = 6
_MAX_SCANNED_CHARS = 4000

# High-precision durable-correction markers. Broad politeness forms
# (「してください」 etc.) are deliberately excluded: they mark requests, not
# corrections, and would flood the review queue.
_CORRECTION_MARKERS = (
    "ではなく",
    "じゃなくて",
    "今後は",
    "必ず",
    "しないで",
    "やめて",
    "二度と",
    "禁止",
    "常に",
    "絶対に",
    "instead of",
    "from now on",
    "always ",
    "never ",
    "do not ",
    "don't ",
    "stop doing",
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。．.!?！？\n])")


def _safe_digest(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return digest.translate(str.maketrans("0123456789", "abcdefghij"))


def _correction_sentence(text: str) -> str | None:
    for sentence in _SENTENCE_SPLIT_RE.split(text[:_MAX_SCANNED_CHARS]):
        stripped = sentence.strip()
        if len(stripped) < _MIN_EXCERPT_CHARS:
            continue
        lowered = stripped.lower()
        if any(marker in stripped or marker in lowered for marker in _CORRECTION_MARKERS):
            return stripped[:_MAX_EXCERPT_CHARS]
    return None


def extract_correction_candidate(
    user_message: Any,
    *,
    session_id: str | None,
    project: str | None = None,
    now: datetime | None = None,
) -> EvidenceCandidate | None:
    """Return a review-gated candidate for a durable-looking user correction.

    Returns None (storing nothing) when no marker matches, the excerpt is out
    of bounds, or the excerpt fails sanitization screening.
    """
    text = user_message if isinstance(user_message, str) else str(user_message or "")
    if not text.strip():
        return None
    excerpt = _correction_sentence(text)
    if excerpt is None:
        return None
    summary = f"Prior user correction: {excerpt}"
    if contains_sensitive_text(summary):
        return None
    session = re.sub(r"[^A-Za-z0-9_.:-]", "-", session_id or "unknown-session")[:96] or "unknown-session"
    ts = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    # Class identity comes from the excerpt alone: the same correction repeated
    # in other sessions dedups onto one row whose occurrence_count grows.
    cid = f"ctx-candidate-corr-{_safe_digest(excerpt)}"
    applies_to = list(dict.fromkeys([*derive_topic_keys(text.lower(), project=project), "user_correction_capture"]))
    return EvidenceCandidate(
        candidate_id=cid,
        evidence=ContextEvidence(
            evidence_id=cid.replace("ctx-candidate-", "ctx-ev-"),
            source_session_id=session,
            source_kind="user_correction",
            scope="personal",
            summary=summary,
            sanitized_sample=excerpt[:80],
            sensitivity="internal",
            applies_to=applies_to,
            valid_from=ts,
            confidence=0.8,
            human_approved=False,
        ),
        approval_state="proposed",
        raw_context_stored=False,
        external_action_performed=False,
        extraction_reason=EXTRACTION_REASON,
    )


def record_correction_candidate(
    user_message: Any,
    *,
    session_id: str | None,
    project: str | None = None,
    review_queue_path: Path | None = None,
    now: datetime | None = None,
) -> EvidenceCandidate | None:
    candidate = extract_correction_candidate(user_message, session_id=session_id, project=project, now=now)
    if candidate is None:
        return None
    return append_candidate_deduplicated(candidate, path=review_queue_path)
