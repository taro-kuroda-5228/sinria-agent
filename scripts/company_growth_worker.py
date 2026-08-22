#!/usr/bin/env python3
"""One safe, profile-scoped Company Growth Loop worker tick.

Dry-run is the default and never claims a job. Execution is limited to local
state transitions. No network/provider adapter is loaded by this entrypoint.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.company_context.operations import ContextLedger, Lease  # noqa: E402
from agent.company_context.policy import WorkspaceIdentity  # noqa: E402
from agent.company_context.state import LocalSyncState  # noqa: E402
from agent.company_context.store import OwnerMismatchError  # noqa: E402
from agent.company_context.transport import CompanyOsTransport, UrllibJsonClient  # noqa: E402
from sinria_constants import get_sinria_home  # noqa: E402


def default_db() -> Path:
    path = get_sinria_home() / "company-context" / "operations.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _dispatch(
    ledger: ContextLedger,
    lease: Lease,
    job: dict[str, Any],
    *,
    company_os: CompanyOsTransport | None = None,
) -> dict[str, Any]:
    kind = job["kind"]
    payload = job["payload"]
    profile = lease.profile
    if kind == "replay":
        result = ledger.replay(profile, payload["proposal_id"], int(payload["version"]))
    elif kind == "activate":
        result = ledger.activate(
            profile,
            payload["proposal_id"],
            payload["continuation"],
            canary=bool(payload.get("canary", False)),
        )
    elif kind == "rollback":
        result = ledger.rollback(profile)
    elif kind == "purge":
        result = ledger.purge(profile, before=float(payload["before"]))
    elif kind == "jml":
        ledger.jml(profile, payload["member_id"], payload["state"], role=payload.get("role", "member"))
        result = payload["state"]
    elif kind == "retain":
        ledger.retain(profile, payload["subject"], reason=payload.get("reason", "policy"))
        result = "retained"
    elif kind == "opportunity_detect":
        result = ledger.detect_opportunity(profile, payload["fingerprint"])
    elif kind == "opportunity_evidence":
        ledger.add_evidence(profile, payload["opportunity_id"], source=payload["source"], value=payload["value"], version=payload.get("version"))
        result = "recorded"
    elif kind == "opportunity_claim":
        result = ledger.claim_opportunity(profile, payload["opportunity_id"], payload.get("worker", lease.owner))
    elif kind == "status_sync":
        result = ledger.sync_status(profile, payload["subject"], payload["state"], details=payload.get("details"), expected_version=payload.get("expected_version"))
    elif kind == "approval_revoke":
        ledger.revoke_approval(profile, payload["continuation"], actor=payload.get("actor", lease.owner))
        result = "revoked"
    elif kind == "company_os_task":
        if company_os is None:
            raise ValueError("company_os_task requires configured transport")
        base_key = payload["idempotency_key"]
        revision = int(payload.get("revision", 1))
        task = company_os.create(
            task_kind=payload["task_kind"],
            title=payload["title"],
            instruction=payload["instruction"],
            agent_os_id=payload.get("agent_os_id", "company-context"),
            idempotency_key=base_key,
            revision=revision,
            human_approval_required=bool(payload.get("human_approval_required", True)),
        )
        task_id = task["taskId"]
        claim = company_os.claim(task_id, idempotency_key=base_key + ":claim", revision=revision)
        attempt = int((claim.get("claim") or {}).get("attempt", 1))
        company_os.result(
            task_id=task_id,
            agent_os_id=payload.get("agent_os_id", "company-context"),
            task_kind=payload["task_kind"],
            status="waiting_review" if payload.get("human_approval_required", True) else "succeeded",
            summary=payload["summary"],
            result_refs=list(payload.get("result_refs") or []),
            idempotency_key=base_key + ":result",
            revision=revision,
            claim_attempt=attempt,
        )
        readback = company_os.readback(task_id=task_id, idempotency_key=base_key + ":readback")
        if not readback.get("ok"):
            raise RuntimeError("Company OS readback failed")
        result = task_id
    elif kind == "company_os_approval":
        if company_os is None:
            raise ValueError("company_os_approval requires configured transport")
        result = company_os.approval(
            payload["review_id"],
            approve=bool(payload["approve"]),
            idempotency_key=payload["idempotency_key"],
            revision=int(payload["revision"]),
        )
    else:
        raise ValueError(f"unsupported safe local job kind: {kind}")
    return {"kind": kind, "result": result}


def _sync_google_drive(*, ledger: ContextLedger, profile: str, source: Any, checkpoint: Any, store: Any) -> int:
    """Apply synthetic normalized Drive changes, then advance its checkpoint."""
    if getattr(store, "profile_id", None) != profile:
        raise OwnerMismatchError("injected store profile mismatch")
    cursor = getattr(checkpoint, "value", getattr(checkpoint, "cursor", None))
    response = source.changes_since(cursor)
    if isinstance(response, list):
        changes, next_token = response, None
    elif isinstance(response, dict):
        changes = response.get("changes", [])
        next_token = response.get("next_token", response.get("new_start_page_token"))
    else:
        raise ValueError("invalid Drive changes response")
    if not isinstance(changes, list):
        raise ValueError("invalid Drive changes")
    applied = 0
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("invalid normalized Drive change")
        change_id = str(change.get("change_id", ""))
        safe_change_id = "drive-change-" + hashlib.sha256(change_id.encode()).hexdigest()
        owner_id = change.get("owner_id")
        doc_id = change.get("doc_id")
        text = change.get("text")
        if not change_id or not owner_id or not doc_id or not isinstance(text, str):
            raise ValueError("invalid normalized Drive change")
        if owner_id != profile:
            raise OwnerMismatchError("Drive change owner mismatch")
        prior = ledger.db.execute(
            "SELECT 1 FROM context_events WHERE profile=? AND kind='google_drive_context_stored' AND idempotency_key=?",
            (profile, safe_change_id),
        ).fetchone()
        if prior:
            continue
        metadata = change.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("invalid normalized Drive metadata")
        metadata = {key: value for key, value in metadata.items() if key in {"title", "mime_type", "modified_at"}}
        store.put(doc_id, owner_id, text, metadata, source="google-drive")
        ledger._event(
            profile,
            "google_drive_context_stored",
            safe_change_id,
            {"content_hash": hashlib.sha256(text.encode()).hexdigest()},
            safe_change_id,
        )
        applied += 1
    if next_token is not None:
        if hasattr(checkpoint, "advance"):
            checkpoint.advance(str(next_token))
        elif hasattr(checkpoint, "save"):
            checkpoint.save(str(next_token))
        else:
            raise ValueError("unsupported Drive checkpoint")
    return applied


def run_tick(*, profile: str, db_path: Path, owner: str, execute: bool = False,
             drive_source: Any = None, checkpoint: Any = None, store: Any = None,
             company_os: CompanyOsTransport | None = None) -> dict[str, Any]:
    ledger = ContextLedger(db_path)
    try:
        if not execute:
            queued = ledger.db.execute(
                "SELECT count(*) FROM context_jobs WHERE profile=? AND status IN ('queued','retry')",
                (profile,),
            ).fetchone()[0]
            return {
                "ok": True,
                "mode": "dry-run",
                "queued": queued,
                "rawContextStored": False,
                "rawLocatorStored": False,
                "credentialStored": False,
            }
        lease = ledger.claim(profile, owner)
        if lease is None:
            return {"ok": True, "mode": "execute-local", "processed": 0,
                    "rawContextStored": False, "rawLocatorStored": False, "credentialStored": False}
        try:
            job = ledger.job(lease)
            # Refresh the durable lease immediately before provider/local work;
            # another process can reclaim only after expiry.
            lease = ledger.heartbeat(lease)
            if job["kind"] == "google_drive_sync":
                if drive_source is None or checkpoint is None or store is None:
                    raise ValueError("google_drive_sync requires injected local dependencies")
                outcome = {"kind": job["kind"], "result": _sync_google_drive(
                    ledger=ledger, profile=profile, source=drive_source, checkpoint=checkpoint, store=store,
                )}
            else:
                outcome = _dispatch(ledger, lease, job, company_os=company_os)
            ledger.finish(lease)
            return {
                "ok": True,
                "mode": "execute-local",
                "processed": 1,
                "jobId": lease.job_id,
                "kind": outcome["kind"],
                "rawContextStored": False,
                "rawLocatorStored": False,
                "credentialStored": False,
            }
        except Exception as exc:
            ledger.finish(lease, status="retry", error=type(exc).__name__)
            return {
                "ok": False,
                "mode": "execute-local",
                "processed": 0,
                "jobId": lease.job_id,
                "errorType": type(exc).__name__,
                "rawContextStored": False,
                "rawLocatorStored": False,
                "credentialStored": False,
            }
    finally:
        ledger.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--db", type=Path, default=default_db())
    parser.add_argument("--owner", default=f"{socket.gethostname()}:company-growth-worker")
    parser.add_argument("--execute-local", action="store_true")
    parser.add_argument("--company-os-sync", action="store_true")
    parser.add_argument("--company-os-url", default=os.getenv("SINRIA_COMPANY_OS_URL", ""))
    parser.add_argument("--workspace-id", default=os.getenv("SINRIA_COMPANY_CONTEXT_WORKSPACE", ""))
    parser.add_argument("--member-id", default=os.getenv("SINRIA_COMPANY_CONTEXT_MEMBER", ""))
    parser.add_argument("--instance-id", default=os.getenv("SINRIA_COMPANY_CONTEXT_INSTANCE", ""))
    args = parser.parse_args()
    company_os = None
    if args.company_os_sync:
        bridge_credential = os.environ.get("SINRIA_COMPANY_OS_BRIDGE_TOKEN", "")
        if not all((args.company_os_url, args.workspace_id, args.member_id, args.instance_id, bridge_credential)):
            parser.error("Company OS sync requires URL, bridge identity, and keychain/env bridge credential")
        company_os = CompanyOsTransport(
            args.company_os_url,
            identity=WorkspaceIdentity(args.workspace_id, args.member_id, args.instance_id),
            bridge_token=bridge_credential,
            state=LocalSyncState(args.db.with_suffix(".transport.json")),
            http=UrllibJsonClient(),
        )
    receipt = run_tick(
        profile=args.profile,
        db_path=args.db,
        owner=args.owner,
        execute=args.execute_local,
        company_os=company_os,
    )
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
