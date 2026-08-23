"""Per-repo repair contract for the codebase self-repair loop (Phase 2).

A repo opts into automated repair by shipping ``.sinria/repair.yaml`` at its
root. No contract — or an invalid one — means the Repair Orchestrator never
generates patches for that repo; recurring defects only produce issue
proposals (fail-closed, design §4.2).

The contract is pure data. Loading is deliberately strict: any field with an
unexpected shape invalidates the whole contract rather than being silently
defaulted, because these values gate what an automated patch may touch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CONTRACT_RELATIVE_PATH = Path(".sinria") / "repair.yaml"
DEFAULT_MAX_PATCH_LINES = 150
DEFAULT_REPRO_TEST_DIR = "tests/repair_repro"
# Python default; JS/TS repos override with e.g. "repro_{fingerprint}.test.ts".
DEFAULT_REPRO_TEST_TEMPLATE = "test_repro_{fingerprint}.py"
DEFAULT_REFACTOR_MIN_OBSERVATIONS = 3
DEFAULT_REFACTOR_MAX_FUNCTION_COMPLEXITY = 15
DEFAULT_REFACTOR_MAX_FUNCTION_LINES = 120
DEFAULT_REFACTOR_MAX_CANDIDATES_PER_SCAN = 5
DEFAULT_REFACTOR_MIN_METRIC_IMPROVEMENT = 1


@dataclass(frozen=True)
class RepairContract:
    repo: str
    repo_root: str
    verify_commands: tuple[str, ...]
    repro_command: str
    repro_test_dir: str = DEFAULT_REPRO_TEST_DIR
    repro_test_template: str = DEFAULT_REPRO_TEST_TEMPLATE
    protected_paths: tuple[str, ...] = ()
    max_patch_lines: int = DEFAULT_MAX_PATCH_LINES
    risk_overrides: tuple[str, ...] = ()
    refactor_enabled: bool = False
    refactor_min_observations: int = DEFAULT_REFACTOR_MIN_OBSERVATIONS
    refactor_max_function_complexity: int = DEFAULT_REFACTOR_MAX_FUNCTION_COMPLEXITY
    refactor_max_function_lines: int = DEFAULT_REFACTOR_MAX_FUNCTION_LINES
    refactor_max_candidates_per_scan: int = DEFAULT_REFACTOR_MAX_CANDIDATES_PER_SCAN
    refactor_min_metric_improvement: int = DEFAULT_REFACTOR_MIN_METRIC_IMPROVEMENT

    def repro_test_path(self, fingerprint: str) -> str:
        """Repo-relative path of the repro test for a defect fingerprint."""
        safe = fingerprint.replace("-", "_")
        return f"{self.repro_test_dir}/{self.repro_test_template.format(fingerprint=safe)}"


def _string_tuple(value) -> tuple[str, ...] | None:
    """Coerce an optional list of non-empty strings; None on any other shape."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        return None
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        items.append(item.strip())
    return tuple(items)


def _positive_int(mapping: dict, key: str, default: int) -> int | None:
    value = mapping.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def load_repair_contract(repo_root: Path, *, repo: str) -> RepairContract | None:
    """Load and validate a repo's repair contract; None means issue-proposal only."""
    target = Path(repo_root) / CONTRACT_RELATIVE_PATH
    try:
        import yaml

        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None

    verify_commands = _string_tuple(raw.get("verify_commands"))
    if not verify_commands:
        return None

    repro_command = raw.get("repro_command")
    if not isinstance(repro_command, str) or "{test_path}" not in repro_command:
        return None
    try:
        # The orchestrator .format()s these on every run; a stray placeholder
        # or unbalanced brace would raise there forever while the ticket stays
        # queued (crash loop). Probe-format now and fail the whole contract.
        repro_command.format(test_path="probe")
    except (KeyError, IndexError, ValueError):
        return None

    repro_test_dir = raw.get("repro_test_dir", DEFAULT_REPRO_TEST_DIR)
    if not isinstance(repro_test_dir, str) or not repro_test_dir.strip():
        return None

    repro_test_template = raw.get("repro_test_template", DEFAULT_REPRO_TEST_TEMPLATE)
    if not isinstance(repro_test_template, str) or "{fingerprint}" not in repro_test_template:
        return None
    try:
        repro_test_template.format(fingerprint="probe")
    except (KeyError, IndexError, ValueError):
        return None

    protected_paths = _string_tuple(raw.get("protected_paths"))
    if protected_paths is None:
        return None

    risk_overrides = _string_tuple(raw.get("risk_overrides"))
    if risk_overrides is None:
        return None

    max_patch_lines = raw.get("max_patch_lines", DEFAULT_MAX_PATCH_LINES)
    if isinstance(max_patch_lines, bool) or not isinstance(max_patch_lines, int) or max_patch_lines <= 0:
        return None

    raw_refactor = raw.get("refactor", {})
    if not isinstance(raw_refactor, dict):
        return None
    allowed_refactor_keys = {
        "enabled",
        "min_observations",
        "max_function_complexity",
        "max_function_lines",
        "max_candidates_per_scan",
        "min_metric_improvement",
    }
    if set(raw_refactor) - allowed_refactor_keys:
        return None
    refactor_enabled = raw_refactor.get("enabled", False)
    if not isinstance(refactor_enabled, bool):
        return None
    refactor_values = (
        _positive_int(raw_refactor, "min_observations", DEFAULT_REFACTOR_MIN_OBSERVATIONS),
        _positive_int(
            raw_refactor, "max_function_complexity", DEFAULT_REFACTOR_MAX_FUNCTION_COMPLEXITY
        ),
        _positive_int(raw_refactor, "max_function_lines", DEFAULT_REFACTOR_MAX_FUNCTION_LINES),
        _positive_int(
            raw_refactor, "max_candidates_per_scan", DEFAULT_REFACTOR_MAX_CANDIDATES_PER_SCAN
        ),
        _positive_int(
            raw_refactor, "min_metric_improvement", DEFAULT_REFACTOR_MIN_METRIC_IMPROVEMENT
        ),
    )
    if any(value is None for value in refactor_values):
        return None
    (
        refactor_min_observations,
        refactor_max_function_complexity,
        refactor_max_function_lines,
        refactor_max_candidates_per_scan,
        refactor_min_metric_improvement,
    ) = refactor_values
    assert refactor_min_observations is not None
    assert refactor_max_function_complexity is not None
    assert refactor_max_function_lines is not None
    assert refactor_max_candidates_per_scan is not None
    assert refactor_min_metric_improvement is not None

    return RepairContract(
        repo=repo,
        repo_root=str(repo_root),
        verify_commands=verify_commands,
        repro_command=repro_command.strip(),
        repro_test_dir=repro_test_dir.strip(),
        repro_test_template=repro_test_template.strip(),
        protected_paths=protected_paths,
        max_patch_lines=max_patch_lines,
        risk_overrides=risk_overrides,
        refactor_enabled=refactor_enabled,
        refactor_min_observations=int(refactor_min_observations),
        refactor_max_function_complexity=int(refactor_max_function_complexity),
        refactor_max_function_lines=int(refactor_max_function_lines),
        refactor_max_candidates_per_scan=int(refactor_max_candidates_per_scan),
        refactor_min_metric_improvement=int(refactor_min_metric_improvement),
    )
