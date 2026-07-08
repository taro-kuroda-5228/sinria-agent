"""Durable local evidence loading for Sinria Context Share v2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from hermes_constants import get_sinria_home

from .evidence import ContextEvidence, SensitiveContextError

EVIDENCE_RELATIVE_PATH = Path("context_share") / "evidence.jsonl"


def evidence_store_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / EVIDENCE_RELATIVE_PATH


def load_evidence_jsonl(path: Path) -> list[ContextEvidence]:
    if not path.exists():
        return []
    evidence: list[ContextEvidence] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            data = json.loads(stripped)
            evidence.append(ContextEvidence(**data))
        except (TypeError, ValueError, json.JSONDecodeError, SensitiveContextError) as exc:
            raise ValueError(f"Invalid Context Share evidence at {path}:{line_no}: {exc}") from exc
    return evidence


def load_durable_evidence(*, home: Path | None = None, path: Path | None = None) -> list[ContextEvidence]:
    return load_evidence_jsonl(path or evidence_store_path(home))


def append_evidence_jsonl(items: Iterable[ContextEvidence], *, path: Path | None = None) -> Path:
    target = path or evidence_store_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item.__dict__, ensure_ascii=False, sort_keys=True) + "\n")
    return target
