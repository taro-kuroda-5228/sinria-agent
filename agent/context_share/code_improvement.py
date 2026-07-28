"""Review-gated code/config self-improvement proposals.

This module owns local proposal metadata and isolated git worktrees.  It never
invokes an LLM, commits, merges, pushes, deploys, or sends raw diffs externally.
A human approval is required before a worktree may be prepared, and a second
human approval is required after the diff and verification evidence are ready.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

from hermes_constants import get_sinria_home

from .safety import assert_safe_identifier, assert_sanitized_metadata, assert_sanitized_text

ProposalState = Literal[
    "proposed",
    "execution_approved",
    "ready_for_review",
    "application_approved",
    "applied",
    "rejected",
]


class InvalidTransition(ValueError):
    """Raised when a proposal attempts to bypass a review gate."""


@dataclass(frozen=True)
class CodeImprovementProposal:
    proposal_id: str
    created_at: str
    updated_at: str
    repository: str
    base_sha: str
    goal: str
    gap: str
    cause: str
    durable_fix: str
    acceptance_criteria: list[str]
    evidence_ids: list[str]
    state: ProposalState = "proposed"
    execution_approved_by: str | None = None
    application_approved_by: str | None = None
    worktree_path: str | None = None
    review_diff_path: str | None = None
    review_diff_sha256: str | None = None
    verification: list[dict[str, Any]] | None = None
    applied_commit_sha: str | None = None
    human_review_required: bool = True
    external_action_performed: bool = False

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CodeImprovementProposal":
        return cls(**payload)


class CodeImprovementStore:
    """File-backed state machine for local, review-gated improvements."""

    def __init__(
        self,
        *,
        home: Path | None = None,
        allowed_repositories: Sequence[Path | str] = (),
    ) -> None:
        self.home = (home or get_sinria_home()).expanduser().resolve()
        self.root = self.home / "self_improvement"
        self.proposals_dir = self.root / "proposals"
        self.worktrees_dir = self.root / "worktrees"
        self.allowed_repositories = {
            Path(path).expanduser().resolve() for path in allowed_repositories
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _git(repo: Path, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:500]
            raise ValueError(f"git {' '.join(args)} failed: {detail}")
        return result.stdout.strip()

    def _validate_repository(self, repository: Path | str) -> Path:
        repo = Path(repository).expanduser().resolve()
        if repo not in self.allowed_repositories:
            raise ValueError("repository is not allowlisted for self-improvement")
        if not repo.is_dir():
            raise ValueError("repository does not exist")
        top = Path(self._git(repo, "rev-parse", "--show-toplevel")).resolve()
        if top != repo:
            raise ValueError("repository must be an allowlisted git root")
        return repo

    def _path(self, proposal_id: str) -> Path:
        assert_safe_identifier(proposal_id, field="proposal_id")
        return self.proposals_dir / f"{proposal_id}.json"

    def _write(self, proposal: CodeImprovementProposal) -> None:
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        target = self._path(proposal.proposal_id)
        temp = target.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(asdict(proposal), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp.replace(target)

    def get(self, proposal_id: str) -> CodeImprovementProposal:
        target = self._path(proposal_id)
        if not target.exists():
            raise ValueError(f"proposal not found: {proposal_id}")
        return CodeImprovementProposal.from_dict(json.loads(target.read_text(encoding="utf-8")))

    def list_proposals(self) -> list[CodeImprovementProposal]:
        if not self.proposals_dir.exists():
            return []
        return [
            CodeImprovementProposal.from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(self.proposals_dir.glob("*.json"))
        ]

    def propose(
        self,
        *,
        repository: Path | str,
        goal: str,
        gap: str,
        cause: str,
        durable_fix: str,
        acceptance_criteria: Sequence[str],
        evidence_ids: Sequence[str],
    ) -> CodeImprovementProposal:
        repo = self._validate_repository(repository)
        values = {
            "goal": goal,
            "gap": gap,
            "cause": cause,
            "durable_fix": durable_fix,
        }
        for field, text in values.items():
            assert_sanitized_text(text, field=field, error_cls=ValueError)
        assert_sanitized_metadata(list(acceptance_criteria), field="acceptance_criteria", error_cls=ValueError)
        for evidence_id in evidence_ids:
            assert_safe_identifier(evidence_id, field="evidence_id", error_cls=ValueError)
        if not acceptance_criteria:
            raise ValueError("at least one acceptance criterion is required")
        base_sha = self._git(repo, "rev-parse", "HEAD")
        seed = "\n".join([str(repo), base_sha, goal, gap, durable_fix])
        proposal_id = "imp-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        existing = self._path(proposal_id)
        if existing.exists():
            return self.get(proposal_id)
        now = self._now()
        proposal = CodeImprovementProposal(
            proposal_id=proposal_id,
            created_at=now,
            updated_at=now,
            repository=str(repo),
            base_sha=base_sha,
            goal=goal,
            gap=gap,
            cause=cause,
            durable_fix=durable_fix,
            acceptance_criteria=list(acceptance_criteria),
            evidence_ids=list(evidence_ids),
            verification=[],
        )
        self._write(proposal)
        return proposal

    def approve_execution(self, proposal_id: str, *, approved_by: str) -> CodeImprovementProposal:
        assert_safe_identifier(approved_by, field="approved_by", error_cls=ValueError)
        proposal = self.get(proposal_id)
        if proposal.state != "proposed":
            raise InvalidTransition("execution approval requires proposed state")
        updated = replace(
            proposal,
            state="execution_approved",
            execution_approved_by=approved_by,
            updated_at=self._now(),
        )
        self._write(updated)
        return updated

    def prepare_worktree(self, proposal_id: str) -> CodeImprovementProposal:
        proposal = self.get(proposal_id)
        if proposal.state != "execution_approved":
            raise InvalidTransition("worktree preparation requires explicit execution approval")
        repo = self._validate_repository(proposal.repository)
        if self._git(repo, "status", "--porcelain"):
            raise ValueError("source repository must be clean before preparing a worktree")
        current_sha = self._git(repo, "rev-parse", proposal.base_sha)
        if current_sha != proposal.base_sha:
            raise ValueError("proposal base commit is no longer available")
        worktree = self.worktrees_dir / proposal.proposal_id
        if proposal.worktree_path:
            return proposal
        if self.worktrees_dir.is_symlink():
            raise ValueError("self-improvement worktree root must not be a symlink")
        if worktree.exists():
            raise ValueError("worktree path already exists without proposal ownership")
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self._git(repo, "worktree", "add", "--detach", str(worktree), proposal.base_sha)
        updated = replace(proposal, worktree_path=str(worktree), updated_at=self._now())
        self._write(updated)
        return updated

    def _owned_worktree(self, proposal: CodeImprovementProposal) -> Path:
        if not proposal.worktree_path:
            raise InvalidTransition("proposal has no prepared worktree")
        if self.worktrees_dir.is_symlink():
            raise ValueError("self-improvement worktree root must not be a symlink")
        expected = (self.worktrees_dir / proposal.proposal_id).resolve()
        actual = Path(proposal.worktree_path).expanduser().resolve()
        if actual != expected or not actual.is_dir():
            raise InvalidTransition("proposal worktree is outside its owned path")
        return actual

    def _review_snapshot(self, proposal: CodeImprovementProposal) -> tuple[str, str]:
        worktree = self._owned_worktree(proposal)
        if self._git(worktree, "rev-parse", "HEAD") != proposal.base_sha:
            raise InvalidTransition("worker commits are forbidden before application approval")
        status = self._git(worktree, "status", "--porcelain=v1")
        if any(line.startswith("??") for line in status.splitlines()):
            raise ValueError("untracked files must be explicitly added or removed before review")
        diff = self._git(worktree, "diff", "HEAD", "--no-ext-diff", "--binary")
        if not diff:
            raise ValueError("worker produced no reviewable diff")
        self._git(worktree, "diff", "HEAD", "--check")
        return diff, hashlib.sha256(diff.encode("utf-8")).hexdigest()

    def collect_review(
        self,
        proposal_id: str,
        *,
        verification: Sequence[dict[str, Any]],
    ) -> CodeImprovementProposal:
        proposal = self.get(proposal_id)
        if proposal.state != "execution_approved" or not proposal.worktree_path:
            raise InvalidTransition("review collection requires an approved prepared worktree")
        assert_sanitized_metadata(list(verification), field="verification", error_cls=ValueError)
        if not verification:
            raise ValueError("verification evidence is required")
        diff, digest = self._review_snapshot(proposal)
        proposal_dir = self.proposals_dir / proposal.proposal_id
        proposal_dir.mkdir(parents=True, exist_ok=True)
        diff_path = proposal_dir / "review.diff"
        diff_path.write_text(diff + "\n", encoding="utf-8")
        updated = replace(
            proposal,
            state="ready_for_review",
            review_diff_path=str(diff_path),
            review_diff_sha256=digest,
            verification=list(verification),
            updated_at=self._now(),
        )
        self._write(updated)
        return updated

    def approve_application(self, proposal_id: str, *, approved_by: str) -> CodeImprovementProposal:
        assert_safe_identifier(approved_by, field="approved_by", error_cls=ValueError)
        proposal = self.get(proposal_id)
        if proposal.state != "ready_for_review":
            raise InvalidTransition("application approval requires ready_for_review state")
        if not proposal.verification or not all(bool(row.get("passed")) for row in proposal.verification):
            raise InvalidTransition("all recorded verification checks must pass")
        _, current_digest = self._review_snapshot(proposal)
        if current_digest != proposal.review_diff_sha256:
            raise InvalidTransition("worktree changed after review; collect a new review artifact")
        updated = replace(
            proposal,
            state="application_approved",
            application_approved_by=approved_by,
            updated_at=self._now(),
        )
        self._write(updated)
        return updated

    def mark_applied(self, proposal_id: str, *, commit_sha: str) -> CodeImprovementProposal:
        assert_safe_identifier(commit_sha, field="commit_sha", error_cls=ValueError)
        proposal = self.get(proposal_id)
        if proposal.state != "application_approved":
            raise InvalidTransition("mark_applied requires separate application approval")
        updated = replace(
            proposal,
            state="applied",
            applied_commit_sha=commit_sha,
            external_action_performed=True,
            updated_at=self._now(),
        )
        self._write(updated)
        return updated
