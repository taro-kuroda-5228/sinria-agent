"""Objective, metadata-only maintainability signals for code repair.

The scanner never stores source text, diffs, tracebacks, prompts, or command
output. A refactor candidate is emitted only after the same repo-relative
function breaches an explicit contract threshold on multiple distinct scans.
"""
from __future__ import annotations

import ast
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from hermes_constants import get_sinria_home

from agent.privacy.sanitization import assert_safe_identifier, assert_sanitized_text
from agent.defect_capture import _safe_digest
from .storage import append_private_text

MAINTENANCE_SIGNALS_RELATIVE_PATH = Path("repair") / "maintenance_signals.jsonl"
SUPPORTED_SIGNAL_KINDS = frozenset({"function_complexity", "function_lines"})
_DEFAULT_EXCLUDED_PARTS = frozenset({".git", ".venv", "venv", "node_modules", "vendor", "dist", "build"})


@dataclass(frozen=True)
class MaintenanceCandidate:
    fingerprint: str
    repo: str
    code_location: str
    signal_kind: str
    metric_name: str
    baseline_metric: float
    target_metric: float
    occurrence_count: int
    candidate_kind: str = "refactor"
    severity: str = "medium"
    exc_class: str = "MaintainabilitySignal"
    transient_likely: bool = False

    def __post_init__(self) -> None:
        assert_safe_identifier(self.fingerprint, field="fingerprint")
        assert_sanitized_text(self.repo, field="repo")
        assert_sanitized_text(self.code_location, field="code_location")
        assert_sanitized_text(self.metric_name, field="metric_name")
        if self.signal_kind not in SUPPORTED_SIGNAL_KINDS:
            raise ValueError(f"unsupported maintenance signal: {self.signal_kind}")
        if self.candidate_kind != "refactor":
            raise ValueError("maintenance candidates must be refactors")
        if not math.isfinite(self.baseline_metric) or not math.isfinite(self.target_metric):
            raise ValueError("maintenance metrics must be finite")
        if self.baseline_metric <= self.target_metric:
            raise ValueError("maintenance metric must improve downward")
        if self.occurrence_count < 1:
            raise ValueError("occurrence_count must be positive")


def maintenance_signals_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / MAINTENANCE_SIGNALS_RELATIVE_PATH


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.functions: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        qualname = ".".join((*self.stack, node.name))
        self.functions.append((qualname, node))
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        qualname = ".".join((*self.stack, node.name))
        self.functions.append((qualname, node))
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()


class _ComplexityCounter(ast.NodeVisitor):
    def __init__(self, root: ast.AST) -> None:
        self.root = root
        self.value = 1

    def visit(self, node: ast.AST) -> Any:
        if node is not self.root and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return None
        return super().visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.value += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self.value += len(node.cases)
        self.generic_visit(node)


def _complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    counter = _ComplexityCounter(node)
    counter.visit(node)
    return counter.value


def _function_metrics(path: Path) -> Iterator[tuple[str, int, int]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        return
    collector = _FunctionCollector()
    collector.visit(tree)
    for qualname, node in collector.functions:
        end_lineno = int(getattr(node, "end_lineno", node.lineno) or node.lineno)
        yield qualname, _complexity(node), max(1, end_lineno - int(node.lineno) + 1)


def _fingerprint(repo: str, code_location: str, signal_kind: str) -> str:
    return f"maint-{_safe_digest(chr(10).join((repo, code_location, signal_kind)))}"


def _append_observation(path: Path, event: dict[str, Any]) -> None:
    append_private_text(path, json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def scan_repository(
    repo_root: Path,
    *,
    repo: str,
    home: Path | None = None,
    config: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append one bounded scan of objective Python maintainability breaches."""
    settings = config if isinstance(config, dict) else {}
    if settings.get("enabled") is not True:
        return {"enabled": False, "observed": 0, "skipped": 0}
    root = repo_root.resolve()
    if not root.is_dir():
        return {"enabled": True, "observed": 0, "skipped": 1}

    max_complexity = int(settings.get("max_function_complexity", 15))
    max_lines = int(settings.get("max_function_lines", 120))
    max_candidates = max(1, int(settings.get("max_candidates_per_scan", 10)))
    excluded_parts = set(_DEFAULT_EXCLUDED_PARTS)
    excluded_parts.update({"tests", "test", "site-packages"})
    raw_excluded = settings.get("excluded_paths", [])
    if isinstance(raw_excluded, str):
        raw_excluded = [raw_excluded]
    excluded_prefixes = tuple(
        str(Path(value)).replace("\\", "/").rstrip("/")
        for value in raw_excluded
        if str(value).strip()
    )
    current = now or datetime.now(timezone.utc)
    scan_id = current.date().isoformat()

    observations: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        relative_text = relative.as_posix()
        if any(part in excluded_parts or part.startswith(".") for part in relative.parts):
            continue
        if any(
            relative_text == prefix or relative_text.startswith(f"{prefix}/")
            for prefix in excluded_prefixes
        ):
            continue
        for qualname, complexity, lines in _function_metrics(path):
            location = f"{relative.as_posix()}:{qualname}"
            if complexity > max_complexity:
                observations.append(
                    {
                        "fingerprint": _fingerprint(repo, location, "function_complexity"),
                        "repo": repo,
                        "code_location": location,
                        "signal_kind": "function_complexity",
                        "metric_name": "cyclomatic_complexity",
                        "baseline_metric": float(complexity),
                        "target_metric": float(max_complexity),
                        "timestamp": current.isoformat(),
                        "scan_id": scan_id,
                    }
                )
            if lines > max_lines:
                observations.append(
                    {
                        "fingerprint": _fingerprint(repo, location, "function_lines"),
                        "repo": repo,
                        "code_location": location,
                        "signal_kind": "function_lines",
                        "metric_name": "function_lines",
                        "baseline_metric": float(lines),
                        "target_metric": float(max_lines),
                        "timestamp": current.isoformat(),
                        "scan_id": scan_id,
                    }
                )

    observations.sort(
        key=lambda event: (
            -(float(event["baseline_metric"]) - float(event["target_metric"])),
            str(event["fingerprint"]),
        )
    )
    ledger = maintenance_signals_path(home)
    existing_keys: set[tuple[str, str]] = set()
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            existing_keys.add((str(row.get("fingerprint", "")), str(row.get("scan_id", ""))))
    written = 0
    for event in observations[:max_candidates]:
        key = (str(event["fingerprint"]), scan_id)
        if key in existing_keys:
            continue
        _append_observation(ledger, event)
        existing_keys.add(key)
        written += 1
    return {"enabled": True, "observed": written, "skipped": max(0, len(observations) - written)}


def load_maintenance_candidates(
    *,
    home: Path | None = None,
    min_observations: int = 3,
) -> list[MaintenanceCandidate]:
    ledger = maintenance_signals_path(home)
    if not ledger.exists():
        return []
    grouped: dict[str, dict[str, Any]] = {}
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            fingerprint = str(row["fingerprint"])
            signal_kind = str(row["signal_kind"])
            baseline = float(row["baseline_metric"])
            target = float(row["target_metric"])
            scan_id = str(row["scan_id"])
            if signal_kind not in SUPPORTED_SIGNAL_KINDS or not scan_id:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        group = grouped.setdefault(fingerprint, {"rows": {}, "latest": row})
        group["rows"][scan_id] = row
        if str(row.get("timestamp", "")) >= str(group["latest"].get("timestamp", "")):
            group["latest"] = row
    candidates: list[MaintenanceCandidate] = []
    required = max(1, int(min_observations))
    for fingerprint, group in grouped.items():
        if len(group["rows"]) < required:
            continue
        row = group["latest"]
        candidates.append(
            MaintenanceCandidate(
                fingerprint=fingerprint,
                repo=str(row["repo"]),
                code_location=str(row["code_location"]),
                signal_kind=str(row["signal_kind"]),
                metric_name=str(row["metric_name"]),
                baseline_metric=float(row["baseline_metric"]),
                target_metric=float(row["target_metric"]),
                occurrence_count=len(group["rows"]),
            )
        )
    return sorted(candidates, key=lambda candidate: candidate.fingerprint)


def measure_ticket_metric(repo_root: Path, ticket: Any) -> float:
    """Recompute the ticket's named metric without retaining source content."""
    try:
        relative_path, qualname = str(ticket.code_location).rsplit(":", 1)
    except ValueError as exc:
        raise ValueError("invalid maintenance code_location") from exc
    path = (repo_root / relative_path).resolve()
    root = repo_root.resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("maintenance target is outside the repository")
    for current_qualname, complexity, lines in _function_metrics(path):
        if current_qualname != qualname:
            continue
        if ticket.signal_kind == "function_complexity":
            return float(complexity)
        if ticket.signal_kind == "function_lines":
            return float(lines)
    raise ValueError("maintenance target function was not found")
