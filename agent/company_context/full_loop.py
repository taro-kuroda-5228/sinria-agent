"""M3-M5 metadata-only Company OS vertical slice; external writes require injection of FakeProvider."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib, json, threading
from typing import Any, Mapping, Protocol

def _now(): return datetime.now(timezone.utc).isoformat()
def _digest(v): return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",",":")).encode()).hexdigest()

@dataclass(frozen=True)
class Rulebook:
    version: str
    rules: Mapping[str, str]
    hash: str = ""
    def __post_init__(self):
        if not self.version or not isinstance(self.rules, Mapping): raise ValueError("invalid rulebook")
        actual = _digest(dict(self.rules))
        if self.hash and self.hash != actual: raise ValueError("rulebook hash mismatch")
        object.__setattr__(self, "hash", actual)

class RulebookStore:
    def __init__(self, rulebook=None): self._active = rulebook or Rulebook("0", {}); self._lock=threading.RLock()
    def read(self): return self._active
    def sync(self, incoming):
        with self._lock:
            if incoming.version == self._active.version and incoming.hash == self._active.hash: return "no-op"
            self._active = incoming; return "applied"

@dataclass(frozen=True)
class AssetProfile:
    asset_id: str; asset_kind: str; classifier: str; confidence: float
    rationale: tuple[str,...]; rulebook_version: str; rulebook_hash: str
    metadata: Mapping[str,Any] = field(default_factory=dict)
    def __post_init__(self):
        if not self.asset_id or not self.asset_kind or not self.classifier: raise ValueError("asset identity required")
        if not 0 <= self.confidence <= 1: raise ValueError("confidence must be between 0 and 1")

class AssetProfiler:
    def __init__(self, rulebooks): self.rulebooks=rulebooks; self._profiles={}
    def profile(self, asset_id, metadata):
        if not asset_id or not isinstance(metadata, Mapping): raise ValueError("asset id and metadata required")
        rb=self.rulebooks.read(); text=" ".join(str(v).lower() for v in metadata.values())
        classifier, confidence, rationale = "unknown", 0.0, ("no matching rule",)
        for name, pattern in sorted(rb.rules.items()):
            if str(pattern).lower() in text:
                classifier, confidence, rationale = name, .95, (f"matched rule:{name}", f"pattern:{pattern}"); break
        result=AssetProfile(asset_id,str(metadata.get("kind","asset")),classifier,confidence,rationale,rb.version,rb.hash,{"keys":tuple(sorted(map(str,metadata)))})
        self._profiles[asset_id]=result; return result
    def read(self, asset_id): return self._profiles[asset_id]

class WriteProvider(Protocol):
    def write(self, key: str, value: Mapping[str,Any]) -> Mapping[str,Any]: ...
    def read(self, key: str): ...
    def undo(self, key: str) -> Mapping[str,Any]: ...

class FakeProvider:
    def __init__(self): self.state={}; self.writes=[]; self.undos=[]
    def write(self,key,value):
        if key not in self.state: self.writes.append(key); self.state[key]={"key":key,**dict(value),"providerVersion":1}
        return dict(self.state[key])
    def read(self,key): return dict(self.state[key]) if key in self.state else None
    def undo(self,key): self.undos.append(key); self.state.pop(key,None); return {"key":key,"deleted":True}

@dataclass(frozen=True)
class Plan:
    plan_id: str; version: str; idempotency_key: str; desired: Mapping[str,Any]

class ExecutionEngine:
    def __init__(self,provider): self.provider=provider; self._plans={}; self._results={}
    def preview(self,plan_id,desired,version="1"):
        p=Plan(plan_id,version,f"plan:{plan_id}:{version}",dict(desired)); self._plans[p.idempotency_key]=p; return p
    def execute(self,plan):
        if self._plans.get(plan.idempotency_key)!=plan: raise ValueError("plan/version/idempotency mismatch")
        if plan.idempotency_key not in self._results: self._results[plan.idempotency_key]=dict(self.provider.write(plan.idempotency_key,plan.desired))
        observed=self.provider.read(plan.idempotency_key)
        if observed is None: raise RuntimeError("provider readback missing")
        self._results[plan.idempotency_key]=dict(observed); return dict(observed)
    def undo(self,plan):
        if plan.idempotency_key not in self._results: return {"key":plan.idempotency_key,"deleted":True}
        result=self.provider.undo(plan.idempotency_key); self._results.pop(plan.idempotency_key,None); return dict(result)

@dataclass
class Lease:
    task_id: str; worker_id: str; fence: int; expires_at: float

class WorkerQueue:
    def __init__(self,lease_seconds=30.0,clock=None):
        import time
        self.lease_seconds=lease_seconds; self.clock=clock or time.monotonic; self._leases={}; self._attempts={}; self._committed=set(); self._lock=threading.Lock()
    def claim(self,task_id,worker_id):
        with self._lock:
            old=self._leases.get(task_id)
            if old and old.expires_at > self.clock(): return None
            fence=old.fence+1 if old else 1; self._attempts[task_id]=self._attempts.get(task_id,0)+1
            lease=Lease(task_id,worker_id,fence,self.clock()+self.lease_seconds); self._leases[task_id]=lease; return lease
    def heartbeat(self,lease):
        with self._lock:
            current=self._leases.get(lease.task_id)
            if current is None or current != lease or current.expires_at <= self.clock(): raise PermissionError("lease expired")
            current.expires_at=self.clock()+self.lease_seconds; return current
    def commit(self,lease):
        with self._lock:
            current=self._leases.get(lease.task_id)
            if current is None or current != lease or current.expires_at <= self.clock(): raise PermissionError("fenced worker")
            if lease.task_id in self._committed: return False
            self._committed.add(lease.task_id); return True
    def attempts(self,task_id): return self._attempts.get(task_id,0)

@dataclass(frozen=True)
class Evidence:
    source: str; value: str; observed_at: str; version: str|None=None
@dataclass
class Opportunity:
    opportunity_id: str; fingerprint: str; status: str="open"; claimed_by: str|None=None; evidence: list[Evidence]=field(default_factory=list)
class OpportunityLedger:
    def __init__(self): self._items={}
    def detect(self,fingerprint):
        key=_digest(fingerprint)[:24]; return self._items.setdefault(key,Opportunity(key,fingerprint))
    def claim(self,opportunity_id,worker_id):
        item=self._items[opportunity_id]
        if item.claimed_by not in (None,worker_id): raise RuntimeError("opportunity already claimed")
        item.claimed_by=worker_id; item.status="claimed"; return item
    def add_evidence(self,opportunity_id,evidence): self._items[opportunity_id].evidence.append(evidence); return self._items[opportunity_id]
    def read(self,opportunity_id): return self._items[opportunity_id]

@dataclass
class Task:
    task_id: str; plan: Plan; status: str="awaiting_approval"; decision: str|None=None; result: Mapping[str,Any]|None=None
class TaskContinuation:
    def __init__(self,execution): self.execution=execution; self.tasks={}
    def activate(self,task_id,plan): return self.tasks.setdefault(task_id,Task(task_id,plan))
    def approve(self,task_id,decision):
        task=self.tasks[task_id]
        if decision not in {"approve","reject"}: raise ValueError("invalid approval")
        if task.decision is not None and task.decision != decision: raise RuntimeError("conflicting approval")
        task.decision=decision
        if decision=="reject": task.status="rejected"
        elif task.status != "completed": task.result=self.execution.execute(task.plan); task.status="completed"
        return task
    def read(self,task_id): return self.tasks[task_id]


@dataclass(frozen=True)
class OutcomeReceipt:
    opportunity_id: str
    task_id: str
    metric: str
    value: float
    recorded_at: str


class OpportunityWorkflow:
    """Single local-safe entrypoint for detect→claim→approve→execute→outcome."""

    def __init__(self, *, opportunities: OpportunityLedger, tasks: TaskContinuation, execution: ExecutionEngine):
        self.opportunities = opportunities
        self.tasks = tasks
        self.execution = execution
        self.outcomes: list[OutcomeReceipt] = []

    def run(
        self,
        *,
        fingerprint: str,
        worker_id: str,
        evidence: Evidence,
        desired: Mapping[str, Any],
        decision: str,
        metric: str,
        value: float,
    ) -> dict[str, Any]:
        opportunity = self.opportunities.detect(fingerprint)
        self.opportunities.claim(opportunity.opportunity_id, worker_id)
        self.opportunities.add_evidence(opportunity.opportunity_id, evidence)
        plan = self.execution.preview(opportunity.opportunity_id, desired)
        task_id = f"opportunity:{opportunity.opportunity_id}"
        task = self.tasks.activate(task_id, plan)
        task = self.tasks.approve(task_id, decision)
        if task.status != "completed":
            opportunity.status = "rejected"
            return {"opportunityId": opportunity.opportunity_id, "taskId": task_id, "status": task.status}
        opportunity.status = "completed"
        receipt = OutcomeReceipt(opportunity.opportunity_id, task_id, metric, float(value), _now())
        if not any(item.task_id == task_id and item.metric == metric for item in self.outcomes):
            self.outcomes.append(receipt)
        observed = self.execution.provider.read(plan.idempotency_key)
        if observed is None:
            raise RuntimeError("provider readback missing after opportunity completion")
        return {
            "opportunityId": opportunity.opportunity_id,
            "taskId": task_id,
            "status": "completed",
            "metric": metric,
            "value": float(value),
            "providerReadback": dict(observed),
            "rawContextStored": False,
        }


class AssetCurationWorkflow:
    """Single safe entrypoint for asset profile→preview→approval→write/readback/undo."""

    def __init__(self, *, profiler: AssetProfiler, execution: ExecutionEngine, tasks: TaskContinuation):
        self.profiler = profiler
        self.execution = execution
        self.tasks = tasks

    def preview(self, asset_id: str, metadata: Mapping[str, Any], desired: Mapping[str, Any]) -> tuple[AssetProfile, Task]:
        profile = self.profiler.profile(asset_id, metadata)
        plan = self.execution.preview(asset_id, desired)
        return profile, self.tasks.activate(f"asset:{asset_id}", plan)

    def decide(self, asset_id: str, decision: str) -> dict[str, Any]:
        task_id = f"asset:{asset_id}"
        task = self.tasks.approve(task_id, decision)
        if task.status != "completed":
            return {"taskId": task_id, "status": task.status}
        observed = self.execution.provider.read(task.plan.idempotency_key)
        if observed is None:
            raise RuntimeError("provider readback missing after asset execution")
        return {"taskId": task_id, "status": task.status, "providerReadback": dict(observed)}

    def undo(self, asset_id: str) -> dict[str, Any]:
        task = self.tasks.read(f"asset:{asset_id}")
        result = self.execution.undo(task.plan)
        task.status = "undone"
        return result
