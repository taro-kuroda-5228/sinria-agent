from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.correction_loop.review_queue import approve_candidate, reject_candidate


def apply_review_readback(
    *,
    candidate_id: str,
    remote_asset: dict[str, Any],
    queue_path: Path,
    evidence_path: Path | None = None,
):
    """Apply a human Company OS decision to the local review queue.

    This changes local approval metadata only. Durable skill/code promotion and all
    external actions remain separate review-gated workflows.
    """
    if remote_asset.get("externalActionPerformed") is not False:
        raise ValueError("external action must not be performed by review readback")
    if remote_asset.get("rawEvidenceStored") not in (None, False):
        raise ValueError("raw evidence storage violates the cloud boundary")
    reviewer = remote_asset.get("reviewedByMemberId")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("human reviewer identity required")
    status = remote_asset.get("status")
    reviewed_at = remote_asset.get("reviewedAt")
    if status == "validated":
        destination = evidence_path or queue_path.with_name(f"{queue_path.stem}.approved.jsonl")
        return approve_candidate(
            candidate_id,
            queue_path=queue_path,
            evidence_path=destination,
            reviewer=reviewer,
            approved_at=reviewed_at,
        )
    if status == "rejected":
        return reject_candidate(
            candidate_id,
            queue_path=queue_path,
            reviewer=reviewer,
            rejected_at=reviewed_at,
        )
    raise ValueError(f"remote asset is not decided: {status}")
