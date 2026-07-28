"""Intent resolver for Sinria Context Share v2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Iterable

from .conflicts import (
    EvidenceConflict,
    evidence_overridden_by_current_request,
    resolve_non_conflicting_evidence,
)
from .evidence import ContextEvidence, EvidenceLedger
from .storage import load_durable_evidence

_RECENCY_HALF_LIFE_DAYS = 90.0
_RECENCY_FLOOR = 0.5


def _recency_factor(valid_from: str, *, now: datetime | None = None) -> float:
    """Decay factor for evidence age: 1.0 fresh → 0.5 floor at one half-life."""
    try:
        parsed = datetime.fromisoformat((valid_from or "").replace("Z", "+00:00"))
    except ValueError:
        return _RECENCY_FLOOR
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    age_days = max(0.0, (current - parsed).total_seconds() / 86400.0)
    return max(_RECENCY_FLOOR, 0.5 ** (age_days / _RECENCY_HALF_LIFE_DAYS))

_DEFAULT_EVIDENCE = [
    ContextEvidence(
        evidence_id="sinria-identity-default",
        source_session_id="built-in-context-share-v2",
        source_kind="policy",
        scope="personal",
        summary="Use Sinria-native paths/labels and avoid Hermes residue in user-facing artifacts unless discussing legacy internals.",
        sanitized_sample="Sinria-native identity/path constraint",
        sensitivity="internal",
        applies_to=["sinria", "identity", "context_share"],
        valid_from="2026-06-06T00:00:00Z",
        confidence=0.9,
        human_approved=True,
    ),
    ContextEvidence(
        evidence_id="sinria-team-mode-default",
        source_session_id="built-in-context-share-v2",
        source_kind="policy",
        scope="org",
        summary="Team Mode shares metadata-only Company OS control-plane rows; raw/private context stays local/on-prem.",
        sanitized_sample="metadata-only Team Mode boundary",
        sensitivity="internal",
        applies_to=["team_mode", "company_os", "org_context", "context_share"],
        valid_from="2026-06-06T00:00:00Z",
        confidence=0.9,
        human_approved=True,
    ),
    ContextEvidence(
        evidence_id="sinria-self-improvement-default",
        source_session_id="built-in-context-share-v2",
        source_kind="policy",
        scope="personal",
        summary="Self-improvement must convert repeated prior corrections into memory, skills, tests, and runbooks instead of one-off apologies.",
        sanitized_sample="self-improvement loop constraint",
        sensitivity="internal",
        applies_to=["self_improvement", "context_share", "skills", "memory"],
        valid_from="2026-06-06T00:00:00Z",
        confidence=0.9,
        human_approved=True,
    ),
    ContextEvidence(
        evidence_id="sinria-claude-code-parity-default",
        source_session_id="built-in-context-share-v2",
        source_kind="policy",
        scope="project",
        summary="Claude Code and .claude worktrees are local execution substrates; AGENTS.md/CLAUDE.md/.claude rules must preserve Sinria confidentiality, Context Share, approval-gate, and practical-completion constraints.",
        sanitized_sample="Claude Code follows Sinria repository policy",
        sensitivity="internal",
        applies_to=["claude_code", "local_execution", "context_share", "implementation", "sinria"],
        valid_from="2026-06-11T00:00:00Z",
        confidence=0.9,
        human_approved=True,
    ),
]


@dataclass(frozen=True)
class IntentResolution:
    inferred_intent: str
    applicable_constraints: list[str]
    missing_context_questions: list[str]
    retrieval_evidence_ids: list[str]
    risk_level: str
    recommended_skills: list[str]
    conflict_notes: list[str] = field(default_factory=list)
    source_trace: list[str] = field(default_factory=list)
    project_source_lock: list[str] = field(default_factory=list)

    def format_for_prompt(self) -> str:
        lines = [
            "## Context Share Resolver",
            "",
            "Sinria must infer intent from prior corrections and durable records before acting. Treat the following as action constraints, not passive notes.",
            f"- Inferred intent: {self.inferred_intent}",
            f"- Risk level: {self.risk_level}",
        ]
        if self.retrieval_evidence_ids:
            lines.append(f"- Retrieved evidence IDs: {', '.join(self.retrieval_evidence_ids)}")
        if self.source_trace:
            lines.append("- Source traceability:")
            for trace in self.source_trace:
                lines.append(f"  - {trace}")
        if self.conflict_notes:
            lines.append("- Conflict resolution:")
            for note in self.conflict_notes:
                lines.append(f"  - {note}")
        if self.project_source_lock:
            lines.append("- Project Source-Lock Gate (before implementation/delegation):")
            for item in self.project_source_lock:
                lines.append(f"  - {item}")
        if self.recommended_skills:
            lines.append(f"- Recommended skills to load/apply: {', '.join(self.recommended_skills)}")
        active_project_constraints = [
            constraint for constraint in self.applicable_constraints
            if "active project" in constraint.lower()
            or "current medspot" in constraint.lower()
            or "must resolve to" in constraint.lower()
        ]
        if active_project_constraints:
            lines.append("- Active project override (apply before any repo/file/tool action):")
            for constraint in active_project_constraints[:3]:
                lines.append(f"  - {constraint}")
            lines.append("  - Do not act on an older durable project context when this override names a different active repo/product.")
        if self.applicable_constraints:
            lines.append("- Applicable prior corrections / constraints:")
            for constraint in self.applicable_constraints[:8]:
                lines.append(f"  - {constraint}")
        if self.missing_context_questions:
            lines.append("- Missing context questions only if unrecoverable:")
            for question in self.missing_context_questions:
                lines.append(f"  - {question}")
        return "\n".join(lines)


class IntentResolver:
    def __init__(self, ledger: EvidenceLedger | None = None, default_evidence: Iterable[ContextEvidence] | None = None, *, include_durable: bool = True):
        if ledger is not None:
            self.ledger = ledger
            return
        evidence = list(default_evidence or _DEFAULT_EVIDENCE)
        if include_durable:
            evidence.extend(load_durable_evidence())
        self.ledger = EvidenceLedger(evidence)

    def resolve(self, user_message: str, *, platform: str | None = None, project: str | None = None, loaded_skills: list[str] | None = None) -> IntentResolution:
        text = _normalize_current_request(user_message or "")
        text_l = text.lower()
        query_keys = self._query_keys(text_l, project=project)
        evidence: dict[str, ContextEvidence] = {}
        relevance: dict[str, float] = {}
        for key in query_keys:
            for item in self.ledger.active_for(key):
                evidence[item.evidence_id] = item
                relevance[item.evidence_id] = relevance.get(item.evidence_id, 0.0) + 2.0
        for score, item in self.ledger.search_scored(text):
            evidence[item.evidence_id] = item
            relevance[item.evidence_id] = relevance.get(item.evidence_id, 0.0) + score

        # Always preserve org-safe default when Sinria/org work is mentioned.
        # Defaults keep baseline relevance so task-matched evidence outranks
        # them under the prompt constraint budget.
        if any(marker in text_l for marker in ("sinria", "context", "コンテキスト", "agent os", "company os", "実装")):
            for default in _DEFAULT_EVIDENCE:
                if default.evidence_id not in evidence:
                    evidence[default.evidence_id] = default

        project_l = (project or "").lower()
        if project_l:
            for item in evidence.values():
                if any(project_l == key.lower() for key in item.applies_to):
                    relevance[item.evidence_id] = relevance.get(item.evidence_id, 0.0) + 2.0

        active_evidence, conflicts = resolve_non_conflicting_evidence(evidence.values())
        current_override_notes: list[str] = []
        if active_evidence:
            retained: list[ContextEvidence] = []
            for item in active_evidence:
                if evidence_overridden_by_current_request(item, text):
                    current_override_notes.append(
                        f"Current user request overrides {item.evidence_id}: newer explicit instruction wins for this turn"
                    )
                else:
                    retained.append(item)
            active_evidence = retained

        def _final_score(item: ContextEvidence) -> float:
            return (1.0 + relevance.get(item.evidence_id, 0.0)) * max(item.confidence, 0.05) * _recency_factor(item.valid_from)

        active_evidence = sorted(
            active_evidence,
            key=lambda item: (_final_score(item), item.confidence, item.valid_from, item.evidence_id),
            reverse=True,
        )
        constraints = [item.summary for item in active_evidence]
        source_trace = [f"{item.evidence_id} <- {item.source_session_id}" for item in active_evidence]
        conflict_notes = [conflict.format_for_prompt() for conflict in conflicts]
        conflict_notes.extend(current_override_notes)
        skills = self._recommended_skills(text_l, loaded_skills or [])
        risk = "regulated_org" if any(marker in text_l for marker in ("sinria", "agent os", "company os", "組織", "医療", "clinical", "context", "コンテキスト")) else "normal"
        intent = self._infer_intent(text, project=project)
        source_lock = self._project_source_lock(text_l, project=project)
        return IntentResolution(
            inferred_intent=intent,
            applicable_constraints=list(dict.fromkeys(constraints)),
            missing_context_questions=[],
            retrieval_evidence_ids=[item.evidence_id for item in active_evidence],
            risk_level=risk,
            recommended_skills=skills,
            conflict_notes=conflict_notes,
            source_trace=source_trace,
            project_source_lock=source_lock,
        )

    @staticmethod
    def _query_keys(text_l: str, *, project: str | None = None) -> list[str]:
        return derive_topic_keys(text_l, project=project)
    @staticmethod
    def _recommended_skills(text_l: str, loaded_skills: list[str]) -> list[str]:
        skills = []
        if "sinria" in text_l or "agent os" in text_l or "コンテキスト" in text_l:
            skills.append("sinria-agent")
        if any(marker in text_l for marker in ("実装", "implement", "完成", "code", "docs/plans")):
            skills.extend(["writing-plans", "test-driven-development"])
        if any(marker in text_l for marker in ("原因", "root cause", "root-cause", "汎用", "その場限り", "間違え", "wrong target", "context drift")):
            skills.append("systematic-debugging")
        if "memory" in text_l or "記憶" in text_l or "自己改善" in text_l:
            skills.append("memory-policy")
        if "claude" in text_l or ".claude" in text_l:
            skills.append("claude-code")
        return [skill for skill in dict.fromkeys(skills) if skill not in loaded_skills]

    @staticmethod
    def _project_source_lock(text_l: str, *, project: str | None = None) -> list[str]:
        """Return source-lock instructions for project-specific action turns.

        The resolver is prompt-only and metadata-only: it does not read project
        files itself.  It forces the agent's first real step to be source
        artifact discovery instead of acting from whichever durable project
        memory is most salient.
        """
        action_markers = (
            "実装", "作成", "追加", "更新", "改善", "直して", "修正", "完成", "本番化", "ローンチ",
            "原因", "特定", "汎用", "その場限り", "間違え", "ドキュメント", "資料",
            "handoff", "plan", "計画", "build", "implement", "fix", "code",
            "deploy", "production", "productionization", "ui", "mockup",
            "dashboard", "doc", "docs", "artifact", "spreadsheet",
            "conflict", "merge", "repo", "repository", "コンフリクト", "マージ", "レポジトリ",
        )
        if not any(marker in text_l for marker in action_markers):
            return []

        lines = [
            "Resolve the active project from the current message/session lane before editing files or delegating coding work.",
            "Search for existing canonical docs/specs/plans/dashboards/artifacts before creating or replacing anything.",
            "Do not create a new artifact when a canonical one exists; update or extend the source of truth unless the user explicitly asked for a new artifact.",
            "State the resolved deployment/runtime target before deploy-like work, and stop before resource mutation if it conflicts with durable context or the explicit current product.",
            "For repeated user corrections, perform a root-cause fix in shared resolver/tests/skills instead of a one-off workflow note.",
            "Read the repo guide (AGENTS.md/CLAUDE.md) plus current spec/plan; for UI work, read the mockup/source artifact before coding.",
            "Extract non-negotiable constraints and use them as acceptance criteria; do not substitute a nearby project when exact artifacts are missing.",
            "If the current project is ambiguous, inspect the current repository and run targeted session_search before acting.",
            "If ambiguity remains after source lookup, ask for the repo/path instead of continuing with an older durable project context.",
        ]
        return lines

    @staticmethod
    def _infer_intent(text: str, *, project: str | None = None) -> str:
        if "弱" in text or "改善" in text or "self-improvement" in text:
            return "Improve Sinria context sharing so prior corrections are applied before action."
        if "実装" in text or "完成" in text:
            return "Implement the requested Sinria plan with tests and safety verification."
        return "Apply relevant prior corrections before responding or acting."


def derive_topic_keys(text_l: str, *, project: str | None = None) -> list[str]:
    """Map a lowercased request/correction text onto the shared applies_to key space.

    Both capture (correction_capture) and recall (IntentResolver) use this
    function, so a correction tagged at write time stays retrievable by the
    same keys at read time.
    """
    keys: list[str] = []
    if "sinria" in text_l or project == "sinria":
        keys.extend(["sinria", "identity", "context_share"])
    if "コンテキスト" in text_l or "context" in text_l:
        keys.extend(["context_share", "team_mode", "org_context"])
    if "自己改善" in text_l or "self-improvement" in text_l or "self_improvement" in text_l:
        keys.append("self_improvement")
    if "team" in text_l or "組織" in text_l or "company os" in text_l or "agent os" in text_l:
        keys.extend(["team_mode", "org_context", "company_os"])
    if "medspot" in text_l:
        keys.extend(["medspot", "healthcare_marketplace"])
    if "medevidence" in text_l or "メドエビデンス" in text_l:
        keys.extend(["medevidence", "medical_evidence", "gcp", "cloud_run"])
    if "本番化" in text_l or "production" in text_l or "productionization" in text_l or "launch" in text_l or "ローンチ" in text_l:
        keys.extend(["productionization", "honban_plan"])
    if "暗黙知" in text_l or "tacit" in text_l:
        keys.append("tacit_skill_os")
    if "実装" in text_l or "implement" in text_l or "完成" in text_l or "改善" in text_l:
        keys.extend(["implementation", "completion"])
    if "claude" in text_l or ".claude" in text_l:
        keys.extend(["claude_code", "local_execution", "implementation"])
    return list(dict.fromkeys(keys or ["context_share"]))


_DISCORD_REPLY_PREFIX_RE = re.compile(
    r"\A\s*\[Replying to:\s*(?P<quote>\"(?:\\.|[^\"])*\"|[^\]]*)\]\s*",
    re.DOTALL,
)
_SENDER_PREFIX_RE = re.compile(r"\A\s*\[[^\]\n]{1,120}\]\s*")


def _normalize_current_request(text: str) -> str:
    """Return only the current user-authored request for intent resolution.

    Discord reply metadata is useful for humans, but it can contain an older
    cross-channel or cross-product assistant message. Feeding that quoted text
    into Context Share resolution makes stale projects look like the current
    task. Keep the user's actual message after the reply header and optional
    sender prefix.
    """
    cleaned = text or ""
    cleaned = _DISCORD_REPLY_PREFIX_RE.sub("", cleaned).lstrip()
    cleaned = _SENDER_PREFIX_RE.sub("", cleaned).lstrip()
    return cleaned


def build_context_resolver_fallback_prompt(reason: str | None = None) -> str:
    constraints = [item.summary for item in _DEFAULT_EVIDENCE]
    lines = [
        "## Context Share Resolver",
        "",
        "Context resolver durable lookup failed; applying fail-closed Sinria default constraints instead of silently skipping prior-correction guidance.",
    ]
    if reason:
        lines.append(f"- Recoverable cause: {reason[:160]}")
    lines.append("- Applicable fail-closed constraints:")
    for constraint in constraints:
        lines.append(f"  - {constraint}")
    return "\n".join(lines)


def build_context_resolver_prompt(user_message: str = "", *, platform: str | None = None, project: str | None = None) -> str:
    try:
        return IntentResolver().resolve(user_message, platform=platform, project=project).format_for_prompt()
    except Exception as exc:
        return build_context_resolver_fallback_prompt(str(exc))
