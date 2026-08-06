"""Fail-open retrieval of advisory correction checks."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Callable, Iterable

from .records import CorrectionRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CorrectionAdvice:
    correction_id: str
    checks: tuple[str, ...]
    fixes: tuple[str, ...]
    verification: tuple[str, ...]


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[a-zA-Z0-9_./:-]{3,}", text.lower())}


def retrieve_advice(
    current_request: str,
    *,
    loader: Callable[[], Iterable[CorrectionRecord]] = tuple,
    limit: int = 6,
) -> tuple[CorrectionAdvice, ...]:
    """Return relevant advice; correction infrastructure can never stop work."""
    try:
        requested = _terms(current_request)
        ranked: list[tuple[int, CorrectionRecord]] = []
        for record in loader():
            if record.superseded_by:
                continue
            score = len(requested.intersection(_terms(" ".join(record.trigger_signature))))
            if score:
                ranked.append((score, record))
        ranked.sort(key=lambda item: (-item[0], item[1].correction_id))
        return tuple(
            CorrectionAdvice(
                correction_id=record.correction_id,
                checks=record.checks,
                fixes=record.fix_steps,
                verification=record.verification_steps,
            )
            for _, record in ranked[: max(0, limit)]
        )
    except Exception as exc:
        logger.warning("Correction advice unavailable; continuing without it: %s", type(exc).__name__)
        return ()
