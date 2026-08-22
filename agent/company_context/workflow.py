from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from agent.correction_loop.review_queue import load_review_candidates

from .client import CompanyOsKnowledgeClient, ProposalResult
from .readback import apply_review_readback
from .state import ReceiptLedger


def sync_review_queue(
    queue_path: Path,
    client: CompanyOsKnowledgeClient,
    *,
    dry_run: bool = False,
) -> list[ProposalResult]:
    """Propose every still-local review candidate; confirmed receipts deduplicate."""
    results: list[ProposalResult] = []
    for candidate in load_review_candidates(path=queue_path):
        if candidate.approval_state == "proposed":
            results.append(client.propose(candidate, dry_run=dry_run))
    return results


def apply_remote_reviews(
    queue_path: Path,
    ledger: ReceiptLedger,
    remote_assets: Iterable[dict[str, Any]],
) -> list[str]:
    """Apply only final reviewed assets correlated by a local receipt."""
    applied: list[str] = []
    for asset in remote_assets:
        status = asset.get("status")
        if status not in {"validated", "rejected"}:
            continue
        remote_id = asset.get("assetId")
        if not isinstance(remote_id, str):
            continue
        receipt = ledger.find_by_remote_id(remote_id)
        if receipt is None or not receipt.candidate_id or receipt.status != "confirmed":
            continue
        apply_review_readback(
            candidate_id=receipt.candidate_id,
            remote_asset=asset,
            queue_path=queue_path,
        )
        applied.append(receipt.candidate_id)
    return applied
