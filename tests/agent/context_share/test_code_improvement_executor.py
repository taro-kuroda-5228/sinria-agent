from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent.context_share.code_improvement import CodeImprovementStore, InvalidTransition


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "initial")
    return root


def test_proposal_is_review_gated_and_does_not_touch_repo(tmp_path: Path, repo: Path):
    store = CodeImprovementStore(home=tmp_path / ".sinria", allowed_repositories=[repo])

    proposal = store.propose(
        repository=repo,
        goal="Prevent repeated configuration failure",
        gap="The same configuration failure recurred",
        cause="Validation is missing at the configuration boundary",
        durable_fix="Add a generic validator and regression test",
        acceptance_criteria=["targeted regression test passes", "git diff --check passes"],
        evidence_ids=["ctx-ev-test"],
    )

    assert proposal.state == "proposed"
    assert proposal.worktree_path is None
    assert _git(repo, "status", "--short") == ""
    assert not (tmp_path / ".sinria" / "self_improvement" / "worktrees").exists()


def test_execution_requires_explicit_approval_and_uses_isolated_worktree(tmp_path: Path, repo: Path):
    store = CodeImprovementStore(home=tmp_path / ".sinria", allowed_repositories=[repo])
    proposal = store.propose(
        repository=repo,
        goal="Fix recurring defect",
        gap="A practical workflow failed",
        cause="Missing boundary validation",
        durable_fix="Implement validation and a regression test",
        acceptance_criteria=["regression passes"],
        evidence_ids=["ctx-ev-test"],
    )

    with pytest.raises(InvalidTransition):
        store.prepare_worktree(proposal.proposal_id)

    approved = store.approve_execution(proposal.proposal_id, approved_by="human:test")
    prepared = store.prepare_worktree(approved.proposal_id)

    worktree = Path(prepared.worktree_path or "")
    assert worktree.is_dir()
    assert worktree != repo
    assert prepared.state == "execution_approved"
    assert _git(worktree, "rev-parse", "HEAD") == proposal.base_sha
    assert _git(repo, "status", "--short") == ""


def test_review_artifact_rejects_worker_commits_and_apply_is_separate_gate(tmp_path: Path, repo: Path):
    store = CodeImprovementStore(home=tmp_path / ".sinria", allowed_repositories=[repo])
    proposal = store.propose(
        repository=repo,
        goal="Fix recurring defect",
        gap="Workflow failed",
        cause="Missing validation",
        durable_fix="Add validation",
        acceptance_criteria=["test passes"],
        evidence_ids=["ctx-ev-test"],
    )
    store.approve_execution(proposal.proposal_id, approved_by="human:test")
    prepared = store.prepare_worktree(proposal.proposal_id)
    worktree = Path(prepared.worktree_path or "")
    (worktree / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    ready = store.collect_review(
        proposal.proposal_id,
        verification=[{"name": "targeted", "passed": True, "summary": "1 passed"}],
    )

    assert ready.state == "ready_for_review"
    assert Path(ready.review_diff_path or "").read_text(encoding="utf-8")
    assert _git(worktree, "rev-parse", "HEAD") == proposal.base_sha
    assert _git(repo, "status", "--short") == ""

    with pytest.raises(InvalidTransition):
        store.mark_applied(proposal.proposal_id, commit_sha="deadbeef")

    approved = store.approve_application(proposal.proposal_id, approved_by="human:test")
    assert approved.state == "application_approved"
    assert _git(worktree, "rev-parse", "HEAD") == proposal.base_sha


def test_repository_allowlist_and_sanitized_metadata_are_enforced(tmp_path: Path, repo: Path):
    store = CodeImprovementStore(home=tmp_path / ".sinria", allowed_repositories=[])
    with pytest.raises(ValueError, match="allowlisted"):
        store.propose(
            repository=repo,
            goal="Fix defect",
            gap="Failure",
            cause="Cause",
            durable_fix="Fix",
            acceptance_criteria=["pass"],
            evidence_ids=[],
        )

    allowed = CodeImprovementStore(home=tmp_path / ".sinria", allowed_repositories=[repo])
    with pytest.raises(ValueError):
        allowed.propose(
            repository=repo,
            goal="pass" + "word=value",
            gap="Failure",
            cause="Cause",
            durable_fix="Fix",
            acceptance_criteria=["pass"],
            evidence_ids=[],
        )


def test_list_is_metadata_only(tmp_path: Path, repo: Path):
    store = CodeImprovementStore(home=tmp_path / ".sinria", allowed_repositories=[repo])
    created = store.propose(
        repository=repo,
        goal="Fix defect",
        gap="Failure",
        cause="Cause",
        durable_fix="Fix",
        acceptance_criteria=["pass"],
        evidence_ids=["ctx-ev-test"],
    )
    rows = store.list_proposals()
    assert [row.proposal_id for row in rows] == [created.proposal_id]
    payload = json.loads((tmp_path / ".sinria" / "self_improvement" / "proposals" / f"{created.proposal_id}.json").read_text())
    assert payload["external_action_performed"] is False
    assert payload["human_review_required"] is True


def test_application_approval_is_bound_to_reviewed_diff(tmp_path: Path, repo: Path):
    store = CodeImprovementStore(home=tmp_path / ".sinria", allowed_repositories=[repo])
    proposal = store.propose(repository=repo, goal="Fix", gap="Gap", cause="Cause", durable_fix="Fix", acceptance_criteria=["pass"], evidence_ids=[])
    store.approve_execution(proposal.proposal_id, approved_by="human:test")
    prepared = store.prepare_worktree(proposal.proposal_id)
    worktree = Path(prepared.worktree_path or "")
    (worktree / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    ready = store.collect_review(proposal.proposal_id, verification=[{"name": "targeted", "passed": True, "summary": "passed"}])
    assert ready.review_diff_sha256
    (worktree / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
    with pytest.raises(InvalidTransition, match="changed after review"):
        store.approve_application(proposal.proposal_id, approved_by="human:test")


def test_staged_changes_are_reviewed_and_untracked_files_are_rejected(tmp_path: Path, repo: Path):
    store = CodeImprovementStore(home=tmp_path / ".sinria", allowed_repositories=[repo])
    proposal = store.propose(repository=repo, goal="Fix", gap="Gap", cause="Cause", durable_fix="Fix", acceptance_criteria=["pass"], evidence_ids=[])
    store.approve_execution(proposal.proposal_id, approved_by="human:test")
    prepared = store.prepare_worktree(proposal.proposal_id)
    worktree = Path(prepared.worktree_path or "")
    (worktree / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(worktree, "add", "app.py")
    ready = store.collect_review(proposal.proposal_id, verification=[{"name": "targeted", "passed": True, "summary": "passed"}])
    assert "VALUE = 2" in Path(ready.review_diff_path or "").read_text(encoding="utf-8")
    second = store.propose(repository=repo, goal="Another fix", gap="Gap", cause="Cause", durable_fix="Fix", acceptance_criteria=["pass"], evidence_ids=[])
    store.approve_execution(second.proposal_id, approved_by="human:test")
    prepared2 = store.prepare_worktree(second.proposal_id)
    worktree2 = Path(prepared2.worktree_path or "")
    (worktree2 / "new.py").write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="untracked"):
        store.collect_review(second.proposal_id, verification=[{"name": "targeted", "passed": True, "summary": "passed"}])


def test_symlinked_worktree_root_is_rejected(tmp_path: Path, repo: Path):
    home = tmp_path / ".sinria"
    outside = tmp_path / "outside"
    outside.mkdir()
    root = home / "self_improvement"
    root.mkdir(parents=True)
    (root / "worktrees").symlink_to(outside, target_is_directory=True)
    store = CodeImprovementStore(home=home, allowed_repositories=[repo])
    proposal = store.propose(repository=repo, goal="Fix", gap="Gap", cause="Cause", durable_fix="Fix", acceptance_criteria=["pass"], evidence_ids=[])
    store.approve_execution(proposal.proposal_id, approved_by="human:test")
    with pytest.raises(ValueError, match="symlink"):
        store.prepare_worktree(proposal.proposal_id)
