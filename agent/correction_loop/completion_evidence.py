"""Sanitized per-turn receipts used by completion enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re
import threading


class CompletionStage(str, Enum):
    """Evidence-backed delivery stages, ordered from local work to production."""

    IMPLEMENTED = "implemented"
    VALIDATED = "validated"
    REVIEW_READY = "review_ready"
    APPROVED = "approved"
    MERGED = "merged"
    DEPLOYED = "deployed"
    PRODUCTION_VERIFIED = "production_verified"


_COMPLETION_STAGE_RANK = {stage: rank for rank, stage in enumerate(CompletionStage)}
_STAGE_ACTION_CLASSES = {
    CompletionStage.VALIDATED: frozenset({"verification"}),
    CompletionStage.REVIEW_READY: frozenset({"verification"}),
    CompletionStage.APPROVED: frozenset({"approval"}),
    CompletionStage.MERGED: frozenset({"git_mutation"}),
    CompletionStage.DEPLOYED: frozenset({"resource_mutation"}),
    CompletionStage.PRODUCTION_VERIFIED: frozenset({"verification"}),
}
_PRODUCTION_READBACK_TOOL_RE = re.compile(
    r"(?:readback|health[_-]?check|smoke[_-]?test)", re.IGNORECASE
)
_STAGE_EVIDENCE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def completion_stage_at_least(
    observed: CompletionStage, required: CompletionStage
) -> bool:
    return _COMPLETION_STAGE_RANK[observed] >= _COMPLETION_STAGE_RANK[required]


@dataclass(frozen=True)
class CompletionEvidence:
    tool_name: str
    action_class: str
    status: str
    target_summary: str
    evidence_ids: tuple[str, ...]
    sequence: int
    reason_code: str = "none"
    completion_stage: CompletionStage | None = None
    target_identity_summary: str = "none"


@dataclass(frozen=True)
class ReceiptCompletion:
    complete: bool
    reason_code: str
    evidence_ids: tuple[str, ...] = ()
    stage: CompletionStage = CompletionStage.IMPLEMENTED


def _sanitize_target(value: str) -> str:
    digest = hashlib.sha256((value or "unspecified").encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"sha256:{digest}"


def _production_target_identity(value: str) -> str:
    """Hash the complete normalized production target without retaining raw data."""
    normalized = " ".join((value or "").strip().lower().split()).rstrip("/")
    if not re.search(
        r"(?<![a-z0-9])(?:production|prod|本番)(?![a-z0-9])",
        normalized,
        re.IGNORECASE,
    ):
        return "none"
    return _sanitize_target(f"production-target:{normalized}")


class CompletionEvidenceLedger:
    """In-memory, turn-scoped receipt ledger; no raw args or tool results."""

    _MUTATION_CLASSES = frozenset(
        {"file_mutation", "git_mutation", "resource_mutation"}
    )
    _EXTERNAL_MUTATION_TOOLS = frozenset(
        {"send_message", "content_os_publish", "browser_click", "browser_type", "browser_press"}
    )

    def __init__(self) -> None:
        self._receipts: list[CompletionEvidence] = []
        self._lock = threading.Lock()
        self._sequence = 0

    @property
    def receipts(self) -> tuple[CompletionEvidence, ...]:
        with self._lock:
            return tuple(self._receipts)

    def has_stage_evidence(
        self, stage: CompletionStage, evidence_ids: tuple[str, ...]
    ) -> bool:
        """Return whether a prior explicit stage shares trusted source evidence."""
        requested = set(evidence_ids)
        if not requested:
            return False
        return any(
            item.status == "succeeded"
            and item.completion_stage is not None
            and completion_stage_at_least(item.completion_stage, stage)
            and bool(requested.intersection(item.evidence_ids))
            for item in self.receipts
        )

    def record(
        self,
        *,
        tool_name: str,
        action_class: str,
        status: str,
        target: str,
        evidence_ids: tuple[str, ...] = (),
        reason_code: str = "none",
        completion_stage: CompletionStage | str | None = None,
    ) -> CompletionEvidence:
        safe_reason_code = (
            reason_code
            if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason_code)
            else "invalid_reason_code"
        )
        safe_completion_stage = (
            CompletionStage(completion_stage) if completion_stage is not None else None
        )
        target_identity_summary = _production_target_identity(target)
        if safe_completion_stage is not None:
            if status != "succeeded":
                raise ValueError("completion_stage requires a succeeded receipt")
            if not evidence_ids or any(
                not _STAGE_EVIDENCE_ID_RE.fullmatch(item) for item in evidence_ids
            ):
                raise ValueError("completion_stage requires sanitized evidence ids")
            allowed_classes = _STAGE_ACTION_CLASSES.get(safe_completion_stage)
            if allowed_classes is not None and action_class not in allowed_classes:
                raise ValueError(
                    f"{safe_completion_stage.value} cannot be attested by {action_class}"
                )
            if (
                safe_completion_stage is CompletionStage.PRODUCTION_VERIFIED
                and not _PRODUCTION_READBACK_TOOL_RE.search(tool_name or "")
            ):
                raise ValueError("production_verified requires a dedicated readback receipt")
            if (
                safe_completion_stage is CompletionStage.PRODUCTION_VERIFIED
                and target_identity_summary == "none"
            ):
                raise ValueError("production_verified requires an explicit production target")
        with self._lock:
            if safe_completion_stage is CompletionStage.PRODUCTION_VERIFIED:
                requested = set(evidence_ids)
                correlated_deploy = any(
                    item.status == "succeeded"
                    and item.completion_stage is not None
                    and completion_stage_at_least(
                        item.completion_stage, CompletionStage.DEPLOYED
                    )
                    and bool(requested.intersection(item.evidence_ids))
                    and item.target_identity_summary == target_identity_summary
                    for item in self._receipts
                )
                if not correlated_deploy:
                    raise ValueError(
                        "production_verified requires prior deployed stage for the same production target with shared evidence"
                    )
            self._sequence += 1
            receipt = CompletionEvidence(
                tool_name=tool_name,
                action_class=action_class,
                status=status,
                target_summary=_sanitize_target(target),
                evidence_ids=tuple(sorted(set(evidence_ids))),
                sequence=self._sequence,
                reason_code=safe_reason_code,
                completion_stage=safe_completion_stage,
                target_identity_summary=target_identity_summary,
            )
            self._receipts.append(receipt)
            return receipt

    @property
    def circuit_open(self) -> bool:
        return any(self.circuit_open_for(item.action_class) for item in self.receipts)

    def circuit_open_for(self, action_class: str) -> bool:
        """Open only for repeated, evidence-backed violations of one action class."""
        counts: dict[tuple[str, ...], int] = {}
        for item in self.receipts:
            if (
                item.action_class != action_class
                or item.status not in {"blocked", "recoverable_blocked", "approval_required"}
                or not item.evidence_ids
            ):
                continue
            counts[item.evidence_ids] = counts.get(item.evidence_ids, 0) + 1
        return any(count >= 3 for count in counts.values())

    def _observed_stage(
        self, receipts: tuple[CompletionEvidence, ...]
    ) -> tuple[CompletionStage, tuple[str, ...]]:
        mutations = [
            item
            for item in receipts
            if (
                item.action_class in self._MUTATION_CLASSES
                or (
                    item.action_class == "external_egress"
                    and item.tool_name in self._EXTERNAL_MUTATION_TOOLS
                )
            )
        ]
        last_mutation_sequence = max(
            (item.sequence for item in mutations), default=0
        )
        stage = CompletionStage.IMPLEMENTED
        evidence_ids: tuple[str, ...] = ()
        for item in receipts:
            if (
                item.status == "succeeded"
                and item.completion_stage is not None
                and item.sequence >= last_mutation_sequence
                and completion_stage_at_least(item.completion_stage, stage)
            ):
                stage = item.completion_stage
                evidence_ids = item.evidence_ids

        if mutations:
            validations = [
                item
                for item in receipts
                if item.action_class == "verification"
                and item.status == "succeeded"
                and item.sequence > last_mutation_sequence
            ]
            if validations and not completion_stage_at_least(
                stage, CompletionStage.VALIDATED
            ):
                stage = CompletionStage.VALIDATED
                evidence_ids = tuple(
                    sorted(
                        {
                            evidence_id
                            for item in validations
                            for evidence_id in item.evidence_ids
                        }
                    )
                )
        return stage, evidence_ids

    def completion_status(self) -> ReceiptCompletion:
        receipts = self.receipts
        observed_stage, stage_evidence_ids = self._observed_stage(receipts)
        approval_required = [item for item in receipts if item.status == "approval_required"]

        def _recovered_by_later_success(item: CompletionEvidence) -> bool:
            return any(
                later.status == "succeeded"
                and later.action_class == item.action_class
                and later.sequence > item.sequence
                and item.evidence_ids
                and bool(set(item.evidence_ids) & set(later.evidence_ids))
                for later in receipts
            )

        unrecovered_approvals = [
            item for item in approval_required if not _recovered_by_later_success(item)
        ]
        if unrecovered_approvals:
            ids = tuple(
                sorted({eid for item in unrecovered_approvals for eid in item.evidence_ids})
            )
            return ReceiptCompletion(False, "approval_required", ids, observed_stage)
        blocked = [item for item in receipts if item.status == "blocked"]
        if blocked:
            ids = tuple(sorted({eid for item in blocked for eid in item.evidence_ids}))
            return ReceiptCompletion(False, "action_blocked", ids, observed_stage)
        recoverable_blocked = [item for item in receipts if item.status == "recoverable_blocked"]
        unrecovered_blocked = [
            item for item in recoverable_blocked if not _recovered_by_later_success(item)
        ]
        if unrecovered_blocked:
            ids = tuple(sorted({eid for item in unrecovered_blocked for eid in item.evidence_ids}))
            return ReceiptCompletion(False, "action_blocked", ids, observed_stage)
        failed = [item for item in receipts if item.status == "failed"]
        unrecovered_failed = [
            item for item in failed if not _recovered_by_later_success(item)
        ]
        if unrecovered_failed:
            ids = tuple(sorted({eid for item in unrecovered_failed for eid in item.evidence_ids}))
            return ReceiptCompletion(False, "failed_action_present", ids, observed_stage)
        recovered = bool(approval_required or recoverable_blocked or failed)
        mutations = [
            item for item in receipts
            if (
                item.action_class in self._MUTATION_CLASSES
                or (
                    item.action_class == "external_egress"
                    and item.tool_name in self._EXTERNAL_MUTATION_TOOLS
                )
            )
            and item.status == "succeeded"
        ]
        if mutations:
            last_mutation_sequence = max(item.sequence for item in mutations)
            recovery_evidence = {
                evidence_id
                for item in (*approval_required, *recoverable_blocked, *failed)
                for evidence_id in item.evidence_ids
            }
            verified = any(
                item.action_class == "verification"
                and item.status == "succeeded"
                and item.sequence > last_mutation_sequence
                and (
                    not recovery_evidence
                    or bool(recovery_evidence & set(item.evidence_ids))
                )
                for item in receipts
            )
            if not verified:
                ids = tuple(sorted({eid for item in mutations for eid in item.evidence_ids}))
                return ReceiptCompletion(
                    False,
                    "verification_receipt_missing",
                    ids,
                    observed_stage,
                )
        return ReceiptCompletion(
            True,
            "recovered_and_verified" if recovered else "verified_or_not_required",
            stage_evidence_ids,
            observed_stage,
        )
