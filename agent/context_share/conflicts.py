"""Conflict detection and resolution for Context Share evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Iterable

from .evidence import ContextEvidence


@dataclass(frozen=True)
class EvidenceConflict:
    winning_evidence_id: str
    losing_evidence_id: str
    reason: str

    def format_for_prompt(self) -> str:
        return f"{self.winning_evidence_id} wins over {self.losing_evidence_id}: {self.reason}"


def _overlaps(a: ContextEvidence, b: ContextEvidence) -> bool:
    a_keys = {key.lower() for key in a.applies_to}
    b_keys = {key.lower() for key in b.applies_to}
    return bool(a_keys & b_keys)


def _identity_stance(text: str) -> str | None:
    text_l = text.lower()
    if "sinria-native" in text_l or "avoid hermes" in text_l or "hermes residue" in text_l:
        return "sinria"
    if "use hermes" in text_l or "hermes labels" in text_l or "hermes paths" in text_l:
        return "hermes"
    return None


def _known_contradiction(a: str, b: str) -> bool:
    a_l = a.lower()
    b_l = b.lower()
    a_identity = _identity_stance(a)
    b_identity = _identity_stance(b)
    if a_identity and b_identity and a_identity != b_identity:
        return True
    contradiction_pairs = [
        ("ai-only", "human+ai"),
        ("interview-first", "observation-first"),
        ("store raw/private context", "metadata-only"),
    ]
    return any((left in a_l and right in b_l) or (right in a_l and left in b_l) for left, right in contradiction_pairs)


_OVERRIDE_MARKERS = (
    "instead of",
    "rather than",
    "do not",
    "don't",
    "must not",
    "no longer",
    "now uses",
    "now use",
    "from now on",
    "must use",
    "禁止",
    "しない",
    "しないで",
    "使わない",
    "ではなく",
    "じゃなくて",
    "今後は",
    "必ず",
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:/-]{2,}")
_CJK_KEY_RE = re.compile(r"[ァ-ヺー㐀-䶿一-鿿]{2,}")
_NEGATION_WINDOW = 80


def _parse_ts(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _meaningful_terms(text: str) -> set[str]:
    text_l = text.lower()
    terms = set(_TOKEN_RE.findall(text_l))
    terms.update(_CJK_KEY_RE.findall(text_l))
    stop = {
        "the", "and", "for", "with", "through", "temporary", "primary",
        "work", "route", "runtime", "policy", "correction", "guidance",
        "users", "uses", "use", "must", "should", "instead", "rather",
        "from", "now", "メドエビデンス", "実装", "作業", "方針",
    }
    return {term.strip(".,;:()[]{}\"'") for term in terms if term not in stop and len(term) >= 3}


def _contains_override_marker(text_l: str) -> bool:
    return any(marker in text_l for marker in _OVERRIDE_MARKERS)


def _contains_current_turn_override_marker(text_l: str) -> bool:
    if "do not forget" in text_l or "don't forget" in text_l:
        return False
    markers = (
        "instead of", "rather than", "must not", "no longer", "from now on",
        "must use", "do not route", "do not import", "do not dispatch", "do not mutate",
        "禁止", "使わない", "ではなく", "じゃなくて", "今後は", "必ず", "routeしない",
        "importしない", "dispatchしない", "mutateしない",
    )
    return any(marker in text_l for marker in markers)


def _term_in_negated_region(term: str, text_l: str) -> bool:
    for marker in _OVERRIDE_MARKERS:
        start = text_l.find(marker)
        while start != -1:
            region = text_l[start:start + _NEGATION_WINDOW]
            if term in region:
                return True
            start = text_l.find(marker, start + 1)
    return False


def _decision_override(newer: ContextEvidence, older: ContextEvidence) -> bool:
    """Return True when a newer correction generically replaces older guidance.

    Startup operating rules often change without a hand-authored ``supersedes``
    list.  When two approved evidence rows overlap by scope and the newer row
    contains explicit replacement/prohibition language that mentions important
    tokens from the older row, suppress the older row.  This is intentionally
    conservative: no override marker, no shared old target term → no conflict.
    """
    if _parse_ts(newer.valid_from) <= _parse_ts(older.valid_from):
        return False
    text_l = newer.summary.lower()
    if not _contains_override_marker(text_l):
        return False
    older_terms = _meaningful_terms(older.summary)
    newer_terms = _meaningful_terms(newer.summary)
    shared = older_terms & newer_terms
    if not shared:
        return False
    if any(_term_in_negated_region(term, text_l) for term in shared):
        return True
    if any(marker in text_l for marker in ("instead of", "rather than", "ではなく", "じゃなくて")) and shared:
        return True
    return False


def evidence_overridden_by_current_request(evidence: ContextEvidence, current_request: str) -> bool:
    """Return True if this turn explicitly replaces a stale durable constraint.

    The current user message is already part of the conversation, but old
    durable evidence can still pollute the resolver block.  This function uses
    the current request only as an in-memory suppression signal and never emits
    the raw request text.  It is stricter than durable-vs-durable override to
    avoid treating reminders such as "do not forget browser smoke" as a repeal.
    """
    text_l = (current_request or "").lower()
    if not _contains_current_turn_override_marker(text_l):
        return False
    old_terms = _meaningful_terms(evidence.summary)
    current_terms = _meaningful_terms(text_l)
    shared = old_terms & current_terms
    if not shared:
        return False
    return any(_term_in_negated_region(term, text_l) for term in shared) or any(
        marker in text_l for marker in ("instead of", "rather than", "ではなく", "じゃなくて")
    )


def _winner(a: ContextEvidence, b: ContextEvidence) -> ContextEvidence:
    if a.evidence_id in (b.supersedes or []):
        return b
    if b.evidence_id in (a.supersedes or []):
        return a
    if _decision_override(a, b):
        return a
    if _decision_override(b, a):
        return b
    return max((a, b), key=lambda item: (item.confidence, item.valid_from, item.evidence_id))


def detect_evidence_conflicts(evidence: Iterable[ContextEvidence]) -> list[EvidenceConflict]:
    items = [item for item in evidence if item.is_active()]
    conflicts: list[EvidenceConflict] = []
    for idx, left in enumerate(items):
        for right in items[idx + 1:]:
            if not _overlaps(left, right):
                continue
            explicit_contradiction = _known_contradiction(left.summary, right.summary)
            override = _decision_override(left, right) or _decision_override(right, left)
            if not explicit_contradiction and not override:
                continue
            winning = _winner(left, right)
            losing = right if winning is left else left
            conflicts.append(EvidenceConflict(
                winning_evidence_id=winning.evidence_id,
                losing_evidence_id=losing.evidence_id,
                reason="newer/higher-confidence prior correction supersedes contradictory guidance",
            ))
    return conflicts


def resolve_non_conflicting_evidence(evidence: Iterable[ContextEvidence]) -> tuple[list[ContextEvidence], list[EvidenceConflict]]:
    items = [item for item in evidence if item.is_active()]
    explicit_superseded = {old for item in items for old in (item.supersedes or [])}
    items = [item for item in items if item.evidence_id not in explicit_superseded]
    conflicts = detect_evidence_conflicts(items)
    losing_ids = {conflict.losing_evidence_id for conflict in conflicts}
    active = [item for item in items if item.evidence_id not in losing_ids]
    active = sorted(active, key=lambda item: (item.confidence, item.valid_from, item.evidence_id), reverse=True)
    return active, conflicts
