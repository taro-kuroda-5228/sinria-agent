"""Goal/actual gap loop for Sinria practical self-improvement.

This module records sanitized per-turn outcome metadata and, when a practical
completion gap is detected, proposes a review-gated Context Share evidence
candidate. It intentionally stores categories and source pointers rather than raw
conversation content.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from hermes_constants import get_sinria_home

from .evidence import ContextEvidence
from .extraction import EvidenceCandidate
from .review_queue import append_candidate_deduplicated
from .safety import assert_safe_identifier, assert_sanitized_text

OUTCOME_GAP_RELATIVE_PATH = Path("context_share") / "outcome_gap.jsonl"

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
    "やって", "実装", "修正", "直して", "設定", "構築", "作って", "可能に", "反映", "入れて",
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
_BLOCKED_TERMS = ("blocked", "failed", "error", "incomplete", "未完了", "失敗", "エラー", "ブロック", "未検証")


def outcome_gap_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / OUTCOME_GAP_RELATIVE_PATH


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

    def __post_init__(self) -> None:
        assert_safe_identifier(self.record_id, field="record_id")
        assert_safe_identifier(self.session_id, field="session_id")
        assert_sanitized_text(self.platform, field="platform")
        assert_sanitized_text(self.model, field="model")
        assert_sanitized_text(self.provider, field="provider")
        assert_safe_identifier(self.source_turn_ref, field="source_turn_ref")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    if _contains_any(text, _ACTION_TERMS):
        return "practical_action"
    if _contains_any(text, _QUESTION_TERMS):
        return "question"
    return "unknown"


def classify_actual(*, final_response: str | None, completed: bool, interrupted: bool, tool_turn_count: int = 0) -> ActualKind:
    response = final_response or ""
    if interrupted or not completed:
        return "failed_or_interrupted"
    if _contains_any(response, _BLOCKED_TERMS):
        return "incomplete_or_blocked"
    claims_completion = _contains_any(response, _COMPLETION_CLAIMS)
    cites_verification = _contains_any(response, _VERIFICATION_TERMS)
    if claims_completion and cites_verification and tool_turn_count > 0:
        return "verified_practical_completion"
    if claims_completion and not cites_verification:
        return "claimed_without_visible_verification"
    if _contains_any(response, _QUESTION_TERMS) or response.strip():
        return "answered_question"
    return "unknown"


def verify_nudges_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / "context_share" / "verify_nudges.jsonl"


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


def apply_practical_completion_guard(
    *,
    user_message: Any,
    final_response: str | None,
    completed: bool,
    interrupted: bool,
    tool_turn_count: int = 0,
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
    if classify_actual(
        final_response=final_response,
        completed=completed,
        interrupted=interrupted,
        tool_turn_count=tool_turn_count,
    ) != "claimed_without_visible_verification":
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
    )


def _candidate_for_record(record: PracticalOutcomeRecord) -> EvidenceCandidate:
    cid = f"ctx-candidate-{record.record_id.replace('outcome-gap-', '')}"
    summary = (
        "Practical-completion gap detected: a practical action request ended without "
        "visible real-workflow verification; apply Goal→Actual→Gap→Cause→Durable Fix."
    )
    return EvidenceCandidate(
        candidate_id=cid,
        evidence=ContextEvidence(
            evidence_id=cid.replace("ctx-candidate-", "ctx-ev-"),
            source_session_id=record.session_id,
            source_kind="workflow_outcome",
            scope="project",
            summary=summary,
            sanitized_sample=f"goal_kind={record.goal_kind}; actual_kind={record.actual_kind}; cause_kind={record.cause_kind}; fixes={','.join(record.durable_fix_kinds)}",
            sensitivity="internal",
            applies_to=["self_improvement", "practical_completion", "context_share"],
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
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return target


def load_outcome_records(*, path: Path | None = None) -> list[PracticalOutcomeRecord]:
    target = path or outcome_gap_path()
    if not target.exists():
        return []
    records: list[PracticalOutcomeRecord] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            data = json.loads(stripped)
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
    )
    append_outcome_record(record, path=outcome_path)
    if record.gap_detected:
        append_candidate_deduplicated(_candidate_for_record(record), path=review_queue_path)
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
