"""Repair Orchestrator — drives tickets through the Phase 2 state machine.

For each queued ticket (one per repo per run):

1. **reproduce** — isolated worktree, adapter writes a repro test, the
   orchestrator machine-verifies it FAILS, then commits it;
2. **patch** — adapter proposes a minimal fix (repro test is now frozen);
3. **verify** — diff gates (protected paths, self-repair guard, repro-test
   immutability, line budget), repro test flips fail→pass, every contract
   verify command exits 0;
4. **pr_open** — sanitized PR proposal with provenance. Merge stays human.

Any gate failure ends the ticket as ``failed`` (audited, outcome recorded,
worktree removed, attempt consumed) — the loop never retries in-place; the
intake decides whether a fresh attempt is allowed (max 2 per fingerprint).

``repair.enabled`` (default False) gates everything; real adapter execution
additionally requires the local-adapter env gates, so an enabled orchestrator
without those env vars fails tickets with a visible reason instead of acting.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from typing import Any

from agent.privacy.sanitization import contains_sensitive_text

from .contract import RepairContract, load_repair_contract
from .executor import RepairExecutionError, RepairExecutor, worktrees_dir
from .intake import (
    _load_config_best_effort,
    _repair_section,
    repair_enabled,
    resolve_repo_root,
    run_intake,
)
from .maintenance import scan_repository
from .risk import evaluate_patch_diff
from .tickets import RepairTicket, load_tickets, record_outcome, transition

DEFAULT_ADAPTER_ENGINE = "sinria_native"

# Crash-recovery window for in-flight tickets (reproducing/patching/verifying).
# A live run may hold a ticket for hours (worker timeouts + two full verify
# passes), so recovery only fires when a ticket shows no progress for longer
# than this. Overridable via ``repair.stale_active_hours`` (positive int).
DEFAULT_STALE_ACTIVE_HOURS = 24
_STALE_RECOVERABLE_STATUSES = frozenset({"reproducing", "patching", "verifying"})


class _TicketFailure(Exception):
    """Internal control flow: a gate failed with a sanitized reason."""


def _adapter_failure_reason(phase: str, adapter_result: dict[str, Any]) -> str:
    """Build a diagnosable failure note that keeps the adapter's own reason.

    The adapter returns a cloud-safe ``sanitizedSummary`` (e.g. "process exited
    with code 1", "not installed", "timed out"). Recording only the status
    threw that away, leaving tickets/transitions with no clue why a phase
    failed. Surface the sanitized summary — it is already the redacted field.
    """
    status = adapter_result.get("status")
    # External adapters report ``sanitizedSummary``; the sinria_native path
    # reports its (fixed, sanitized) failure string under ``reason``.
    summary = str(
        adapter_result.get("sanitizedSummary") or adapter_result.get("reason") or ""
    ).strip()
    base = f"adapter {phase} phase returned status {status!r}"
    return f"{base}: {summary}" if summary else base


def _sanitize_failure_note(reason: str) -> str:
    """Fail-closed laundering for gate-failure notes.

    Failure details can embed arbitrary strings from test ids or adapter
    output (a parametrized pytest id may carry an email or a long digit run).
    ``transition()`` rightly refuses unsanitized notes — but letting that
    refusal escape the failure path would strand the ticket in a working
    state forever. Same convention as ``sanitize_defect_message``: redact,
    re-verify, and withhold the body when it cannot be made clean (the
    ``failed`` transition itself still lands).
    """
    collapsed = " ".join(str(reason or "").split())
    try:
        from agent.redact import redact_sensitive_text

        cleaned = redact_sensitive_text(collapsed, force=True)
    except Exception:
        cleaned = ""
    cleaned = " ".join(str(cleaned or "").split())[:300]
    if not cleaned or contains_sensitive_text(cleaned):
        return "gate failure note withheld (sanitization failed) — see local repair artifacts"
    return cleaned


def _edit_approval_required(config: dict[str, Any] | None) -> bool:
    approval = dict(_repair_section(config).get("approval") or {})
    return approval.get("edit_requires_human_approval", True) is not False


def partition_queued_ticket_ids(
    tickets: list[RepairTicket], config: dict[str, Any] | None
) -> tuple[list[str], list[str]]:
    """Return executable and approval-blocked queued ticket IDs."""
    approval_required = _edit_approval_required(config)
    queued: list[str] = []
    awaiting: list[str] = []
    for ticket in tickets:
        if ticket.status != "queued":
            continue
        approved = bool(ticket.edit_approved_at and ticket.edit_approved_by)
        if approval_required and not approved:
            awaiting.append(ticket.ticket_id)
        else:
            queued.append(ticket.ticket_id)
    return queued, awaiting


def _adapter_engine(config: dict[str, Any] | None) -> str:
    return str(_repair_section(config).get("adapter_engine") or DEFAULT_ADAPTER_ENGINE)


def _pr_release_approved(config: dict[str, Any] | None) -> bool:
    """Require independent config and process-level approval for external PR writes."""
    return (
        _repair_section(config).get("open_pr") is True
        and os.environ.get("SINRIA_REPAIR_OPEN_PR_APPROVED") == "1"
    )


def _repro_instructions(ticket: RepairTicket, repro_test_path: str) -> str:
    if ticket.candidate_kind == "refactor":
        return (
            f"Repair ticket {ticket.ticket_id} (phase 1/2 — characterize behavior only). "
            f"Objective signal: {ticket.metric_name}={ticket.baseline_metric:g} at "
            f"{ticket.code_location} in repo {ticket.repo}. Create exactly one new pytest file at "
            f"{repro_test_path} that PASSES against the current code and locks observable behavior. "
            "Do NOT refactor production code and do NOT modify any other file. The test must cover "
            "public behavior, not implementation shape."
        )
    trigger = f" Sanitized trigger: {ticket.redacted_message}." if ticket.redacted_message else ""
    return (
        f"Repair ticket {ticket.ticket_id} (phase 1/2 — reproduce only). "
        f"Defect: recurring {ticket.exc_class} at {ticket.code_location} in repo {ticket.repo} "
        f"(fingerprint {ticket.fingerprint}, seen {ticket.occurrence_count}x).{trigger} "
        f"Create exactly one new pytest file at {repro_test_path} that FAILS against the current "
        "code by reproducing this defect. Do NOT fix the defect and do NOT modify any other file. "
        "The test must fail for the defect's reason, not from import or collection errors."
    )


def _patch_instructions(ticket: RepairTicket, contract: RepairContract, repro_test_path: str) -> str:
    protected = ", ".join(contract.protected_paths) or "(none)"
    if ticket.candidate_kind == "refactor":
        return (
            f"Repair ticket {ticket.ticket_id} (phase 2/2 — behavior-preserving refactor). "
            f"The passing characterization test {repro_test_path} freezes observable behavior. "
            f"Reduce {ticket.metric_name} at {ticket.code_location} from {ticket.baseline_metric:g} "
            f"to at most {ticket.target_metric:g}; the orchestrator will re-measure it. Keep the "
            f"change within {contract.max_patch_lines} changed lines. Do NOT modify "
            f"{repro_test_path}. Do NOT touch these protected paths: {protected}. No dependency, "
            "public API, configuration schema, auth, billing, clinical, deployment, or behavior changes."
        )
    return (
        f"Repair ticket {ticket.ticket_id} (phase 2/2 — minimal fix). "
        f"The failing test {repro_test_path} reproduces a recurring {ticket.exc_class} at "
        f"{ticket.code_location}. Fix the defect so that test passes. Keep the change minimal "
        f"(at most {contract.max_patch_lines} changed lines). Do NOT modify {repro_test_path}. "
        f"Do NOT touch these protected paths: {protected}. "
        "No dependency changes, no deploys, no credential or config secrets, no unrelated refactors."
    )


def _classify_fix(files: list[str]) -> str:
    if files and all(name.startswith(("tests/", "test/")) for name in files):
        return "test-only"
    return "logic"


def _pr_body(ticket: RepairTicket, contract: RepairContract, files: list[str], lines: int) -> str:
    return "\n".join(
        [
            "## Sinria Codebase Self-Repair Loop — automated fix proposal (Phase 2)",
            "",
            f"- Ticket: `{ticket.ticket_id}` (attempt {ticket.attempt})",
            f"- Defect fingerprint: `{ticket.fingerprint}` — {ticket.exc_class} at "
            f"`{ticket.code_location}` (seen {ticket.occurrence_count}x)",
            f"- Repro test: `{ticket.repro_test_path}` (fail→pass machine-verified)",
            f"- Verify commands (all green): {', '.join(f'`{cmd}`' for cmd in contract.verify_commands)}",
            f"- Changed: {len(files)} file(s) / {lines} line(s) — gated by protected paths, "
            "repro-test immutability, and max_patch_lines",
            "",
            "PR-proposal only: merging remains a human decision (design §7 Phase 2). "
            "Provenance: sanitized DefectRecords in `repair/code_defects.jsonl` and the "
            f"ticket file `repair/tickets/{ticket.ticket_id}.json` under the local Sinria home.",
        ]
    )


def process_ticket(
    ticket: RepairTicket,
    *,
    config: dict | None,
    home: Path | None,
    executor: RepairExecutor,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one queued ticket through reproduce → patch → verify → pr_open."""
    result: dict[str, Any] = {
        "ticket_id": ticket.ticket_id,
        "repo": ticket.repo,
        "candidate_kind": ticket.candidate_kind,
    }
    if _edit_approval_required(config) and not (
        ticket.edit_approved_at and ticket.edit_approved_by
    ):
        return {**result, "final_status": "awaiting_approval"}
    root = resolve_repo_root(ticket.repo, config)
    contract = load_repair_contract(root, repo=ticket.repo) if root is not None else None
    if contract is None:
        moved = transition(
            ticket, "failed", note="repair contract missing or invalid at processing time",
            now=now, home=home,
        )
        record_outcome(
            {
                "ticket_id": moved.ticket_id,
                "fingerprint": moved.fingerprint,
                "repo": moved.repo,
                "event": "failed",
                "reason": "missing contract",
                "attempt": moved.attempt,
                "timestamp": moved.updated_at,
            },
            home=home,
        )
        return {**result, "final_status": "failed", "note": moved.notes[-1]}
    if ticket.candidate_kind == "refactor" and not contract.refactor_enabled:
        moved = transition(
            ticket,
            "failed",
            note="refactor not enabled by repo contract at processing time",
            now=now,
            home=home,
        )
        record_outcome(
            {
                "ticket_id": moved.ticket_id,
                "fingerprint": moved.fingerprint,
                "repo": moved.repo,
                "event": "failed",
                "reason": "refactor contract disabled",
                "attempt": moved.attempt,
                "timestamp": moved.updated_at,
            },
            home=home,
        )
        return {**result, "final_status": "failed", "note": moved.notes[-1]}

    engine = _adapter_engine(config)
    branch = f"repair/{ticket.ticket_id}"
    repro_test_path = contract.repro_test_path(ticket.fingerprint)
    repro_command = contract.repro_command.format(test_path=repro_test_path)
    worktree: Path | None = None
    try:
        worktree = executor.prepare_worktree(Path(contract.repo_root), branch)
        ticket = transition(
            ticket, "reproducing", note="isolated worktree prepared", now=now, home=home,
            updates={"branch": branch, "repro_test_path": repro_test_path},
        )

        repro_instructions = _repro_instructions(ticket, repro_test_path)
        repro_result = executor.invoke_adapter(
            ticket=ticket, phase="repro",
            instructions=repro_instructions,
            worktree=worktree, engine=engine,
        )
        if repro_result.get("status") != "completed":
            raise _TicketFailure(_adapter_failure_reason("repro", repro_result))
        repro_file = worktree / repro_test_path
        if not repro_file.exists():
            # A native worker can return a prose plan as its final response without
            # using a file tool. Treat that as an incomplete action, not a consumed
            # repair attempt, and give it one tightly-scoped corrective turn.
            correction = (
                f"{repro_instructions}\n\n"
                "The previous turn returned completed without creating the required file. "
                "Do not return a plan or explanation. Use the file tools now and create exactly "
                f"{repro_test_path}. Finish only after that file exists."
            )
            repro_result = executor.invoke_adapter(
                ticket=ticket, phase="repro", instructions=correction,
                worktree=worktree, engine=engine,
            )
            if repro_result.get("status") != "completed":
                raise _TicketFailure(_adapter_failure_reason("repro corrective retry", repro_result))
        if not repro_file.exists():
            label = "characterization" if ticket.candidate_kind == "refactor" else "repro"
            raise _TicketFailure(
                f"adapter did not create the {label} test file after one corrective retry"
            )
        exit_code, _tail = executor.run_command(repro_command, cwd=worktree)
        baseline_metric: float | None = None
        if ticket.candidate_kind == "refactor":
            if exit_code != 0:
                raise _TicketFailure("characterization test did not pass before patch")
            baseline_metric = executor.measure_ticket_metric(ticket, worktree)
            if baseline_metric != ticket.baseline_metric:
                raise _TicketFailure("objective metric baseline changed since candidate observation")
            commit_message = f"repair: characterization test for {ticket.fingerprint}"
            transition_note = "characterization test passes before refactor (machine-verified)"
        else:
            if exit_code == 0:
                raise _TicketFailure(
                    "repro test passed before any patch — it does not reproduce the defect"
                )
            commit_message = f"repair: repro test for {ticket.fingerprint}"
            transition_note = "repro test fails as expected (machine-verified)"
        if not executor.commit_all(worktree, commit_message):
            raise _TicketFailure("nothing to commit after the repro phase")
        repro_sha = executor.rev_parse(worktree)
        # Baseline capture (pre-patch): the repo-wide suite may be red for
        # reasons unrelated to this repair, so record which tests already
        # fail at the repro commit. The post-patch gate tolerates exactly
        # this set and rejects any NEW failure (differential verify).
        baseline_failures: dict[str, frozenset] = {}
        for command in contract.verify_commands:
            base_code, base_failed, _tail = executor.run_verify_command(command, cwd=worktree)
            baseline_failures[command] = base_failed if base_code != 0 else frozenset()
        ticket = transition(
            ticket, "patching", note=transition_note,
            now=now, home=home,
        )

        patch_result = executor.invoke_adapter(
            ticket=ticket, phase="patch",
            instructions=_patch_instructions(ticket, contract, repro_test_path),
            worktree=worktree, engine=engine,
        )
        if patch_result.get("status") != "completed":
            raise _TicketFailure(_adapter_failure_reason("patch", patch_result))
        if not executor.commit_all(worktree, f"repair: candidate fix for {ticket.fingerprint}"):
            raise _TicketFailure("adapter produced no changes in the patch phase")
        ticket = transition(ticket, "verifying", note="candidate patch committed", now=now, home=home)

        files, lines = executor.diff_stats(worktree, repro_sha)
        diff_ok, diff_reason = evaluate_patch_diff(
            files, lines,
            contract=contract, repro_test_path=repro_test_path,
            enforce_self_repair_guard=(ticket.repo == "sinria"),
        )
        if not diff_ok:
            raise _TicketFailure(diff_reason)
        exit_code, _tail = executor.run_command(repro_command, cwd=worktree)
        if exit_code != 0:
            label = "characterization" if ticket.candidate_kind == "refactor" else "repro"
            raise _TicketFailure(f"{label} test still failing after the patch")
        final_metric: float | None = None
        metric_improvement: float | None = None
        if ticket.candidate_kind == "refactor":
            assert baseline_metric is not None
            final_metric = executor.measure_ticket_metric(ticket, worktree)
            metric_improvement = baseline_metric - final_metric
            if (
                final_metric > ticket.target_metric
                or metric_improvement < contract.refactor_min_metric_improvement
            ):
                raise _TicketFailure("objective metric did not improve to the contracted target")
            result.update(
                baseline_metric=baseline_metric,
                final_metric=final_metric,
                metric_improvement=metric_improvement,
            )
        ignored_pre_existing = 0
        for command in contract.verify_commands:
            exit_code, failed_now, _tail = executor.run_verify_command(command, cwd=worktree)
            if exit_code == 0:
                continue
            pre_existing = baseline_failures.get(command, frozenset())
            new_failures = failed_now - pre_existing
            if new_failures or not failed_now:
                # New failures — or a nonzero exit with nothing parseable
                # (collection crash, OOM): no basis to tolerate, reject.
                detail = ""
                if new_failures:
                    sample = ", ".join(sorted(new_failures)[:3])
                    detail = (
                        f" — {len(new_failures)} new failing test(s) vs pre-patch baseline: {sample}"
                    )
                raise _TicketFailure(
                    f"verify command failed (exit {exit_code}): {command}{detail}"
                )
            ignored_pre_existing += len(failed_now)

        if not _pr_release_approved(config):
            verify_note = "all gates green — local branch ready for human review"
            if ignored_pre_existing:
                verify_note += (
                    f" ({ignored_pre_existing} pre-existing baseline failure(s) unrelated to "
                    "this patch tolerated by differential verify)"
                )
            ticket = transition(
                ticket, "review_ready", note=verify_note, now=now, home=home,
            )
            record_outcome(
                {
                    "ticket_id": ticket.ticket_id,
                    "fingerprint": ticket.fingerprint,
                    "repo": ticket.repo,
                    "event": "review_ready",
                    "branch": branch,
                    "candidate_kind": ticket.candidate_kind,
                    "baseline_metric": baseline_metric,
                    "final_metric": final_metric,
                    "metric_improvement": metric_improvement,
                    "attempt": ticket.attempt,
                    "timestamp": ticket.updated_at,
                },
                home=home,
            )
            return {
                **result,
                "final_status": "review_ready",
                "branch": branch,
                "note": verify_note,
            }

        title = (
            f"repair: fix {ticket.exc_class} at {ticket.code_location} ({ticket.fingerprint})"
        )
        verify_note = "all gates green — PR proposed after explicit release approval"
        if ignored_pre_existing:
            verify_note += (
                f" ({ignored_pre_existing} pre-existing baseline failure(s) unrelated to "
                "this patch tolerated by differential verify)"
            )
        pr_url = executor.open_pr(worktree, branch, title, _pr_body(ticket, contract, files, lines))
        ticket = transition(
            ticket, "pr_open", note=verify_note, now=now, home=home,
            updates={"pr_url": pr_url},
        )
        record_outcome(
            {
                "ticket_id": ticket.ticket_id,
                "fingerprint": ticket.fingerprint,
                "repo": ticket.repo,
                "event": "pr_open",
                "fix_class": _classify_fix(files),
                "candidate_kind": ticket.candidate_kind,
                "baseline_metric": baseline_metric,
                "final_metric": final_metric,
                "metric_improvement": metric_improvement,
                "changed_files": len(files),
                "changed_lines": lines,
                "attempt": ticket.attempt,
                "timestamp": ticket.updated_at,
            },
            home=home,
        )
        return {**result, "final_status": "pr_open", "note": ticket.notes[-1], "pr_url": pr_url}
    except (_TicketFailure, RepairExecutionError) as failure:
        reason = _sanitize_failure_note(str(failure))
        ticket = transition(ticket, "failed", note=reason, now=now, home=home)
        record_outcome(
            {
                "ticket_id": ticket.ticket_id,
                "fingerprint": ticket.fingerprint,
                "repo": ticket.repo,
                "event": "failed",
                "reason": reason[:200],
                "attempt": ticket.attempt,
                "timestamp": ticket.updated_at,
            },
            home=home,
        )
        return {**result, "final_status": "failed", "note": reason}
    finally:
        if worktree is not None:
            executor.remove_worktree(Path(contract.repo_root), worktree)


def _stale_active_hours(config: dict[str, Any] | None) -> int:
    value = _repair_section(config).get("stale_active_hours", DEFAULT_STALE_ACTIVE_HOURS)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return DEFAULT_STALE_ACTIVE_HOURS
    return value


def recover_stale_tickets(
    *,
    config: dict[str, Any] | None,
    home: Path | None,
    executor: RepairExecutor,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fail in-flight tickets abandoned by a crashed or killed run.

    A process death between transitions strands a ticket in a working state:
    it is never picked up again (only ``queued`` tickets are processed), yet
    it still counts as ACTIVE for intake dedup, so its fingerprint can never
    be re-ticketed. Tickets with no progress inside the stale window move to
    ``failed`` (audited, outcome recorded, attempt consumed) and their
    leftover worktree is removed best-effort. Fresh in-flight tickets are
    left alone — a live run may legitimately hold one for hours. Recovery is
    idempotent: a recovered ticket leaves the recoverable status set.
    """
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(hours=_stale_active_hours(config))
    recovered: list[dict[str, Any]] = []
    for ticket in load_tickets(home=home):
        if ticket.status not in _STALE_RECOVERABLE_STATUSES:
            continue
        updated: datetime | None
        try:
            updated = datetime.fromisoformat(str(ticket.updated_at).replace("Z", "+00:00"))
        except ValueError:
            updated = None  # malformed timestamp: fail-closed, treat as stale
        if updated is not None and updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if updated is not None and updated >= cutoff:
            continue
        from_status = ticket.status
        moved = transition(
            ticket,
            "failed",
            note=(
                "stale in-flight ticket recovered after interrupted run "
                "(no progress within the stale window) — attempt consumed"
            ),
            now=now,
            home=home,
        )
        record_outcome(
            {
                "ticket_id": moved.ticket_id,
                "fingerprint": moved.fingerprint,
                "repo": moved.repo,
                "event": "failed",
                "reason": "stale in-flight recovery",
                "from_status": from_status,
                "attempt": moved.attempt,
                "timestamp": moved.updated_at,
            },
            home=home,
        )
        if moved.branch:
            root = resolve_repo_root(moved.repo, config)
            if root is not None:
                leftover = worktrees_dir(home) / moved.branch.replace("/", "-")
                try:
                    executor.remove_worktree(root, leftover)
                except Exception:
                    pass  # cleanup is best-effort; the recovery itself already landed
        recovered.append(
            {"ticket_id": moved.ticket_id, "repo": moved.repo, "from_status": from_status}
        )
    return recovered


def _run_maintenance_scan(
    *, config: dict[str, Any], home: Path | None, now: datetime | None
) -> dict[str, Any]:
    section = _repair_section(config)
    raw_refactor = section.get("refactor")
    globally_enabled = isinstance(raw_refactor, dict) and raw_refactor.get("enabled") is True
    report: dict[str, Any] = {"enabled": globally_enabled, "observations": 0, "repos": []}
    if not globally_enabled:
        return report
    raw_repo_paths = section.get("repo_paths")
    repo_paths = raw_repo_paths if isinstance(raw_repo_paths, dict) else {}
    for repo in sorted(str(name) for name in repo_paths):
        root = resolve_repo_root(repo, config)
        if root is None:
            continue
        contract = load_repair_contract(root, repo=repo)
        if contract is None or not contract.refactor_enabled:
            continue
        scan_result = scan_repository(
            root,
            repo=repo,
            home=home,
            config={
                "enabled": True,
                "max_function_complexity": contract.refactor_max_function_complexity,
                "max_function_lines": contract.refactor_max_function_lines,
                "max_candidates_per_scan": contract.refactor_max_candidates_per_scan,
                "excluded_paths": list(contract.protected_paths),
            },
            now=now,
        )
        observed = int(scan_result.get("observed", 0))
        report["observations"] += observed
        report["repos"].append({"repo": repo, **scan_result})
    return report


def run_orchestrator(
    *,
    config: dict | None = None,
    home: Path | None = None,
    executor: RepairExecutor | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Recover stale tickets, intake, then process queued tickets (throttled)."""
    resolved_config = config if config is not None else _load_config_best_effort()
    section = _repair_section(resolved_config)
    actual_executor = executor
    recovered: list[dict[str, Any]] = []
    if not dry_run and repair_enabled(resolved_config):
        # Recovery runs before intake so a freed fingerprint can be
        # re-ticketed in the same run (subject to every intake gate).
        if actual_executor is None:
            actual_executor = RepairExecutor(home=home)
        recovered = recover_stale_tickets(
            config=resolved_config, home=home, executor=actual_executor, now=now
        )
    maintenance_scan: dict[str, Any] = {"enabled": False, "observations": 0, "repos": []}
    if not dry_run and section.get("enabled") is True:
        maintenance_scan = _run_maintenance_scan(config=resolved_config, home=home, now=now)
    intake_report = run_intake(config=resolved_config, home=home, now=now, dry_run=dry_run)
    report: dict[str, Any] = {
        "enabled": intake_report["enabled"],
        "dry_run": dry_run,
        "recovered": recovered,
        "maintenance_scan": maintenance_scan,
        "intake": intake_report,
        "processed": [],
    }
    tickets = load_tickets(home=home)
    queued_ids, awaiting_ids = partition_queued_ticket_ids(tickets, resolved_config)
    report["awaiting_approval"] = awaiting_ids
    report["queued"] = queued_ids
    if dry_run or not intake_report["enabled"]:
        return report
    if actual_executor is None:
        actual_executor = RepairExecutor(home=home)
    seen_repos: set[str] = set()
    for ticket in tickets:
        if ticket.ticket_id not in queued_ids or ticket.repo in seen_repos:
            continue
        seen_repos.add(ticket.repo)
        report["processed"].append(
            process_ticket(
                ticket, config=resolved_config, home=home, executor=actual_executor, now=now,
            )
        )
    return report
