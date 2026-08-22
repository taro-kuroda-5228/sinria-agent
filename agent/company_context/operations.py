"""Durable M3-M8 company-context operations.

The ledger is deliberately local and profile-scoped.  Provider effects are an
outbox-style operation: the stable idempotency key is recorded before a write,
then the provider is read back before the operation is committed.  A retry is
therefore safe after a process restart or a lost provider response.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .bridge import validate_metadata_payload


class LedgerError(RuntimeError): pass
class ProfileViolation(LedgerError): pass
class LeaseBusy(LedgerError): pass
class ApprovalBindingError(LedgerError): pass
class QuotaExceeded(LedgerError): pass


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


class Provider(Protocol):
    def write(self, *, key: str, artifact_id: str, content: str) -> str: ...
    def read(self, provider_id: str) -> str: ...
    def revoke(self, provider_id: str) -> None: ...
    def undo(self, provider_id: str) -> None: ...


class FakeProvider:
    """Deterministic provider used by tests and local staging; no network IO."""
    def __init__(self) -> None:
        self.items: dict[str, tuple[str, str, bool]] = {}
        self.writes = 0
        self.fail_next: str | None = None

    def write(self, *, key: str, artifact_id: str, content: str) -> str:
        fault = self.fail_next
        if fault == "timeout":
            self.fail_next = None
            raise TimeoutError("provider timeout")
        for provider_id, (old_key, _, revoked) in self.items.items():
            if old_key == key and not revoked: return provider_id
        provider_id = "fake-" + secrets.token_hex(8)
        self.items[provider_id] = (key, content, False); self.writes += 1
        if self.fail_next == "drop_response":
            self.fail_next = None
            raise TimeoutError("provider response lost after commit")
        return provider_id

    def read(self, provider_id: str) -> str:
        if provider_id not in self.items: raise KeyError(provider_id)
        _, content, revoked = self.items[provider_id]
        if revoked: raise LedgerError("provider item is revoked")
        return content

    def revoke(self, provider_id: str) -> None:
        key, content, _ = self.items[provider_id]; self.items[provider_id] = (key, content, True)
    def undo(self, provider_id: str) -> None:
        key, content, _ = self.items[provider_id]; self.items[provider_id] = (key, content, False)


@dataclass(frozen=True)
class Lease:
    job_id: str; profile: str; owner: str; token: str; expires_at: float


_SCHEMA = """
PRAGMA user_version=1;
CREATE TABLE IF NOT EXISTS context_jobs(
 job_id TEXT PRIMARY KEY, profile TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', idempotency_key TEXT NOT NULL UNIQUE,
 attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, lease_owner TEXT, lease_token TEXT,
 lease_expires REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS context_jobs_ready ON context_jobs(profile,status,created_at);
CREATE TABLE IF NOT EXISTS context_events(
 event_id INTEGER PRIMARY KEY AUTOINCREMENT, profile TEXT NOT NULL, kind TEXT NOT NULL,
 subject TEXT NOT NULL, data TEXT NOT NULL, idempotency_key TEXT, created_at REAL NOT NULL,
 UNIQUE(profile,kind,subject,idempotency_key));
CREATE TABLE IF NOT EXISTS context_approvals(
 approval_id TEXT PRIMARY KEY, profile TEXT NOT NULL, proposal_id TEXT NOT NULL,
 proposal_version INTEGER NOT NULL, content_hash TEXT NOT NULL, state TEXT NOT NULL,
 expires_at REAL, continuation TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS context_proposals(
 proposal_id TEXT PRIMARY KEY, profile TEXT NOT NULL, version INTEGER NOT NULL,
 content TEXT NOT NULL, content_hash TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL,
 UNIQUE(profile,proposal_id,version));
CREATE TABLE IF NOT EXISTS context_artifacts(
 artifact_id TEXT PRIMARY KEY, profile TEXT NOT NULL, proposal_id TEXT NOT NULL,
 version INTEGER NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL,
 state TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(profile,proposal_id,version));
CREATE TABLE IF NOT EXISTS context_activation(
 profile TEXT PRIMARY KEY, artifact_id TEXT, previous_artifact_id TEXT, generation INTEGER NOT NULL DEFAULT 0,
 updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS context_resources(
 profile TEXT PRIMARY KEY, used INTEGER NOT NULL DEFAULT 0, quota INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS context_holds(
 profile TEXT NOT NULL, subject TEXT NOT NULL, reason TEXT NOT NULL, created_at REAL NOT NULL,
 PRIMARY KEY(profile,subject));
CREATE TABLE IF NOT EXISTS context_members(
 profile TEXT NOT NULL, member_id TEXT NOT NULL, role TEXT NOT NULL, state TEXT NOT NULL,
 updated_at REAL NOT NULL, PRIMARY KEY(profile,member_id));
CREATE TABLE IF NOT EXISTS context_opportunities(
 opportunity_id TEXT PRIMARY KEY, profile TEXT NOT NULL, fingerprint TEXT NOT NULL,
 fingerprint_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', claimed_by TEXT,
 version INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(profile,fingerprint_hash));
CREATE TABLE IF NOT EXISTS context_evidence(
 evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, profile TEXT NOT NULL,
 opportunity_id TEXT NOT NULL, source TEXT NOT NULL, value_hash TEXT NOT NULL,
 observed_at REAL NOT NULL, version TEXT, UNIQUE(profile,opportunity_id,source,value_hash));
CREATE TABLE IF NOT EXISTS context_status(
 profile TEXT NOT NULL, subject TEXT NOT NULL, state TEXT NOT NULL, details TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL, PRIMARY KEY(profile,subject));
CREATE TABLE IF NOT EXISTS context_runtime(
 profile TEXT PRIMARY KEY, kill_switch INTEGER NOT NULL DEFAULT 0, slo_ms INTEGER NOT NULL DEFAULT 5000,
 alert_state TEXT NOT NULL DEFAULT 'ok', manifest_revision INTEGER NOT NULL DEFAULT 0,
 active_revision TEXT, index_revision TEXT, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS context_receipts(
 receipt_id TEXT PRIMARY KEY, profile TEXT NOT NULL, outcome TEXT NOT NULL, latency_ms REAL NOT NULL,
 payload TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS context_gaps(
 gap_id TEXT PRIMARY KEY, profile TEXT NOT NULL, receipt_id TEXT NOT NULL, metric TEXT NOT NULL,
 expected REAL NOT NULL, actual REAL NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS context_candidates(
 candidate_id TEXT PRIMARY KEY, profile TEXT NOT NULL, gap_id TEXT NOT NULL, revision TEXT NOT NULL,
 content TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS context_reviews(
 candidate_id TEXT PRIMARY KEY, profile TEXT NOT NULL, reviewer TEXT NOT NULL, decision TEXT NOT NULL,
 binding_hash TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS context_replay_corpus(
 candidate_id TEXT PRIMARY KEY, profile TEXT NOT NULL, corpus TEXT NOT NULL, result TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS synthetic_drill_receipts(
 run_id TEXT NOT NULL, sequence INTEGER NOT NULL, step TEXT NOT NULL, state TEXT NOT NULL,
 metadata TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(run_id, sequence));
"""


class ContextLedger:
    def __init__(self, db_path: str | Path, *, clock: Callable[[], float] = time.time) -> None:
        self.db_path = Path(db_path); self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL"); self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript(_SCHEMA); os.chmod(self.db_path, 0o600)

    def close(self) -> None: self.db.close()
    def _require_profile(self, profile: str) -> None:
        if not profile or profile.strip() != profile: raise ProfileViolation("profile is required")
    def _event(self, profile: str, kind: str, subject: str, data: dict[str, Any], key: str | None = None) -> None:
        self.db.execute("INSERT OR IGNORE INTO context_events VALUES(NULL,?,?,?,?,?,?)",
                        (profile, kind, subject, _json(data), key, self.clock()))

    def enqueue(self, profile: str, kind: str, payload: dict[str, Any], *, key: str) -> str:
        self._require_profile(profile); now = self.clock(); job_id = _hash((profile, key))[:32]
        if not isinstance(payload, dict): raise ValueError("payload must be an object")
        forbidden = {"raw", "raw_content", "locator", "credential", "credentials", "password", "secret", "access_token", "refresh_token"}
        if any(str(k).lower() in forbidden for k in payload):
            raise ProfileViolation("unsafe payload field")
        self.db.execute("INSERT OR IGNORE INTO context_jobs(job_id,profile,kind,payload,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                        (job_id, profile, kind, _json(payload), key, now, now))
        self._event(profile, "job_enqueued", job_id, {"kind": kind}, key); return job_id

    def claim(self, profile: str, owner: str, *, ttl: float = 300) -> Lease | None:
        self._require_profile(profile); now = self.clock(); token = secrets.token_hex(16)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM context_jobs WHERE profile=? AND ((status IN ('queued','retry')) OR (status='running' AND lease_expires<=?)) AND (lease_expires IS NULL OR lease_expires<=?) ORDER BY created_at LIMIT 1", (profile, now, now)).fetchone()
            if not row: self.db.execute("COMMIT"); return None
            exp = now + ttl; changed = self.db.execute("UPDATE context_jobs SET status='running',attempts=attempts+1,lease_owner=?,lease_token=?,lease_expires=?,updated_at=? WHERE job_id=? AND (lease_expires IS NULL OR lease_expires<=?)", (owner,token,exp,now,row['job_id'],now)).rowcount
            if changed != 1: self.db.execute("ROLLBACK"); raise LeaseBusy(row['job_id'])
            self._event(profile, "lease_acquired", row['job_id'], {"owner": owner, "expires_at": exp})
            self.db.execute("COMMIT"); return Lease(row['job_id'],profile,owner,token,exp)
        except Exception:
            if self.db.in_transaction: self.db.execute("ROLLBACK")
            raise

    def heartbeat(self, lease: Lease, *, ttl: float = 300) -> Lease:
        exp = self.clock() + ttl
        n = self.db.execute("UPDATE context_jobs SET lease_expires=?,updated_at=? WHERE job_id=? AND profile=? AND status='running' AND lease_owner=? AND lease_token=? AND lease_expires>?", (exp,self.clock(),lease.job_id,lease.profile,lease.owner,lease.token,self.clock())).rowcount
        if n != 1: raise LeaseBusy("lease lost")
        return Lease(lease.job_id,lease.profile,lease.owner,lease.token,exp)

    def job(self, lease: Lease) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT kind,payload FROM context_jobs WHERE job_id=? AND profile=? "
            "AND status='running' AND lease_owner=? AND lease_token=? AND lease_expires>?",
            (lease.job_id, lease.profile, lease.owner, lease.token, self.clock()),
        ).fetchone()
        if not row:
            raise LeaseBusy("lease lost")
        return {"kind": row["kind"], "payload": json.loads(row["payload"])}

    def finish(self, lease: Lease, *, status: str = "done", error: str | None = None) -> None:
        n = self.db.execute("UPDATE context_jobs SET status=?,last_error=?,lease_owner=NULL,lease_token=NULL,lease_expires=NULL,updated_at=? WHERE job_id=? AND profile=? AND status='running' AND lease_owner=? AND lease_token=?", (status,error,self.clock(),lease.job_id,lease.profile,lease.owner,lease.token)).rowcount
        if n != 1: raise LeaseBusy("lease lost")
        self._event(lease.profile, "job_finished", lease.job_id, {"status": status, "error": bool(error)})

    def propose(self, profile: str, proposal_id: str, content: str, *, version: int = 1) -> str:
        self._require_profile(profile); digest = _hash(content); now = self.clock()
        self.db.execute("INSERT OR REPLACE INTO context_proposals VALUES(?,?,?,?,?,?,?)", (proposal_id,profile,version,content,digest,"proposed",now))
        self._event(profile,"proposal_created",proposal_id,{"version":version,"content_hash":digest}); return digest

    def approve(self, profile: str, proposal_id: str, *, actor: str, expires_at: float | None = None) -> str:
        row = self.db.execute("SELECT * FROM context_proposals WHERE proposal_id=? AND profile=?",(proposal_id,profile)).fetchone()
        if not row: raise ApprovalBindingError("proposal/profile mismatch")
        token = secrets.token_urlsafe(32); self.db.execute("INSERT INTO context_approvals VALUES(?,?,?,?,?,?,?,?)", (token,profile,proposal_id,row['version'],row['content_hash'],'approved',expires_at,token))
        self._event(profile,"approved",proposal_id,{"actor":actor,"version":row['version']}); return token

    def revoke_approval(self, profile: str, continuation: str, *, actor: str) -> None:
        self._require_profile(profile)
        changed = self.db.execute("UPDATE context_approvals SET state='revoked' WHERE profile=? AND continuation=? AND state='approved'", (profile, continuation)).rowcount
        if changed != 1: raise ApprovalBindingError("approval not found or already revoked")
        self._event(profile, "approval_revoked", "approval", {"actor": actor})

    def detect_opportunity(self, profile: str, fingerprint: str) -> str:
        self._require_profile(profile); digest = _hash(fingerprint); oid = _hash((profile, digest))[:32]; now = self.clock()
        self.db.execute("INSERT OR IGNORE INTO context_opportunities VALUES(?,?,?,?,?,?,?,?,?)", (oid, profile, digest, digest, "open", None, 0, now, now))
        self._event(profile, "opportunity_detected", oid, {"fingerprint_hash": digest})
        return oid

    def add_evidence(self, profile: str, opportunity_id: str, *, source: str, value: str, version: str | None = None) -> None:
        self._require_profile(profile)
        if not self.db.execute("SELECT 1 FROM context_opportunities WHERE profile=? AND opportunity_id=?", (profile, opportunity_id)).fetchone(): raise LedgerError("opportunity not found")
        digest = _hash(value)
        self.db.execute("INSERT OR IGNORE INTO context_evidence(profile,opportunity_id,source,value_hash,observed_at,version) VALUES(?,?,?,?,?,?)", (profile, opportunity_id, source, digest, self.clock(), version))
        self._event(profile, "evidence_recorded", opportunity_id, {"source": source, "value_hash": digest})

    def claim_opportunity(self, profile: str, opportunity_id: str, worker: str) -> bool:
        self._require_profile(profile)
        n = self.db.execute("UPDATE context_opportunities SET status='claimed',claimed_by=?,version=version+1,updated_at=? WHERE profile=? AND opportunity_id=? AND status='open' AND claimed_by IS NULL", (worker, self.clock(), profile, opportunity_id)).rowcount
        if n: self._event(profile, "opportunity_claimed", opportunity_id, {"worker": worker})
        return n == 1

    def sync_status(self, profile: str, subject: str, state: str, *, details: dict[str, Any] | None = None, expected_version: int | None = None) -> int:
        self._require_profile(profile); now = self.clock(); details = details or {}
        if expected_version is None:
            self.db.execute("INSERT INTO context_status(profile,subject,state,details,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(profile,subject) DO UPDATE SET state=excluded.state,details=excluded.details,version=version+1,updated_at=excluded.updated_at", (profile, subject, state, _json(details), now))
        else:
            n = self.db.execute("UPDATE context_status SET state=?,details=?,version=version+1,updated_at=? WHERE profile=? AND subject=? AND version=?", (state, _json(details), now, profile, subject, expected_version)).rowcount
            if n != 1: raise LedgerError("status CAS conflict")
        version = int(self.db.execute("SELECT version FROM context_status WHERE profile=? AND subject=?", (profile, subject)).fetchone()[0])
        self._event(profile, "company_os_status_synced", subject, {"state": state, "version": version})
        return version

    def continuation(self, token: [REDACTED], profile: str, proposal_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT a.*,p.content,p.state AS proposal_state FROM context_approvals a JOIN context_proposals p ON p.profile=a.profile AND p.proposal_id=a.proposal_id AND p.version=a.proposal_version WHERE a.continuation=? AND a.profile=? AND a.proposal_id=?",(token,profile,proposal_id)).fetchone()
        if not row or row['state'] != 'approved' or (row['expires_at'] is not None and row['expires_at'] <= self.clock()) or _hash(row['content']) != row['content_hash']:
            raise ApprovalBindingError("invalid, expired, changed, or cross-profile continuation")
        return row

    def activate(self, profile: str, proposal_id: str, approval_handle: str, *, canary: bool = False) -> str:
        now = self.clock(); self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.continuation(approval_handle, profile, proposal_id)
            consumed = self.db.execute(
                "UPDATE context_approvals SET state='consumed' WHERE continuation=? AND profile=? AND proposal_id=? AND state='approved'",
                (approval_handle, profile, proposal_id),
            ).rowcount
            if consumed != 1:
                raise ApprovalBindingError("approval continuation already consumed")
            aid = f"{proposal_id}:{row['proposal_version']}"
            current = self.db.execute(
                "SELECT artifact_id FROM context_activation WHERE profile=?",
                (profile,),
            ).fetchone()
            old_id = current["artifact_id"] if current else None
            self.db.execute("INSERT OR IGNORE INTO context_artifacts VALUES(?,?,?,?,?,?,?,?)",(aid,profile,proposal_id,row['proposal_version'],row['content'],row['content_hash'],'canary' if canary else 'verified',now))

            self.db.execute("INSERT INTO context_activation(profile,artifact_id,previous_artifact_id,generation,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(profile) DO UPDATE SET previous_artifact_id=artifact_id,artifact_id=excluded.artifact_id,generation=generation+1,updated_at=excluded.updated_at",(profile,aid,old_id,0,now))
            self._event(profile,"artifact_activated",aid,{"canary":canary,"previous":old_id}); self.db.execute("COMMIT"); return aid
        except Exception:
            self.db.execute("ROLLBACK"); raise

    def rollback(self, profile: str) -> str:
        row=self.db.execute("SELECT artifact_id,previous_artifact_id FROM context_activation WHERE profile=?",(profile,)).fetchone()
        if not row or not row['previous_artifact_id']: raise LedgerError("no verified previous artifact")
        self.db.execute("UPDATE context_activation SET artifact_id=?,previous_artifact_id=?,generation=generation+1,updated_at=? WHERE profile=?",(row['previous_artifact_id'],row['artifact_id'],self.clock(),profile)); self._event(profile,"artifact_rollback",row['previous_artifact_id'],{}); return row['previous_artifact_id']

    def publish(self, profile: str, provider: Provider, artifact_id: str, content: str, *, key: str) -> str:
        """Write once, read back, and reuse the receipt on retry."""
        self._require_profile(profile); digest = _hash(content)
        prior = self.db.execute("SELECT subject,data FROM context_events WHERE profile=? AND kind='provider_published' AND idempotency_key=?", (profile,key)).fetchone()
        if prior:
            provider_id = prior['subject']; payload = json.loads(prior['data'])
            if payload.get('content_hash') != digest: raise LedgerError("idempotency key content mismatch")
            if provider.read(provider_id) != content: raise LedgerError("provider readback mismatch")
            return provider_id
        self._event(profile,"provider_write_started",artifact_id,{"content_hash":digest},key)
        provider_id = provider.write(key=key, artifact_id=artifact_id, content=content)
        if provider.read(provider_id) != content: raise LedgerError("provider readback mismatch")
        self._event(profile,"provider_published",provider_id,{"artifact_id":artifact_id,"content_hash":digest},key)
        return provider_id

    def replay(self, profile: str, proposal_id: str, version: int) -> str:
        row=self.db.execute("SELECT content_hash FROM context_proposals WHERE profile=? AND proposal_id=? AND version=?",(profile,proposal_id,version)).fetchone()
        if not row: raise LedgerError("proposal not found")
        self._event(profile,"proposal_replayed",proposal_id,{"version":version,"content_hash":row['content_hash']}); return row['content_hash']

    def revoke(self, profile: str, provider: Provider, provider_id: str, *, key: str) -> None:
        self._event(profile,"provider_revoke_started",provider_id,{},key); provider.revoke(provider_id); self._event(profile,"provider_revoked",provider_id,{},key)
    def undo(self, profile: str, provider: Provider, provider_id: str, *, key: str) -> None:
        self._event(profile,"provider_undo_started",provider_id,{},key); provider.undo(provider_id); self._event(profile,"provider_undone",provider_id,{},key)

    def retain(self, profile: str, subject: str, *, reason: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO context_holds VALUES(?,?,?,?)",(profile,subject,reason,self.clock())); self._event(profile,"legal_hold",subject,{"reason":reason})
    def purge(self, profile: str, *, before: float) -> int:
        before = min(before, 9223372036854775807)
        held={r['subject'] for r in self.db.execute("SELECT subject FROM context_holds WHERE profile=?",(profile,))}; rows=self.db.execute("SELECT event_id,subject FROM context_events WHERE profile=? AND created_at<?",(profile,before)).fetchall(); ids=[r['event_id'] for r in rows if r['subject'] not in held]
        if ids: self.db.executemany("DELETE FROM context_events WHERE event_id=?",((i,) for i in ids)); self._event(profile,"retention_purge","retention",{"count":len(ids)})
        return len(ids)
    def set_quota(self, profile: str, quota: int) -> None: self.db.execute("INSERT INTO context_resources(profile,quota) VALUES(?,?) ON CONFLICT(profile) DO UPDATE SET quota=excluded.quota",(profile,quota))
    def reserve(self, profile: str, amount: int) -> None:
        n=self.db.execute("UPDATE context_resources SET used=used+? WHERE profile=? AND used+?<=quota",(amount,profile,amount)).rowcount
        if n != 1: raise QuotaExceeded(profile)
    def jml(self, profile: str, member: str, state: str, *, role: str = "member") -> None:
        if state not in {"active","moved","leaver"}: raise ValueError("invalid JML state")
        self.db.execute("INSERT INTO context_members VALUES(?,?,?,?,?) ON CONFLICT(profile,member_id) DO UPDATE SET role=excluded.role,state=excluded.state,updated_at=excluded.updated_at",(profile,member,role,state,self.clock())); self._event(profile,"jml_"+state,member,{"role":role})

    # M6-M8 durable outcome-to-activation runtime.
    def _runtime(self, profile: str) -> sqlite3.Row:
        self._require_profile(profile)
        self.db.execute("INSERT OR IGNORE INTO context_runtime(profile,updated_at) VALUES(?,?)", (profile, self.clock()))
        return self.db.execute("SELECT * FROM context_runtime WHERE profile=?", (profile,)).fetchone()

    def set_kill_switch(self, profile: str, enabled: bool) -> None:
        self._runtime(profile)
        self.db.execute("UPDATE context_runtime SET kill_switch=?,updated_at=? WHERE profile=?", (int(enabled), self.clock(), profile))
        self._event(profile, "kill_switch", profile, {"enabled": bool(enabled)})

    def set_slo(self, profile: str, slo_ms: int) -> None:
        if slo_ms <= 0: raise ValueError("slo must be positive")
        self._runtime(profile); self.db.execute("UPDATE context_runtime SET slo_ms=?,updated_at=? WHERE profile=?", (slo_ms, self.clock(), profile))

    def evaluate_alert(self, profile: str, latency_ms: float) -> bool:
        row = self._runtime(profile); breached = latency_ms > row["slo_ms"]
        self.db.execute("UPDATE context_runtime SET alert_state=?,updated_at=? WHERE profile=?", ("breach" if breached else "ok", self.clock(), profile))
        self._event(profile, "slo_alert", profile, {"latency_ms": latency_ms, "breached": breached})
        return breached

    def require_live(self, profile: str) -> None:
        if self._runtime(profile)["kill_switch"]: raise LedgerError("kill switch enabled")

    def record_receipt(self, profile: str, receipt_id: str, outcome: str, latency_ms: float, payload: dict[str, Any]) -> None:
        self.require_live(profile)
        validate_metadata_payload(payload)
        self.db.execute("INSERT OR IGNORE INTO context_receipts VALUES(?,?,?,?,?,?)", (receipt_id, profile, outcome, latency_ms, _json(payload), self.clock()))
        self._event(profile, "outcome_receipt", receipt_id, {"outcome": outcome, "latency_ms": latency_ms}, receipt_id)

    def record_drill_receipt(self, run_id: str, sequence: int, step: str, state: str, metadata: dict[str, Any]) -> None:
        """Persist only allow-listed, metadata-only evidence for a synthetic drill."""
        validate_metadata_payload(metadata)
        if not run_id or sequence < 1 or not step or state not in {"ok", "failed", "rolled_back"}:
            raise ValueError("invalid drill receipt")
        self.db.execute("INSERT OR IGNORE INTO synthetic_drill_receipts VALUES(?,?,?,?,?,?)",
                        (run_id, sequence, step, state, _json(metadata), self.clock()))

    def drill_receipts(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT run_id,sequence,step,state,metadata FROM synthetic_drill_receipts WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
        return [{"run_id": r["run_id"], "sequence": r["sequence"], "step": r["step"], "state": r["state"], "metadata": json.loads(r["metadata"])} for r in rows]

    def record_gap(self, profile: str, receipt_id: str, metric: str, expected: float, actual: float) -> str:
        self.require_live(profile); gap_id = _hash((profile, receipt_id, metric))[:32]
        self.db.execute("INSERT OR IGNORE INTO context_gaps VALUES(?,?,?,?,?,?,?,?)", (gap_id, profile, receipt_id, metric, expected, actual, "open", self.clock()))
        self._event(profile, "gap_detected", gap_id, {"receipt_id": receipt_id, "metric": metric})
        return gap_id

    def candidate(self, profile: str, gap_id: str, revision: str, content: str) -> str:
        self.require_live(profile); candidate_id = _hash((profile, gap_id, revision, content))[:32]
        self.db.execute("INSERT OR IGNORE INTO context_candidates VALUES(?,?,?,?,?,?,?)", (candidate_id, profile, gap_id, revision, content, "replay_pending", self.clock()))
        self._event(profile, "candidate_revision", candidate_id, {"gap_id": gap_id, "revision": revision})
        return candidate_id

    def replay_candidate(self, profile: str, candidate_id: str, corpus: list[dict[str, Any]]) -> str:
        row = self.db.execute("SELECT * FROM context_candidates WHERE candidate_id=? AND profile=?", (candidate_id, profile)).fetchone()
        if not row: raise ProfileViolation("candidate/profile mismatch")
        result = "pass" if all(bool(item.get("pass", True)) for item in corpus) else "fail"
        self.db.execute("INSERT OR REPLACE INTO context_replay_corpus VALUES(?,?,?,?,?)", (candidate_id, profile, _json(corpus), result, self.clock()))
        self.db.execute("UPDATE context_candidates SET state=? WHERE candidate_id=?", ("replay_passed" if result == "pass" else "replay_failed", candidate_id))
        self._event(profile, "replay_complete", candidate_id, {"result": result, "corpus_size": len(corpus)})
        return result

    def review_candidate(self, profile: str, candidate_id: str, reviewer: str, decision: str) -> str:
        if decision not in {"approve", "reject"}: raise ValueError("invalid review decision")
        row = self.db.execute("SELECT state,content FROM context_candidates WHERE candidate_id=? AND profile=?", (candidate_id, profile)).fetchone()
        if not row or row["state"] != "replay_passed": raise ApprovalBindingError("candidate is not replay-approved")
        binding = _hash((profile, candidate_id, reviewer, decision, row["content"]))
        self.db.execute("INSERT OR REPLACE INTO context_reviews VALUES(?,?,?,?,?,?)", (candidate_id, profile, reviewer, decision, binding, self.clock()))
        self.db.execute("UPDATE context_candidates SET state=? WHERE candidate_id=?", ("review_approved" if decision == "approve" else "review_rejected", candidate_id))
        self._event(profile, "human_review", candidate_id, {"reviewer": reviewer, "decision": decision, "binding_hash": binding})
        return binding

    def activate_manifest(self, profile: str, revision: str, *, index_revision: str | None = None, fail: bool = False) -> int:
        self.require_live(profile); self.db.execute("BEGIN IMMEDIATE")
        try:
            if fail: raise LedgerError("activation fault injected")
            row = self._runtime(profile); generation = int(row["manifest_revision"]) + 1; idx = index_revision or revision
            self.db.execute("UPDATE context_runtime SET manifest_revision=?,active_revision=?,index_revision=?,updated_at=? WHERE profile=?", (generation, revision, idx, self.clock(), profile))
            self._event(profile, "manifest_activated", revision, {"generation": generation, "index_revision": idx})
            self.db.execute("COMMIT"); return generation
        except Exception:
            self.db.execute("ROLLBACK"); raise

    def rollback_manifest(self, profile: str, revision: str) -> int:
        """Synthetic-safe rollback to a known revision, without provider or network effects."""
        return self.activate_manifest(profile, revision, index_revision=revision)

    def runtime_status(self, profile: str) -> dict[str, Any]: return dict(self._runtime(profile))

    def backup(self, destination: str | Path) -> None:
        dest = sqlite3.connect(str(destination)); self.db.backup(dest); dest.close()

    def restore(self, source: str | Path) -> None:
        src = sqlite3.connect(str(source)); src.backup(self.db); src.close()
