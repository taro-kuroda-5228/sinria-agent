from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent.correction_loop.extraction import EvidenceCandidate

from .bridge import candidate_payloads
from .policy import KillSwitch, ScopePolicy, WorkspaceIdentity
from .state import Receipt, ReceiptLedger


class TransportOutcomeUnknown(RuntimeError):
    pass


@dataclass(frozen=True)
class ProposalResult:
    status: str
    idempotency_key: str
    remote_id: str | None = None


class CompanyOsKnowledgeClient:
    """Pushes sanitized metadata through an injected Company OS transport."""

    def __init__(
        self,
        *,
        identity: WorkspaceIdentity,
        policy: ScopePolicy,
        kill_switch: KillSwitch,
        ledger: ReceiptLedger,
        transport: Callable[[dict[str, Any]], dict[str, Any] | None],
    ):
        self.identity = identity
        self.policy = policy
        self.kill_switch = kill_switch
        self.ledger = ledger
        self.transport = transport

    def propose(self, candidate: EvidenceCandidate, *, dry_run: bool = False) -> ProposalResult:
        self.kill_switch.require_enabled()
        self.policy.authorize(self.identity, "knowledge.propose")
        observation, asset = candidate_payloads(candidate, self.identity)
        key = str(asset["idempotencyKey"])
        prior = self.ledger.get(key)
        if prior and prior.retry_blocked:
            raise TransportOutcomeUnknown("retry blocked after unknown transport outcome")
        if prior and prior.status == "confirmed":
            return ProposalResult(prior.status, key, prior.remote_id)
        if dry_run:
            return ProposalResult("dry_run", key)
        try:
            observation_result = self.transport(observation) or {}
            if not observation_result.get("ok"):
                raise RuntimeError("observation rejected")
            remote_observation = observation_result.get("observation", {}).get("observationId")
            if remote_observation:
                asset = {**asset, "sourceObservationIds": [remote_observation]}
            candidate_result = self.transport(asset) or {}
            if not candidate_result.get("ok"):
                raise RuntimeError("candidate rejected")
        except (TimeoutError, ConnectionError) as exc:
            self.ledger.put(Receipt(key, "unknown", retry_blocked=True, candidate_id=candidate.candidate_id))
            raise TransportOutcomeUnknown("transport outcome unknown; retry blocked") from exc
        remote_candidate = candidate_result.get("candidate") or candidate_result.get("asset") or {}
        remote_id = remote_candidate.get("assetId")
        if not isinstance(remote_id, str) or not remote_id.strip():
            raise RuntimeError("candidate response missing assetId")
        self.ledger.put(Receipt(key, "confirmed", remote_id=remote_id, candidate_id=candidate.candidate_id))
        return ProposalResult("confirmed", key, remote_id)
