"""Goal/actual gap loop for Sinria practical self-improvement.

This module records sanitized per-turn outcome metadata and, when a practical
completion gap is detected, proposes a review-gated Correction Loop evidence
candidate. It intentionally stores categories and source pointers rather than raw
conversation content.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agent.failure_events import FailureEnvelope
from sinria_constants import get_sinria_home

from .completion_evidence import CompletionStage, completion_stage_at_least
from .evidence import ContextEvidence
from .extraction import EvidenceCandidate
from .review_queue import append_candidate_deduplicated
from agent.privacy.sanitization import assert_safe_identifier, assert_sanitized_text

OUTCOME_GAP_RELATIVE_PATH = Path("corrections") / "outcome_gap.jsonl"

GoalKind = Literal["practical_action", "question", "status", "creative", "unknown"]
ActualKind = Literal[
    "verified_practical_completion",
    "claimed_without_visible_verification",
    "incomplete_or_blocked",
    "failed_or_interrupted",
    "answered_question",
    "unknown",
]
CauseKind = Literal[
    "none",
    "verification_gap",
    "execution_incomplete",
    "interrupted_or_failed",
    "not_practical_action",
]
DurableFixKind = Literal["memory", "skill", "test", "runbook", "code_or_config", "none"]

_ACTION_TERMS = (
    "do", "implement", "fix", "set up", "configure", "build", "make it possible",
    "deploy", "release", "publish",
    "create", "write", "add", "delete",
    "やって", "実装", "修正", "直して", "設定", "構築", "作って", "可能に", "反映", "入れて",
    "作成", "書いて", "保存", "追加", "削除",
)
_QUESTION_TERMS = ("?", "？", "どう", "かな", "教えて", "what", "why", "how")
_COMPLETION_CLAIMS = (
    "done", "completed", "fixed", "implemented", "set up", "configured",
    "完了", "できました", "実装しました", "修正しました", "設定しました", "構築しました", "反映しました",
)
_VERIFICATION_TERMS = (
    "verified", "tested", "smoke", "passed", "ok", "browser", "console", "curl", "pytest",
    "npm test", "typecheck", "build", "確認", "検証", "テスト", "実行", "通過", "再起動", "動作",
)
_UNRESOLVED_BLOCK_RE = re.compile(
    r"\b(?:is|are|still|remains?|was)\s+(?:blocked|incomplete|unverified)\b"
    r"|\bblocked\s+by\b"
    r"|\b(?:cannot|can't|unable\s+to|failed\s+to)\b"
    r"|(?:ブロックされて|ブロック中|未完了(?:です|のまま)?|未検証(?:です|のまま)?|できません|失敗しました|エラー(?:で|により).{0,16}(?:停止|中断|未完了))",
    re.IGNORECASE,
)

# Failure-signature extraction: category tokens only. Error classes are
# CamelCase identifiers ending in a known failure suffix; anything else in a
# tool result (paths, payloads, numbers) never enters the signature.
_ERROR_CLASS_RE = re.compile(r"\b([A-Z][A-Za-z]{2,39}(?:Error|Exception|Timeout|Denied|NotFound|Refused))\b")
_SIGNATURE_TOKEN_MAX = 40
_FAILURE_SIGNATURE_GAP_MARKER = "|sinria_failure_signature="
# Lock files are separate from the append-only ledger so an atomic replace
# never swaps out the inode on which a writer is holding the lock.
_OUTCOME_LEDGER_LOCK_SUFFIX = ".lock"
# Abnormal turn-exit reasons that identify a failure mode even when no single
# tool call errored (the model ran out of budget / produced nothing).
_EXIT_REASON_SIGNATURES = (
    ("max_iterations_reached", "max_iterations"),
    ("budget_exhausted", "budget_exhausted"),
    ("all_retries_exhausted_no_response", "model_no_response"),
    ("empty_response_exhausted", "model_no_response"),
    ("error_near_max_iterations", "api_error"),
)


def _signature_tool_token(value: str) -> str:
    return re.sub(r"[^a-z_]", "", (value or "").lower())[:_SIGNATURE_TOKEN_MAX]


def extract_turn_failure_signature(
    messages,
    turn_exit_reason: str = "",
    current_user_message: Any | None = None,
) -> str:
    """Derive a sanitized per-failure-mode signature from one turn's messages.

    ``messages`` may contain persisted history from earlier turns. When the
    current user message is supplied, only tool results after the last matching
    real user message are considered; if that boundary cannot be found, the
    function fails closed to no tool signature rather than attributing a stale
    failure to the current turn.

    Returns ``tool=<name>:cls=<class>`` for the most frequent failing tool call
    (reusing the deterministic ``classify_tool_failure`` mirror so this never
    disagrees with the CLI's ``[error]`` tag), ``exit=<token>`` for abnormal
    turn exits without a tool failure, or ``""`` for clean turns. Digit-free
    category tokens only — raw tool output never enters the signature.
    """
    try:
        from agent.tool_guardrails import classify_tool_failure
    except Exception:
        return ""
    scoped_messages = list(messages or [])
    if current_user_message is not None and any(
        isinstance(message, dict) and message.get("role") == "user"
        for message in scoped_messages
    ):
        scoped_messages = []
        source_messages = list(messages or [])
        for index in range(len(source_messages) - 1, -1, -1):
            message = source_messages[index]
            if (
                isinstance(message, dict)
                and message.get("role") == "user"
                and not message.get("_strategist_plan")
                and message.get("content") == current_user_message
            ):
                scoped_messages = source_messages[index + 1 :]
                break
    failures: dict[tuple[str, str], int] = {}
    order: list[tuple[str, str]] = []
    for message in scoped_messages:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        name = str(message.get("name") or "unknown")
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            failed, _ = classify_tool_failure(name, content)
        except Exception:
            failed = False
        if not failed:
            continue
        tool_token = _signature_tool_token(name) or "unknown"
        policy_reason = ""
        if content.lstrip().startswith("{"):
            try:
                payload = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                payload = None
            if (
                isinstance(payload, dict)
                and str(payload.get("error") or "").startswith("Correction Loop ")
            ):
                policy_reason = _signature_tool_token(str(payload.get("reason_code") or ""))
        if policy_reason:
            cls_token = policy_reason
        elif name == "terminal":
            # Exit codes are digits; the fixed token keeps signatures digit-free.
            cls_token = "nonzero_exit"
        else:
            match = _ERROR_CLASS_RE.search(content[:500])
            cls_token = (
                re.sub(r"[^A-Za-z_]", "", match.group(1))[:_SIGNATURE_TOKEN_MAX]
                if match
                else "toolerror"
            )
        key = (tool_token, cls_token)
        if key not in failures:
            order.append(key)
        failures[key] = failures.get(key, 0) + 1
    if failures:
        tool_token, cls_token = max(order, key=lambda key: failures[key])
        return f"tool={tool_token}:cls={cls_token}"
    reason = turn_exit_reason or ""
    for prefix, token in _EXIT_REASON_SIGNATURES:
        if reason.startswith(prefix):
            return f"exit={token}"
    return ""


def outcome_gap_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / OUTCOME_GAP_RELATIVE_PATH


@contextmanager
def _outcome_ledger_lock(target: Path):
    """Serialize new writers and one-time migrations across Sinria worktrees."""

    lock_path = target.with_name(target.name + _OUTCOME_LEDGER_LOCK_SUFFIX)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _fsync_directory(path: Path) -> None:
    """Best-effort durability barrier for a newly created/replaced directory entry."""

    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


def _encode_legacy_compatible_outcome_row(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Move a PR-105 signature into a field understood by older runtimes."""

    encoded = dict(data)
    removed_envelope = encoded.pop("failure_envelope", None) is not None
    if "failure_signature" not in encoded:
        return encoded, removed_envelope
    signature = str(encoded.pop("failure_signature", "") or "")
    assert_sanitized_text(signature, field="failure_signature")
    gap_summary = str(encoded.get("gap_summary", "") or "")
    _base_summary, marker, embedded_signature = gap_summary.rpartition(_FAILURE_SIGNATURE_GAP_MARKER)
    if marker:
        if signature and signature != embedded_signature:
            raise ValueError("conflicting failure signatures in outcome-gap row")
        if embedded_signature:
            assert_sanitized_text(embedded_signature, field="failure_signature")
        encoded["gap_summary"] = gap_summary
    elif signature:
        encoded["gap_summary"] = f"{gap_summary}{_FAILURE_SIGNATURE_GAP_MARKER}{signature}"
    return encoded, True


def _decode_outcome_row(data: dict[str, Any]) -> dict[str, Any]:
    """Accept legacy, transitional PR-105, and current embedded rows."""

    decoded = dict(data)
    decoded.pop("failure_envelope", None)
    transitional_signature = str(decoded.pop("failure_signature", "") or "")
    persisted_gap_summary = str(decoded.get("gap_summary", "") or "")
    base_summary, marker, embedded_signature = persisted_gap_summary.rpartition(
        _FAILURE_SIGNATURE_GAP_MARKER
    )
    if marker:
        if transitional_signature and transitional_signature != embedded_signature:
            raise ValueError("conflicting failure signatures in outcome-gap row")
        decoded["gap_summary"] = base_summary
        transitional_signature = embedded_signature
    assert_sanitized_text(transitional_signature, field="failure_signature")
    decoded["failure_signature"] = transitional_signature
    return decoded


@dataclass(frozen=True)
class PracticalOutcomeRecord:
    record_id: str
    session_id: str
    timestamp: str
    platform: str
    model: str
    provider: str
    goal_kind: GoalKind
    actual_kind: ActualKind
    cause_kind: CauseKind
    gap_summary: str
    gap_detected: bool
    durable_fix_kinds: list[DurableFixKind]
    source_turn_ref: str
    raw_context_stored: bool = False
    external_action_performed: bool = False
    human_review_required: bool = True
    failure_signature: str = ""

    def __post_init__(self) -> None:
        assert_safe_identifier(self.record_id, field="record_id")
        assert_safe_identifier(self.session_id, field="session_id")
        assert_sanitized_text(self.platform, field="platform")
        assert_sanitized_text(self.model, field="model")
        assert_sanitized_text(self.provider, field="provider")
        assert_safe_identifier(self.source_turn_ref, field="source_turn_ref")
        assert_sanitized_text(self.failure_signature, field="failure_signature")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.gap_detected:
            failure_class = self.failure_signature or self.actual_kind
            retryability = (
                "backoff"
                if self.failure_signature.startswith(("tool=", "exit="))
                else "human_review"
            )
            data["failure_envelope"] = FailureEnvelope.create(
                provider=self.provider,
                failure_class=failure_class,
                run_id=self.session_id,
                root_event_id=self.source_turn_ref,
                retryability=retryability,
            ).to_dict()
        return data

    def to_persisted_dict(self) -> dict[str, Any]:
        """Return the strict shared-ledger wire shape understood by old worktrees."""

        encoded, _ = _encode_legacy_compatible_outcome_row(asdict(self))
        return encoded


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                content = item.get("text") or item.get("content") or ""
                if isinstance(content, str):
                    parts.append(content)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(value or "")


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def classify_goal(user_message: Any) -> GoalKind:
    text = _text(user_message)
    if _contains_any(text, _QUESTION_TERMS) or re.search(
        r"^\s*(?:deploy|deployment|release|production)\s+status\b",
        text,
        re.IGNORECASE,
    ):
        return "question"
    if _contains_any(text, _ACTION_TERMS):
        return "practical_action"
    return "unknown"


def classify_actual(*, final_response: str | None, completed: bool, interrupted: bool, tool_turn_count: int = 0) -> ActualKind:
    response = final_response or ""
    if interrupted or not completed:
        return "failed_or_interrupted"
    if _UNRESOLVED_BLOCK_RE.search(response):
        return "incomplete_or_blocked"
    claims_completion = _contains_any(response, _COMPLETION_CLAIMS)
    cites_verification = _contains_any(response, _VERIFICATION_TERMS)
    if claims_completion and cites_verification and tool_turn_count > 0:
        return "verified_practical_completion"
    if claims_completion and not cites_verification:
        return "claimed_without_visible_verification"
    if claims_completion and tool_turn_count == 0:
        # Completion claimed with verification WORDS but zero tool executions:
        # the "verification" was never performed inside the turn — fabricated
        # evidence is still an unverified claim, not an answered question.
        return "claimed_without_visible_verification"
    if _contains_any(response, _QUESTION_TERMS) or response.strip():
        return "answered_question"
    return "unknown"


def verify_nudges_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / "corrections" / "verify_nudges.jsonl"


def record_verify_nudge_event(
    *,
    session_id: str | None,
    model: str | None,
    provider: str | None,
    tier: str,
    path: Path | None = None,
) -> Path:
    """Append a sanitized verify-after-act nudge event (metadata only).

    Feeds the 運用観察 measurement cron: nudge frequency and the
    nudge→verified conversion rate join these events with the outcome
    records by session and time. Never stores conversation text.
    """
    target = path or verify_nudges_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "session_id": session_id or "",
        "model": model or "",
        "provider": provider or "",
        "tier": tier,
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return target


def should_nudge_verification(
    *,
    user_message: Any,
    final_response: str | None,
    tool_turn_count: int,
) -> bool:
    """True when a practical-action turn claims completion without citing verification.

    The conversation loop uses this to give the model one bounded
    verification nudge before accepting the answer (verify-after-act,
    architecture-centric P1). Tool-less turns never nudge: with no tool
    activity there is nothing the model could re-verify.
    """
    if tool_turn_count <= 0:
        return False
    if classify_goal(user_message) != "practical_action":
        return False
    actual = classify_actual(
        final_response=final_response,
        completed=True,
        interrupted=False,
        tool_turn_count=tool_turn_count,
    )
    return actual == "claimed_without_visible_verification"


# Receipt reason codes that require bounded same-session recovery before
# carryover is permitted. Tool failure alone is recoverable: the continuation
# must inspect the failure and use a safe alternative rather than blindly retry.
# Explicit human approval is a carryover exception and must not busy-retry.
_RECOVERABLE_RECEIPT_REASONS = frozenset(
    {
        "verification_receipt_missing",
        "action_blocked",
        "recoverable_blocked",
        "failed_action_present",
    }
)
_CARRYOVER_RECEIPT_REASONS = frozenset({"approval_required"})
_TERMINAL_RECEIPT_REASONS: frozenset[str] = frozenset()

_PRODUCTION_ACTION_RE = re.compile(
    r"(?:"
    r"(?:本番|production|\bprod\b).{0,16}(?:へ|に|で)?\s*"
    r"(?:反映|デプロイ|投入|公開|リリース|再起動|更新)"
    r"(?:して|する|まで|してください|を実行)"
    r"|\b(?:deploy|release|publish|roll out|promote|restart)\b.{0,24}"
    r"\b(?:to|in)\s+(?:production|prod)\b"
    r")",
    re.IGNORECASE,
)
_DEPLOYED_ACTION_RE = re.compile(
    r"(?:デプロイ|リリース)(?:して|する|まで|してください)"
    r"|\b(?:deploy|release|publish|roll out|promote)\b",
    re.IGNORECASE,
)
_MERGED_STAGE_RE = re.compile(
    r"(?:マージ(?:して|する|まで|してください)|\bmerge\b(?!\s+(?:can|could|how)))",
    re.IGNORECASE,
)
_APPROVED_STAGE_RE = re.compile(
    r"(?:承認済みにして|承認して|\bapprove\b)", re.IGNORECASE
)
_REVIEW_READY_STAGE_RE = re.compile(
    r"(?:レビュー可能な状態にして|review[_ -]?ready|プルリク(?:を)?作成して|\bopen (?:a )?(?:PR|pull request)\b)",
    re.IGNORECASE,
)
_VALIDATED_STAGE_RE = re.compile(
    r"(?:"
    r"(?:実装|修正|構築|作成|設定|テスト|検証)(?:して|する|まで|してください|を(?:完了|実行)して)"
    r"|変更(?:して|する|まで|してください|を適用して)"
    r"|\b(?:implement|fix|edit|build|configure|test|verify)\b"
    r")",
    re.IGNORECASE,
)
_ENGLISH_QUESTION_RE = re.compile(
    r"(?:\bcan you\b|\bcould you\b|\bwould you\b|\bshould i\b|"
    r"\bis it safe\b|\bdo (?:i|we) need\b|\bhow (?:do|can|to)\b|\bwhat is\b)",
    re.IGNORECASE,
)
_NEGATED_DELIVERY_RE = re.compile(
    r"(?:"
    r"(?:本番|production|\bprod\b|デプロイ|\bdeploy\b).{0,20}"
    r"(?:しない|しません|せず|しないで|行わない|実施しない|投入しない|禁止|未反映|not|don't|do not)"
    r"|(?:not|don't|do not|never).{0,20}(?:production|\bprod\b|\bdeploy\b)"
    r")",
    re.IGNORECASE,
)


def infer_required_completion_stage(user_message: Any) -> CompletionStage | None:
    """Infer only an explicitly requested delivery boundary."""
    text = _text(user_message)
    if _ENGLISH_QUESTION_RE.search(text):
        return None
    delivery_negated = bool(_NEGATED_DELIVERY_RE.search(text))
    if _PRODUCTION_ACTION_RE.search(text) and not delivery_negated:
        return CompletionStage.PRODUCTION_VERIFIED
    if _MERGED_STAGE_RE.search(text):
        return CompletionStage.MERGED
    if _APPROVED_STAGE_RE.search(text):
        return CompletionStage.APPROVED
    if _REVIEW_READY_STAGE_RE.search(text):
        return CompletionStage.REVIEW_READY
    if _DEPLOYED_ACTION_RE.search(text) and not delivery_negated:
        return CompletionStage.DEPLOYED
    if _VALIDATED_STAGE_RE.search(text):
        return CompletionStage.VALIDATED
    return None


def assess_practical_completion(
    *,
    user_message: Any,
    final_response: str | None,
    completed: bool,
    interrupted: bool,
    tool_turn_count: int = 0,
    receipt_completion: Any = None,
    required_stage: CompletionStage | str | None = None,
) -> dict[str, Any] | None:
    """Return a structured completion decision for a finished turn.

    This is the *source of truth* for whether a practical action really
    completed — separate from "the model produced a final sentence".  It is
    receipt-driven: an explicit Correction Loop ``ReceiptCompletion`` outranks any
    completion words in the response text, because "verified" in prose is not a
    receipt.

    Returns ``None`` when the turn's ``completed`` flag should be left as-is
    (non-practical Q&A, interrupted turns, verified practical actions, or turns
    with no deterministic evidence to downgrade).  Otherwise returns a JSON-safe
    dict::

        {
            "completed": False,
            "reason": {
                "code": "practical_receipt_missing",
                "kind": "recoverable" | "terminal",
                "receipt_reason": "<ReceiptCompletion.reason_code>",
                "evidence_ids": [...],  # sanitized sha256/evidence ids only
            },
        }

    The dict never contains raw tool output, payloads, or credentials.
    """
    if interrupted:
        return None
    if classify_goal(user_message) != "practical_action":
        return None
    required = CompletionStage(required_stage) if required_stage is not None else None
    if required is not None and receipt_completion is None:
        return {
            "completed": False,
            "reason": {
                "code": "practical_receipt_incomplete",
                "kind": "recoverable",
                "receipt_reason": "completion_stage_receipt_missing",
                "evidence_ids": [],
                "required_stage": required.value,
            },
        }

    observed = CompletionStage(
        getattr(receipt_completion, "stage", CompletionStage.IMPLEMENTED)
    )
    if (
        required is not None
        and receipt_completion is not None
        and getattr(receipt_completion, "complete", True)
        and not completion_stage_at_least(observed, required)
    ):
        return {
            "completed": False,
            "reason": {
                "code": "practical_receipt_incomplete",
                "kind": "recoverable",
                "receipt_reason": "completion_stage_requirement_unmet",
                "evidence_ids": list(
                    getattr(receipt_completion, "evidence_ids", ()) or ()
                ),
                "observed_stage": observed.value,
                "required_stage": required.value,
            },
        }

    # An explicit receipt gate is the only signal strong enough to flip a
    # "completed" turn to incomplete.  Without a required delivery stage, a
    # missing receipt remains on the legacy text-guard path.
    if receipt_completion is None:
        return None
    if getattr(receipt_completion, "complete", True):
        return None
    receipt_reason = str(getattr(receipt_completion, "reason_code", "") or "receipt_incomplete")
    if receipt_reason in _CARRYOVER_RECEIPT_REASONS:
        kind = "carryover"
    elif receipt_reason in _TERMINAL_RECEIPT_REASONS:
        kind = "terminal"
    elif receipt_reason in _RECOVERABLE_RECEIPT_REASONS:
        kind = "recoverable"
    else:
        # Unknown incomplete receipt reason: treat as recoverable-but-bounded so
        # a follow-up turn can inspect state, but never as terminal (which would
        # strand work) or as complete (which is the original bug).
        kind = "recoverable"
    evidence_ids = [
        str(eid)
        for eid in (getattr(receipt_completion, "evidence_ids", ()) or ())
        if str(eid)
    ]
    return {
        "completed": False,
        "reason": {
            "code": "practical_receipt_incomplete",
            "kind": kind,
            "receipt_reason": receipt_reason,
            "evidence_ids": evidence_ids,
            "observed_stage": observed.value,
            **({"required_stage": required.value} if required is not None else {}),
        },
    }


def apply_practical_completion_guard(
    *,
    user_message: Any,
    final_response: str | None,
    completed: bool,
    interrupted: bool,
    tool_turn_count: int = 0,
    receipt_completion: Any = None,
    required_stage: CompletionStage | str | None = None,
) -> str | None:
    """Prevent unverified practical-completion claims from reaching the user as final.

    This is the execution-side counterpart to the outcome-gap recorder.  The
    recorder can learn from bad turns after the fact, but the recurrence class
    "claimed_without_visible_verification" should be stopped before delivery
    whenever deterministic metadata shows a practical action response claims
    completion without citing real workflow verification.
    """
    if final_response is None:
        return None
    if classify_goal(user_message) != "practical_action":
        return final_response
    actual_kind = classify_actual(
        final_response=final_response,
        completed=completed,
        interrupted=interrupted,
        tool_turn_count=tool_turn_count,
    )
    claims_completion = actual_kind in {
        "claimed_without_visible_verification",
        "verified_practical_completion",
    } or _contains_any(final_response, _COMPLETION_CLAIMS)
    required = CompletionStage(required_stage) if required_stage is not None else None
    observed = CompletionStage(
        getattr(receipt_completion, "stage", CompletionStage.IMPLEMENTED)
    )
    stage_gap = (
        required is not None
        and (
            receipt_completion is None
            or not completion_stage_at_least(observed, required)
        )
    )
    if claims_completion and stage_gap:
        return (
            "実行完了とは確認できません。Sinriaの完遂段階が不足しています "
            f"({observed.value} → {required.value}).\n\n{final_response}"
        )
    if (
        claims_completion
        and receipt_completion is not None
        and not getattr(receipt_completion, "complete", True)
    ):
        reason = getattr(receipt_completion, "reason_code", "receipt_incomplete")
        evidence_ids = tuple(getattr(receipt_completion, "evidence_ids", ()) or ())
        source = f" Evidence: {', '.join(evidence_ids)}." if evidence_ids else ""
        return (
            "実行完了とは確認できません。Correction Loopの実行証跡が未完了です "
            f"({reason}).{source}\n\n"
            f"{final_response}"
        )
    if actual_kind != "claimed_without_visible_verification":
        if (
            claims_completion
            and required is not None
            and observed is not CompletionStage.PRODUCTION_VERIFIED
            and "Sinria status:" not in final_response
        ):
            return (
                f"Sinria status: {observed.value}（本番反映は未実施）\n\n"
                f"{final_response}"
            )
        return final_response
    if "Sinria verification gate" in final_response:
        return final_response
    return (
        f"{final_response.rstrip()}\n\n"
        "⚠️ Sinria verification gate: この完了主張は未検証です。"
        "実ワークフローの確認結果が提示されていないため、完了ではなく未完了/要検証として扱います。"
    )


def _durable_fix_kinds(goal_kind: GoalKind, actual_kind: ActualKind) -> list[DurableFixKind]:
    if goal_kind != "practical_action":
        return ["none"]
    if actual_kind == "claimed_without_visible_verification":
        return ["skill", "test", "runbook"]
    if actual_kind in {"incomplete_or_blocked", "failed_or_interrupted"}:
        return ["test", "runbook", "code_or_config"]
    return ["none"]


def classify_cause(goal_kind: GoalKind, actual_kind: ActualKind) -> CauseKind:
    """Classify the durable cause category without storing raw turn text."""
    if goal_kind != "practical_action":
        return "not_practical_action"
    if actual_kind == "claimed_without_visible_verification":
        return "verification_gap"
    if actual_kind == "incomplete_or_blocked":
        return "execution_incomplete"
    if actual_kind == "failed_or_interrupted":
        return "interrupted_or_failed"
    return "none"


def _gap_summary(goal_kind: GoalKind, actual_kind: ActualKind, cause_kind: CauseKind) -> str:
    return f"{goal_kind}:{actual_kind}:{cause_kind}"


def assess_practical_outcome(
    *,
    session_id: str | None,
    user_message: Any,
    final_response: str | None,
    completed: bool,
    interrupted: bool,
    model: str | None = None,
    provider: str | None = None,
    platform: str | None = None,
    tool_turn_count: int = 0,
    now: datetime | None = None,
    messages: list | None = None,
    turn_exit_reason: str = "",
) -> PracticalOutcomeRecord:
    session = re.sub(r"[^A-Za-z0-9_.:-]", "-", session_id or "unknown-session")[:96] or "unknown-session"
    goal_kind = classify_goal(user_message)
    actual_kind = classify_actual(final_response=final_response, completed=completed, interrupted=interrupted, tool_turn_count=tool_turn_count)
    # A practical action request that ends as a generic answer is still a gap:
    # the user asked Sinria to change the world, not merely explain.  Without
    # this normalization, "構築して" / "実装して" turns that stop at a plan or
    # conceptual answer are mislabeled as no-gap answered_question records and
    # the self-improvement loop never learns from the practical-completion miss.
    if goal_kind == "practical_action" and actual_kind == "answered_question":
        actual_kind = "incomplete_or_blocked"
    gap_detected = goal_kind == "practical_action" and actual_kind in {
        "claimed_without_visible_verification",
        "incomplete_or_blocked",
        "failed_or_interrupted",
    }
    cause_kind = classify_cause(goal_kind, actual_kind)
    failure_signature = ""
    if gap_detected:
        try:
            failure_signature = extract_turn_failure_signature(
                messages,
                turn_exit_reason=turn_exit_reason,
                current_user_message=user_message,
            )
        except Exception:
            failure_signature = ""
    ts = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    digest = hashlib.sha256(f"{session}\n{ts}\n{goal_kind}\n{actual_kind}".encode("utf-8")).hexdigest()[:16]
    safe_digest = digest.translate(str.maketrans("0123456789", "abcdefghij"))
    return PracticalOutcomeRecord(
        record_id=f"outcome-gap-{safe_digest}",
        session_id=session,
        timestamp=ts,
        platform=(platform or "unknown")[:80],
        model=(model or "unknown")[:80],
        provider=(provider or "unknown")[:80],
        goal_kind=goal_kind,
        actual_kind=actual_kind,
        cause_kind=cause_kind,
        gap_summary=_gap_summary(goal_kind, actual_kind, cause_kind),
        gap_detected=gap_detected,
        durable_fix_kinds=_durable_fix_kinds(goal_kind, actual_kind),
        # Use the digit-free safe digest here too. The shared safety guard
        # treats long numeric-looking strings as potential identifiers/phone
        # numbers, so raw hex digests can intermittently fail depending on
        # their random digit runs.
        source_turn_ref=f"turn:{safe_digest}",
        human_review_required=gap_detected,
        failure_signature=failure_signature,
    )


def _candidate_for_record(record: PracticalOutcomeRecord) -> EvidenceCandidate:
    cid = f"ctx-candidate-{record.record_id.replace('outcome-gap-', '')}"
    if record.failure_signature:
        # Signature-specific class: dedup_key includes summary/sample, so each
        # failure mode accumulates its own occurrence_count instead of merging
        # into one information-free mega-candidate.
        summary = (
            f"Recurring practical-failure signature {record.failure_signature}: practical action "
            "turns repeatedly fail at this failure mode; propose a durable fix (test/runbook/code_or_config)."
        )
        sample_prefix = f"signature={record.failure_signature}; "
    else:
        summary = (
            "Practical-completion gap detected: a practical action request ended without "
            "visible real-workflow verification; apply Goal→Actual→Gap→Cause→Durable Fix."
        )
        sample_prefix = ""
    return EvidenceCandidate(
        candidate_id=cid,
        evidence=ContextEvidence(
            evidence_id=cid.replace("ctx-candidate-", "ctx-ev-"),
            source_session_id=record.session_id,
            source_kind="workflow_outcome",
            scope="project",
            summary=summary,
            sanitized_sample=f"{sample_prefix}goal_kind={record.goal_kind}; actual_kind={record.actual_kind}; cause_kind={record.cause_kind}; fixes={','.join(record.durable_fix_kinds)}",
            sensitivity="internal",
            applies_to=["self_improvement", "practical_completion", "correction_loop"],
            valid_from=record.timestamp,
            confidence=0.88,
            human_approved=False,
        ),
        approval_state="proposed",
        raw_context_stored=False,
        external_action_performed=False,
        extraction_reason="goal_actual_gap_practical_completion_loop",
    )


def append_outcome_record(record: PracticalOutcomeRecord, *, path: Path | None = None) -> Path:
    target = path or outcome_gap_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _outcome_ledger_lock(target):
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_persisted_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return target


def migrate_outcome_gap_ledger(
    *,
    path: Path | None = None,
    apply: bool = False,
    backup_path: Path | None = None,
) -> dict[str, Any]:
    """Migrate transitional PR-105 rows to the legacy-compatible wire schema.

    The default is a read-only preview. Applying the migration creates an
    exclusive byte-for-byte backup and atomically replaces the ledger while
    holding the same sidecar lock used by current appenders. Any malformed row,
    unsafe signature, or conflicting representation aborts before mutation.
    """

    target = path or outcome_gap_path()
    with _outcome_ledger_lock(target):
        if not target.exists():
            raise FileNotFoundError(target)
        real_target = target.resolve() if target.is_symlink() else target
        original = real_target.read_bytes()
        try:
            text = original.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("outcome-gap ledger is not valid UTF-8") from exc

        rendered_lines: list[str] = []
        rows_scanned = 0
        rows_changed = 0
        for line_number, raw_line in enumerate(text.splitlines(keepends=True), start=1):
            payload = raw_line.rstrip("\r\n")
            newline = raw_line[len(payload):]
            if not payload.strip():
                rendered_lines.append(raw_line)
                continue
            rows_scanned += 1
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid outcome-gap JSON at line {line_number}") from exc
            if not isinstance(data, dict):
                raise ValueError(f"outcome-gap row at line {line_number} is not an object")
            try:
                encoded, changed = _encode_legacy_compatible_outcome_row(data)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid outcome-gap row at line {line_number}: {exc}") from exc
            if changed:
                rows_changed += 1
                rendered_lines.append(
                    json.dumps(encoded, ensure_ascii=False, sort_keys=True) + newline
                )
            else:
                rendered_lines.append(raw_line)

        report: dict[str, Any] = {
            "target": str(target),
            "rows_scanned": rows_scanned,
            "rows_changed": rows_changed,
            "applied": False,
            "backup_path": None,
        }
        if not apply or rows_changed == 0:
            return report

        if backup_path is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = target.with_name(f"{target.name}.pre-pr105-forward-compat.{stamp}.bak")
        else:
            backup = backup_path
        backup.parent.mkdir(parents=True, exist_ok=True)
        mode = stat.S_IMODE(real_target.stat().st_mode)
        with backup.open("xb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(backup, mode)
        _fsync_directory(backup.parent)

        replacement = "".join(rendered_lines).encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(
            dir=str(real_target.parent),
            prefix=f".{real_target.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(replacement)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, mode)
            os.replace(tmp_name, real_target)
            _fsync_directory(real_target.parent)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

        report["applied"] = True
        report["backup_path"] = str(backup)
        return report


def load_outcome_records(*, path: Path | None = None) -> list[PracticalOutcomeRecord]:
    target = path or outcome_gap_path()
    if not target.exists():
        return []
    records: list[PracticalOutcomeRecord] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            data = _decode_outcome_row(json.loads(stripped))
            if "cause_kind" not in data:
                data["cause_kind"] = classify_cause(data.get("goal_kind", "unknown"), data.get("actual_kind", "unknown"))
            if "gap_summary" not in data:
                data["gap_summary"] = _gap_summary(data.get("goal_kind", "unknown"), data.get("actual_kind", "unknown"), data["cause_kind"])
            records.append(PracticalOutcomeRecord(**data))
    return records


def record_outcome_unit_miss_candidate(
    *,
    session_id: str | None,
    os_id: str,
    app_module_id: str | None,
    outcome_id: str,
    goal_summary: str,
    actual_summary: str,
    review_queue_path: Path | None = None,
) -> EvidenceCandidate:
    """Queue a review-gated improvement candidate for a missed OutcomeUnit.

    Stores only identifiers and outcome categories. Human-readable goal/actual
    text is accepted so callers can perform local reasoning, but it is not copied
    into the cloud/shareable sanitized_sample.
    """
    session = re.sub(r"[^A-Za-z0-9_.:-]", "-", session_id or "unknown-session")[:96] or "unknown-session"
    for field, value in {
        "os_id": os_id,
        "outcome_id": outcome_id,
        "app_module_id": app_module_id or "none",
    }.items():
        assert_safe_identifier(value, field=field)
    assert_sanitized_text(goal_summary[:160], field="goal_summary")
    assert_sanitized_text(actual_summary[:160], field="actual_summary")
    digest_source = f"{session}\n{os_id}\n{app_module_id or 'none'}\n{outcome_id}"
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
    safe_digest = digest.translate(str.maketrans("0123456789", "abcdefghij"))
    cid = f"ctx-candidate-outcome-unit-{safe_digest}"
    candidate = EvidenceCandidate(
        candidate_id=cid,
        evidence=ContextEvidence(
            evidence_id=cid.replace("ctx-candidate-", "ctx-ev-"),
            source_session_id=session,
            source_kind="workflow_outcome",
            scope="project",
            summary="OutcomeUnit miss detected in review-gated Sinria outcome loop; propose durable skill/test/runbook/code fix after human review.",
            sanitized_sample=f"os_id={os_id}; app_module_id={app_module_id or 'none'}; outcome_id={outcome_id}; status=missed; raw_context_stored=false; external_action_performed=false",
            sensitivity="internal",
            applies_to=["outcome_loop", "self_improvement", "practical_completion"],
            valid_from=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            confidence=0.86,
            human_approved=False,
        ),
        approval_state="proposed",
        raw_context_stored=False,
        external_action_performed=False,
        extraction_reason="outcome_unit_miss_review_gated_improvement",
    )
    append_candidate_deduplicated(candidate, path=review_queue_path)
    return candidate


def record_practical_outcome_and_candidates(
    *,
    session_id: str | None,
    user_message: Any,
    final_response: str | None,
    completed: bool,
    interrupted: bool,
    model: str | None = None,
    provider: str | None = None,
    platform: str | None = None,
    tool_turn_count: int = 0,
    outcome_path: Path | None = None,
    review_queue_path: Path | None = None,
    messages: list | None = None,
    turn_exit_reason: str = "",
    defects_path: Path | None = None,
) -> PracticalOutcomeRecord:
    record = assess_practical_outcome(
        session_id=session_id,
        user_message=user_message,
        final_response=final_response,
        completed=completed,
        interrupted=interrupted,
        model=model,
        provider=provider,
        platform=platform,
        tool_turn_count=tool_turn_count,
        messages=messages,
        turn_exit_reason=turn_exit_reason,
    )
    append_outcome_record(record, path=outcome_path)
    if record.gap_detected:
        append_candidate_deduplicated(_candidate_for_record(record), path=review_queue_path)
    # Repair-loop bridge: a gap turn whose signature names a failing tool becomes
    # tool_error_result telemetry on the code_defects surface, so real-use tool
    # failures aggregate per fingerprint and reach the nightly repair intake.
    # Best-effort and fail-closed (pseudo-repo issue-proposal lane by default).
    if record.gap_detected and record.failure_signature.startswith("tool="):
        try:
            from agent.defect_capture import (
                record_turn_tool_error_defect,
                repair_telemetry_enabled,
                turn_signal_tickets_enabled,
            )

            if repair_telemetry_enabled():
                tool_part, _, cls_part = record.failure_signature.partition(":cls=")
                record_turn_tool_error_defect(
                    tool_name=tool_part.removeprefix("tool="),
                    error_class=cls_part or "toolerror",
                    ticket_eligible=turn_signal_tickets_enabled(),
                    path=defects_path,
                )
        except Exception:
            pass
    # Open-world correction capture shares this per-turn choke point but must
    # never break outcome recording; capture failures are silently dropped
    # (the outcome record above is already persisted).
    try:
        from .correction_capture import record_correction_candidate

        record_correction_candidate(
            user_message,
            session_id=session_id,
            review_queue_path=review_queue_path,
        )
    except Exception:
        pass
    # Built-in loop-close (P1): on a throttled cadence (default once/day), promote
    # recurring low-risk classes into durable, resolver-visible evidence so the
    # self-improvement loop closes by default for every Sinria install — no manual
    # cron/launchd setup. This shares the universal per-turn seam (CLI/gateway/cron
    # all pass through here); it is throttled and best-effort so it never adds
    # meaningful per-turn cost and never breaks a turn.
    try:
        from .loop_maintenance import run_builtin_promotion_if_due

        run_builtin_promotion_if_due(review_queue_path=review_queue_path)
    except Exception:
        pass
    return record
