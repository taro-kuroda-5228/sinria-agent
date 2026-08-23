"""Risk classification and patch-diff gates for the self-repair loop (Phase 2).

Two fail-closed layers (design §5):

- **Ticket time** — :func:`classify_defect_risk` marks defects that touch the
  deny-marker vocabulary (production/auth/clinical/...) as permanently
  ``human_only`` (never eligible for the Phase 4 auto-merge promotion), and
  defects located inside the self-repair machinery itself as ``escalate_only``
  (the orchestrator must never attempt to patch its own safety rails).
- **Patch time** — :func:`evaluate_patch_diff` rejects diffs that are empty,
  touch protected paths, modify the repro test (gate bypass), or exceed the
  contract's line budget.

The self-repair guard set is enforced in code, independent of what any
contract declares: a contract edit cannot re-open the recursion door.
"""

from __future__ import annotations

from agent.correction_loop.auto_triage import DENY_MARKERS

from .contract import RepairContract

RISK_AUTO_ELIGIBLE = "auto_eligible"
RISK_HUMAN_ONLY = "human_only"
RISK_ESCALATE_ONLY = "escalate_only"

# Permanently human-only paths: the loop's own safety rails. Patching these
# automatically would let the loop rewrite its own guards (design §5,
# 自己再帰の禁止). Applies to the sinria repo only — other repos have their
# own protected_paths via their contract.
SELF_REPAIR_PROTECTED_PATHS: tuple[str, ...] = (
    "agent/repair/",
    "agent/defect_capture.py",
    "agent/correction_loop/auto_triage.py",
    "agent/correction_loop/loop_maintenance.py",
    "tools/approval.py",
    ".sinria/",
)


def _path_matches(path: str, entry: str) -> bool:
    entry = entry.rstrip("/")
    return path == entry or path.startswith(entry + "/")


def is_self_repair_path(path: str) -> bool:
    return any(_path_matches(path, entry) for entry in SELF_REPAIR_PROTECTED_PATHS)


def classify_defect_risk(
    *,
    repo: str,
    exc_class: str,
    code_location: str,
    extra_markers: tuple[str, ...] = (),
) -> tuple[str, str]:
    """Return (risk_class, reason). Fail-closed: markers beat recurrence."""
    location_file = code_location.rsplit(":", 1)[0] if ":" in code_location else code_location
    if repo == "sinria" and is_self_repair_path(location_file):
        return (
            RISK_ESCALATE_ONLY,
            "defect targets the self-repair safety rails (permanently human-only, no automated patch)",
        )
    haystack = f"{repo} {exc_class} {code_location}".lower()
    for marker in (*DENY_MARKERS, *extra_markers):
        if marker in haystack:
            return RISK_HUMAN_ONLY, f"deny marker {marker!r} — never eligible for auto-merge"
    return RISK_AUTO_ELIGIBLE, "no deny markers"


def evaluate_patch_diff(
    changed_files: list[str],
    total_changed_lines: int,
    *,
    contract: RepairContract,
    repro_test_path: str,
    enforce_self_repair_guard: bool,
) -> tuple[bool, str]:
    """Gate a candidate patch diff against the contract. Returns (ok, reason)."""
    if not changed_files:
        return False, "empty diff — nothing to propose"
    guards: tuple[str, ...] = tuple(contract.protected_paths)
    if enforce_self_repair_guard:
        guards = guards + SELF_REPAIR_PROTECTED_PATHS
    for changed in changed_files:
        if " => " in changed or changed.startswith('"'):
            # git rename detection emits combined "old => new" (optionally
            # brace-grouped) notation, and special characters produce C-quoted
            # paths. Either form dodges the exact-prefix guards below — e.g.
            # "tools/{approval.py => helpers.py}" matches neither
            # "tools/approval.py" nor its prefix. Fail closed on anything the
            # matcher cannot interpret literally.
            return (
                False,
                f"unparseable diff path {changed!r} (rename/escaped notation) — human review required",
            )
        if changed == repro_test_path:
            return False, f"patch modifies the repro test {repro_test_path} (gate bypass)"
        for entry in guards:
            if _path_matches(changed, entry):
                return False, f"patch touches protected path {entry!r} — human review required"
    if total_changed_lines > contract.max_patch_lines:
        return (
            False,
            f"patch size {total_changed_lines} lines exceeds max_patch_lines {contract.max_patch_lines}",
        )
    return True, "diff within contract limits"
