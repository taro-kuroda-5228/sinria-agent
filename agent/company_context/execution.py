"""Executable M0-M2 company-context workflow.

The production adapters and this synthetic provider share the same boundaries: an
owner-bound OAuth grant, policy/egress/audit checks, encrypted local retrieval,
and approval-gated Gmail.  The provider is intentionally deterministic and never
contains credentials; it is suitable for workflow tests and local dry runs.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .audit import ImmutableAudit
from .data_policy import Classification, allow_egress
from .gmail import GmailPrivateSignal
from .drive import Checkpoint, DriveChangesConnector
from .retriever import ContextProvider
from .store import EncryptedLocalStore, KeyProvider


class OAuthLifecycle:
    """Minimal authorization-code + PKCE lifecycle with scope escalation/revoke."""
    def __init__(self, *, owner_id: str, client_id: str = "sinria-synthetic"):
        self.owner_id, self.client_id = owner_id, client_id
        self._pending: dict[str, tuple[str, frozenset[str]]] = {}
        self._grants: dict[str, dict[str, Any]] = {}

    def begin(self, scopes: set[str], *, verifier: str | None = None) -> dict[str, str]:
        if not scopes: raise ValueError("scope required")
        state, verifier = secrets.token_urlsafe(18), verifier or secrets.token_urlsafe(32)
        challenge = hashlib.sha256(verifier.encode()).hexdigest()
        self._pending[state] = (challenge, frozenset(scopes))
        return {"state": state, "code_challenge": challenge, "code_challenge_method": "S256"}

    def callback(self, *, state: str, verifier: str, code: str) -> dict[str, Any]:
        try: challenge, scopes = self._pending.pop(state)
        except KeyError as exc: raise PermissionError("invalid OAuth state") from exc
        if hashlib.sha256(verifier.encode()).hexdigest() != challenge or not code:
            raise PermissionError("PKCE verification failed")
        grant = {"owner_id": self.owner_id, "scopes": scopes, "refresh_generation": 1, "revoked": False}
        self._grants[self.owner_id] = grant
        return dict(grant)

    def escalate(self, scopes: set[str]) -> dict[str, Any]:
        grant = self.require()
        if not scopes - set(grant["scopes"]): return dict(grant)
        # Escalation is a new consent transaction, not an implicit grant.
        grant["pending_scopes"] = frozenset(set(grant["scopes"]) | scopes)
        return dict(grant)

    def complete_escalation(self) -> dict[str, Any]:
        grant = self.require()
        pending = grant.pop("pending_scopes", None)
        if pending is None: raise PermissionError("scope escalation not pending")
        grant["scopes"] = pending
        grant["refresh_generation"] += 1
        return dict(grant)

    def rotate(self) -> int:
        grant = self.require(); grant["refresh_generation"] += 1; return grant["refresh_generation"]

    def revoke(self) -> None:
        grant = self._grants.get(self.owner_id)
        if grant: grant["revoked"] = True

    def require(self, scope: str | None = None) -> dict[str, Any]:
        grant = self._grants.get(self.owner_id)
        if not grant or grant["revoked"] or (scope and scope not in grant["scopes"]):
            raise PermissionError("OAuth grant unavailable")
        return grant


@dataclass
class SyntheticDrive:
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    changes: list[dict[str, Any]] = field(default_factory=list)
    token: int = 0
    revoked: bool = False

    def bootstrap(self, file_id: str, text: str, *, revision: str = "1") -> None:
        self.files[file_id] = {"id": file_id, "name": file_id, "text": text, "revision": revision, "trashed": False}
        self.changes.append({"change_id": f"c-{len(self.changes)+1}", "file_id": file_id, "file": self.files[file_id].copy()})

    def change(self, file_id: str, text: str, revision: str) -> None:
        self.bootstrap(file_id, text, revision=revision)

    def revoke_file(self, file_id: str) -> None:
        self.files.pop(file_id, None)
        self.changes.append({"change_id": f"c-{len(self.changes)+1}", "file_id": file_id, "removed": True})

    def list_changes(self, page_token: str | None, page_size: int) -> dict[str, Any]:
        if self.revoked: return {"status": 403}
        start = int(page_token or 0)
        rows = self.changes[start:start + page_size]
        return {"changes": rows, "new_start_page_token": str(start + len(rows))}


class SyntheticGmail:
    def __init__(self): self.sent: dict[str, dict[str, Any]] = {}
    def send(self, *, owner_id: str, message: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        self.sent.setdefault(idempotency_key, {"idempotency_key": idempotency_key, "message_id": f"m-{len(self.sent)+1}", "owner_id": owner_id, "message": message})
        return {"status": "sent", "message_id": self.sent[idempotency_key]["message_id"]}
    def readback(self, *, owner_id: str, idempotency_key: str) -> dict[str, Any] | None:
        row = self.sent.get(idempotency_key)
        return row if row and row["owner_id"] == owner_id else None


@dataclass
class TeamKnowledge:
    """CAS-backed manifest facade used by the Company OS API/UI boundary."""
    manifest: dict[str, dict[str, Any]] = field(default_factory=dict)
    cas: dict[str, str] = field(default_factory=dict)
    revoked: set[str] = field(default_factory=set)

    def publish(self, canonical_id: str, revision: str, text: str) -> str:
        digest = hashlib.sha256(text.encode()).hexdigest()
        self.cas[digest] = text
        self.manifest[canonical_id] = {"canonical_id": canonical_id, "revision": revision, "cas": digest, "visible": True}
        self.revoked.discard(canonical_id)
        return digest

    def revoke(self, canonical_id: str) -> None:
        if canonical_id in self.manifest: self.manifest[canonical_id]["visible"] = False
        self.revoked.add(canonical_id)

    def visible(self, canonical_id: str) -> bool:
        return bool(self.manifest.get(canonical_id, {}).get("visible")) and canonical_id not in self.revoked


def run_synthetic_full_loop(tmp_path, *, remote_available: bool = True) -> dict[str, Any]:
    """Run the real M0-M2 entrypoint from OAuth through UI-visible revocation."""
    owner, workspace = "member-taro", "medical-horizon"
    tmp_path.mkdir(parents=True, exist_ok=True)
    audit = ImmutableAudit(); verifier = "synthetic-verifier"
    oauth = OAuthLifecycle(owner_id=owner)
    state = oauth.begin({"workspace_read"}, verifier=verifier)
    oauth.callback(state=state["state"], verifier=verifier, code="synthetic-code")
    oauth.escalate({"gmail_read", "workspace_action"}); oauth.complete_escalation(); generation = oauth.rotate()
    audit.append("oauth.grant", {"owner": owner, "scopes": sorted(oauth.require()["scopes"]), "generation": generation})

    drive = SyntheticDrive(); drive.bootstrap("drive-file-1", "canonical operating procedure", revision="r1")
    drive.change("drive-file-1", "revised canonical operating procedure", revision="r2")
    store = EncryptedLocalStore(tmp_path / "company-context.db", KeyProvider(b"x" * 32, profile_id=owner), profile_id=owner, workspace_id=workspace)
    canonical_id = "gdrive:shared-drive-1:drive-file-1"
    knowledge = TeamKnowledge(); last_revision = None
    def apply_change(change):
        nonlocal last_revision
        if change.get("removed"):
            store.revoke(owner)
            knowledge.revoke(canonical_id)
            return
        file = change["file"]; last_revision = file["revision"]
        store.put(canonical_id, owner, file["text"], {"revision": last_revision, "data_class": Classification.Internal.value}, source="shared-drive")
        knowledge.publish(canonical_id, last_revision, file["text"])
        audit.append("drive.ingest", {"canonical_id": canonical_id, "revision": last_revision})
    connector = DriveChangesConnector(drive, Checkpoint("0"))
    connector.sync(apply_change)

    retriever = ContextProvider(store); context = retriever.context(owner, "revised canonical")
    if not remote_available or not allow_egress(Classification.Internal, "remote_model", approved_provider=True).allowed:
        remote_context = ""
        audit.append("egress.denied", {"owner": owner, "reason": "remote unavailable"})
    else:
        remote_context = context; audit.append("context.injected", {"owner": owner, "citations": context.count("[local:")})

    gmail_transport = SyntheticGmail(); signal = GmailPrivateSignal(owner, gmail_transport, clock=lambda: time.time())
    message = {"subject": "private metadata", "body": "review requested"}; signal.draft("mail-1", message)
    signal.approve("mail-1", {"owner_id": owner, "idempotency_key": "mail-1", "payload_hash": signal._digest(message), "expires_at": time.time() + 60})
    sent = signal.send("mail-1"); audit.append("gmail.send", {"owner": owner, "state": sent.state})

    drive.revoke_file("drive-file-1"); drive.changes[-1]["file"]=None
    store.revoke(owner); knowledge.revoke(canonical_id); oauth.revoke(); audit.append("revoke.purge", {"owner": owner, "canonical_id": canonical_id})
    return {"oauth_generation": generation, "revision": last_revision, "context": remote_context, "gmail_state": sent.state, "canonical_visible": knowledge.visible(canonical_id), "retrieval_after_revoke": retriever.context(owner, "revised"), "audit_ok": audit.verify(), "audit_events": [e.event_type for e in audit.events]}

# M3-M5 compatibility exports.  The implementation remains centralized in
# full_loop.py so callers do not get a second execution state machine.
from .full_loop import ExecutionEngine, FakeProvider as FullLoopFakeProvider, Plan, WorkerQueue, Lease
FakeProvider = FullLoopFakeProvider
__all__ = ["ExecutionEngine", "FakeProvider", "Plan", "WorkerQueue", "Lease", "OAuthLifecycle", "run_synthetic_full_loop"]
