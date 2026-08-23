"""Defect-to-ticket intake for the self-repair loop (Phase 2).

Turns recurring sanitized defect telemetry (``code_defects.jsonl``) into
durable repair tickets, applying every fail-closed gate from design §4.3/§5:

- recurrence threshold (``occurrence_count >= min_occurrences`` or high
  severity), transient signals discounted;
- fingerprint dedup against active tickets, max repair attempts (then human
  escalation), and a daily ticket cap;
- human-decided tickets (merged / rejected / rolled_back / escalated) are
  never auto-retried;
- repos without a valid ``.sinria/repair.yaml`` contract get **issue
  proposals only** (deduped, appended to ``repair/issue_proposals.jsonl``);
- defects inside the self-repair machinery are escalated, never patched.

``repair.enabled`` defaults to **False** — with the flag off this module does
nothing, so shipping it changes no behavior until Taro turns the loop on.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_constants import get_sinria_home

from agent.defect_capture import code_defects_path, is_transient_exc, load_defect_summaries

from .contract import load_repair_contract
from .maintenance import load_maintenance_candidates
from .risk import RISK_ESCALATE_ONLY, classify_defect_risk
from .storage import append_private_text
from .tickets import ACTIVE_STATUSES, load_tickets, new_ticket, save_ticket, transition

DEFAULT_MIN_OCCURRENCES = 3
DEFAULT_DAILY_TICKET_CAP = 3
DEFAULT_MAX_ATTEMPTS = 2

# A human already looked at (or must look at) these — automation never re-opens.
_HUMAN_DECIDED_STATUSES = frozenset({"escalated", "merged", "rejected", "rolled_back"})

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_config_best_effort() -> dict:
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _repair_section(config: dict | None) -> dict:
    if not isinstance(config, dict):
        return {}
    section = config.get("repair")
    return section if isinstance(section, dict) else {}


def repair_enabled(config: dict | None = None) -> bool:
    """``repair.enabled`` kill switch — default False (org/multi-tenant safe)."""
    if config is None:
        config = _load_config_best_effort()
    value = _repair_section(config).get("enabled", False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def _int_setting(section: dict, key: str, default: int) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return default
    return value


def resolve_repo_root(repo: str, config: dict | None) -> Path | None:
    """Map a repo name to its local checkout; None means issue-proposal only."""
    if repo == "sinria":
        return _REPO_ROOT
    mapping = _repair_section(config).get("repo_paths")
    if not isinstance(mapping, dict):
        return None
    raw = mapping.get(repo)
    if not isinstance(raw, str) or not raw.strip():
        return None
    root = Path(raw).expanduser()
    return root if root.is_dir() else None


def issue_proposals_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / "repair" / "issue_proposals.jsonl"


def evidence_confirmations_path(home: Path | None = None) -> Path:
    """Sanitized allow-list proving a human confirmed user-provided evidence."""
    return (home or get_sinria_home()) / "repair" / "evidence_confirmations.jsonl"


def _confirmed_evidence_fingerprints(path: Path) -> set[str]:
    if not path.exists():
        return set()
    confirmed: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("decision") == "confirmed" and row.get("fingerprint"):
            confirmed.add(str(row["fingerprint"]))
    return confirmed


def _existing_proposal_fingerprints(path: Path) -> set[str]:
    if not path.exists():
        return set()
    fingerprints: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            fingerprints.add(str(json.loads(stripped).get("fingerprint", "")))
        except ValueError:
            continue
    return fingerprints


def run_intake(
    *,
    config: dict | None = None,
    home: Path | None = None,
    defects_path: Path | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    only_fingerprints: set[str] | None = None,
) -> dict[str, Any]:
    """Create repair tickets / issue proposals from recurring defects.

    Returns a sanitized report dict. ``dry_run`` previews decisions without
    writing anything (and works even while ``repair.enabled`` is off, so the
    orchestrator script can show what the loop *would* do).
    """
    resolved_config = config if config is not None else _load_config_best_effort()
    enabled = repair_enabled(resolved_config)
    report: dict[str, Any] = {
        "enabled": enabled,
        "dry_run": dry_run,
        "created": [],
        "escalated": [],
        "skipped": [],
        "issue_proposals": [],
    }
    if not enabled and not dry_run:
        return report

    section = _repair_section(resolved_config)
    min_occurrences = _int_setting(section, "min_occurrences", DEFAULT_MIN_OCCURRENCES)
    daily_cap = _int_setting(section, "daily_ticket_cap", DEFAULT_DAILY_TICKET_CAP)
    max_attempts = _int_setting(section, "max_attempts_per_fingerprint", DEFAULT_MAX_ATTEMPTS)
    current = now or datetime.now(timezone.utc)
    today = current.isoformat()[:10]

    defect_summaries = load_defect_summaries(path=defects_path or code_defects_path(home))
    raw_refactor_section = section.get("refactor")
    refactor_section: dict[str, Any] = raw_refactor_section if isinstance(raw_refactor_section, dict) else {}
    maintenance_summaries = (
        load_maintenance_candidates(home=home, min_observations=1)
        if refactor_section.get("enabled") is True
        else []
    )
    summaries: list[Any] = [*defect_summaries, *maintenance_summaries]
    tickets = load_tickets(home=home)
    by_fingerprint: dict[str, list] = {}
    for ticket in tickets:
        by_fingerprint.setdefault(ticket.fingerprint, []).append(ticket)
    created_today = sum(1 for ticket in tickets if ticket.created_at[:10] == today)
    proposals_target = issue_proposals_path(home)
    proposed = _existing_proposal_fingerprints(proposals_target)
    confirmed_evidence = _confirmed_evidence_fingerprints(evidence_confirmations_path(home))

    # High severity first, then most-recurring — the daily cap spends itself
    # on the worst defects.
    ordered = sorted(summaries, key=lambda s: (s.severity != "high", -s.occurrence_count))
    for summary in ordered:
        fingerprint = summary.fingerprint

        # Explicit evidence confirmation calls this function with a one-item
        # allow-list. Do not even evaluate unrelated defects in that path.
        if only_fingerprints is not None and fingerprint not in only_fingerprints:
            continue

        def skip(reason: str) -> None:
            report["skipped"].append({"fingerprint": fingerprint, "reason": reason})

        confirmation_required = bool(getattr(summary, "confirmation_required", False))
        if confirmation_required and fingerprint not in confirmed_evidence:
            skip("user evidence confirmation required before repair intake")
            continue
        if summary.transient_likely or is_transient_exc(summary.exc_class):
            # Second clause reclassifies history: events recorded before an
            # exception class joined the transient list keep their old flag.
            skip("transient_likely — measured as noise, not a code defect")
            continue
        candidate_kind = str(getattr(summary, "candidate_kind", "defect"))
        # A human-confirmed evidence receipt is itself an explicit intake signal;
        # organic defect telemetry still needs recurrence or high severity.
        confirmed_user_evidence = (
            confirmation_required and fingerprint in confirmed_evidence
        )
        if candidate_kind != "refactor" and not confirmed_user_evidence and not (
            summary.occurrence_count >= min_occurrences or summary.severity == "high"
        ):
            skip(
                f"below recurrence threshold ({summary.occurrence_count} < {min_occurrences}) "
                "and not high severity"
            )
            continue
        existing = by_fingerprint.get(fingerprint, [])
        if any(ticket.status in ACTIVE_STATUSES for ticket in existing):
            skip("active ticket exists for this fingerprint")
            continue
        if any(ticket.status in _HUMAN_DECIDED_STATUSES for ticket in existing):
            skip("previous ticket was human-decided (merged/rejected/rolled_back/escalated) — no auto-retry")
            continue
        if len(existing) >= max_attempts:
            skip(f"max repair attempts ({max_attempts}) reached — human escalation required")
            continue

        root = resolve_repo_root(summary.repo, resolved_config)
        contract = load_repair_contract(root, repo=summary.repo) if root is not None else None
        if contract is None:
            if fingerprint in proposed:
                skip("issue proposal already filed for unregistered repo")
                continue
            proposal = {
                "fingerprint": fingerprint,
                "repo": summary.repo,
                "exc_class": summary.exc_class,
                "code_location": summary.code_location,
                "occurrence_count": summary.occurrence_count,
                "severity": summary.severity,
                "reason": "repo has no repair contract (.sinria/repair.yaml) — issue proposal only",
                "timestamp": current.isoformat().replace("+00:00", "Z"),
            }
            report["issue_proposals"].append(proposal)
            if not dry_run:
                append_private_text(
                    proposals_target,
                    json.dumps(proposal, ensure_ascii=False, sort_keys=True) + "\n",
                    root=proposals_target.parent,
                )
                proposed.add(fingerprint)
            continue

        if candidate_kind == "refactor" and not contract.refactor_enabled:
            skip("refactor not enabled by repo contract")
            continue
        if (
            candidate_kind == "refactor"
            and summary.occurrence_count < contract.refactor_min_observations
        ):
            skip("refactor observation threshold not met")
            continue

        # The cap bounds adapter/PR work, so it gates only the ticket path:
        # contract-less repos above produce issue proposals (visibility rows
        # that consume no adapter attempt) regardless of how many tickets the
        # registered repos already spent today.
        if created_today >= daily_cap:
            skip(f"daily ticket cap ({daily_cap}) reached")
            continue

        risk_class, risk_reason = classify_defect_risk(
            repo=summary.repo,
            exc_class=summary.exc_class,
            code_location=summary.code_location,
            extra_markers=contract.risk_overrides,
        )
        ticket = new_ticket(
            fingerprint=fingerprint,
            repo=summary.repo,
            exc_class=summary.exc_class,
            code_location=summary.code_location,
            severity=summary.severity,
            risk_class=risk_class,
            occurrence_count=summary.occurrence_count,
            redacted_message=str(getattr(summary, "redacted_message", "")),
            attempt=len(existing) + 1,
            candidate_kind=candidate_kind,
            signal_kind=str(getattr(summary, "signal_kind", "")),
            metric_name=str(getattr(summary, "metric_name", "")),
            baseline_metric=float(getattr(summary, "baseline_metric", 0.0)),
            target_metric=float(getattr(summary, "target_metric", 0.0)),
            now=current,
        )
        entry = {
            "ticket_id": ticket.ticket_id,
            "fingerprint": fingerprint,
            "repo": summary.repo,
            "risk_class": risk_class,
            "candidate_kind": ticket.candidate_kind,
            "signal_kind": ticket.signal_kind,
            "baseline_metric": ticket.baseline_metric,
            "target_metric": ticket.target_metric,
        }
        if risk_class == RISK_ESCALATE_ONLY:
            if not dry_run:
                save_ticket(ticket, home=home)
                transition(ticket, "escalated", note=risk_reason, now=current, home=home)
            report["escalated"].append(entry)
        else:
            if not dry_run:
                save_ticket(ticket, home=home)
            report["created"].append(entry)
        created_today += 1
    return report
