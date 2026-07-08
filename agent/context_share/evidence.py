"""Evidence ledger for Sinria Context Share v2."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Literal

from .safety import assert_safe_identifier, assert_sanitized_text

# Kanji/katakana runs only: hiragana is grammar, not content, and bigrams that
# bridge particles (「を確」「認す」) create accidental matches between any two
# sentences. Treating hiragana as a separator keeps bigrams on content words.
_CJK_CONTENT_RUN_RE = re.compile(r"[ァ-ヺー㐀-䶿一-鿿]+")
_ASCII_TERM_RE = re.compile(r"[a-z0-9]{3,}")


def _cjk_bigrams(text_l: str) -> set[str]:
    bigrams: set[str] = set()
    for run in _CJK_CONTENT_RUN_RE.findall(text_l):
        bigrams.update(run[i:i + 2] for i in range(len(run) - 1))
    return bigrams

SourceKind = Literal["user_correction", "decision", "policy", "repeated_failure", "workflow_outcome", "skill_candidate"]
Scope = Literal["personal", "workspace", "org", "project", "app_module"]
Sensitivity = Literal["public", "internal", "confidential", "clinical", "secret_ref"]


class SensitiveContextError(ValueError):
    """Raised when raw sensitive content is about to enter context evidence."""


@dataclass(frozen=True)
class ContextEvidence:
    evidence_id: str
    source_session_id: str
    source_kind: SourceKind
    scope: Scope
    summary: str
    sanitized_sample: str
    sensitivity: Sensitivity
    applies_to: list[str]
    valid_from: str
    confidence: float
    human_approved: bool
    expires_at: str | None = None
    supersedes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.source_session_id:
            raise ValueError("evidence_id and source_session_id are required source pointers")
        assert_safe_identifier(self.evidence_id, field="evidence_id", error_cls=SensitiveContextError)
        assert_safe_identifier(self.source_session_id, field="source_session_id", error_cls=SensitiveContextError)
        if not self.summary.strip():
            raise ValueError("summary is required")
        if not self.applies_to:
            raise ValueError("applies_to must contain at least one scope key")
        assert_sanitized_text(self.summary, field="summary", error_cls=SensitiveContextError)
        assert_sanitized_text(self.sanitized_sample, field="sanitized_sample", error_cls=SensitiveContextError)
        for scope_key in self.applies_to:
            assert_safe_identifier(scope_key, field="applies_to", error_cls=SensitiveContextError)
        if self.supersedes is None:
            object.__setattr__(self, "supersedes", [])
        for superseded in self.supersedes:
            assert_safe_identifier(superseded, field="supersedes", error_cls=SensitiveContextError)
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

    def is_active(self, *, now: datetime | None = None) -> bool:
        if not self.human_approved:
            return False
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        try:
            valid_from = datetime.fromisoformat(self.valid_from.replace("Z", "+00:00"))
        except ValueError:
            return False
        if valid_from.tzinfo is None:
            valid_from = valid_from.replace(tzinfo=timezone.utc)
        if valid_from > current:
            return False
        if not self.expires_at:
            return True
        try:
            expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > current


class EvidenceLedger:
    """Small in-memory evidence ledger used by resolver tests and prompt assembly.

    Durable backing can be added later; the contract keeps raw source content out
    and stores only source pointers plus sanitized summaries.
    """

    def __init__(self, evidence: Iterable[ContextEvidence] | None = None):
        self._items: dict[str, ContextEvidence] = {}
        self._superseded: set[str] = set()
        for item in evidence or []:
            self.add(item)

    def add(self, evidence: ContextEvidence) -> None:
        self._items[evidence.evidence_id] = evidence
        self._superseded.update(evidence.supersedes or [])

    def all(self) -> list[ContextEvidence]:
        return list(self._items.values())

    def active_for(self, key: str) -> list[ContextEvidence]:
        key_l = key.lower()
        active = [
            item for item in self._items.values()
            if item.evidence_id not in self._superseded
            and item.is_active()
            and any(key_l == scope.lower() or key_l in scope.lower() or scope.lower() in key_l for scope in item.applies_to)
        ]
        return sorted(active, key=lambda item: (item.confidence, item.valid_from), reverse=True)

    def search(self, query: str) -> list[ContextEvidence]:
        return [item for _, item in self.search_scored(query)]

    def search_scored(self, query: str) -> list[tuple[float, ContextEvidence]]:
        """Score evidence against the query using ASCII terms plus CJK bigrams.

        Japanese requests are not whitespace-segmented, so term splitting alone
        never matches them; character-bigram overlap restores recall for CJK
        summaries/samples. A single shared bigram is too weak to count as a
        match (min score 1.0 = one ASCII term or two bigrams).
        """
        query_l = query.lower()
        terms = set(_ASCII_TERM_RE.findall(query_l))
        query_bigrams = _cjk_bigrams(query_l)
        scored: list[tuple[float, ContextEvidence]] = []
        for item in self._items.values():
            if item.evidence_id in self._superseded or not item.is_active():
                continue
            haystack = " ".join([item.summary, item.sanitized_sample, " ".join(item.applies_to)]).lower()
            score = float(sum(1 for term in terms if term in haystack))
            if query_bigrams:
                score += 0.5 * len(query_bigrams & _cjk_bigrams(haystack))
            if score >= 1.0:
                scored.append((score, item))
        return sorted(scored, key=lambda pair: (pair[0], pair[1].confidence), reverse=True)
