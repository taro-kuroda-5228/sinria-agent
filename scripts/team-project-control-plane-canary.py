#!/usr/bin/env python3
"""Persist a real metadata-only receipt for live control-plane verification."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sinria_team_project_transport import validate_team_project_metadata


CAPABILITY = "control-plane-canary"


def execute(payload: object, *, evidence_dir: Path) -> dict[str, object]:
    metadata = validate_team_project_metadata(payload)
    if metadata is None or metadata.get("type") != "task_request":
        raise ValueError("task request metadata is required")
    if metadata["capability"] != CAPABILITY:
        raise ValueError("unsupported team capability")

    evidence_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schemaVersion": "sinria.team-project-receipt.v1",
        "dispatchId": metadata["dispatchId"],
        "projectId": metadata["projectId"],
        "taskId": metadata["taskId"],
        "capability": metadata["capability"],
        "attempt": metadata["attempt"],
        "criteria": metadata["acceptanceCriteria"],
        "rawContextStored": False,
        "externalActionPerformed": False,
    }
    receipt_path = evidence_dir / f"{metadata['dispatchId']}.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    evidence_ref = f"local://team-project-evidence/{receipt_path.name}"
    return {
        "summary": "Metadata-only local execution receipt persisted.",
        "evidence": [evidence_ref],
        "criteriaEvidence": {
            criterion: evidence_ref for criterion in metadata["acceptanceCriteria"]
        },
        "verdict": "accepted",
        "externalActionPerformed": False,
    }


def main() -> int:
    evidence_dir = Path(
        os.environ.get(
            "SINRIA_TEAM_EVIDENCE_DIR",
            str(Path.home() / ".sinria" / "team-project-evidence"),
        )
    )
    try:
        payload = json.load(sys.stdin)
        result = execute(payload, evidence_dir=evidence_dir)
    except Exception:
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
