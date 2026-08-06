"""Sinria GCP project-lock preflight helpers.

This module is intentionally read-only by default: it verifies the resolved
Sinria product/repo/GCP project before a human or wrapper runs Cloud Run/GCP
commands. It does not deploy, delete, bill, or mutate resources.
"""

from __future__ import annotations

import json as _json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml


class ProjectLockError(RuntimeError):
    """Raised when a project-lock file is missing, invalid, or unsafe."""


@dataclass(frozen=True)
class ProjectLock:
    product: str
    repo: str
    project_id: str
    region: str
    allowed_services: tuple[str, ...] = field(default_factory=tuple)
    forbidden_adjacent_projects: tuple[str, ...] = field(default_factory=tuple)
    source_path: Path | None = None


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    exit_code: int
    product: str
    expected_project: str
    active_project: str
    region: str
    source_path: str
    side_effect_blocked: bool
    message: str
    auth_checked: bool = False
    auth_ok: bool | None = None


def _norm_product(product: str | None) -> str | None:
    if product is None:
        return None
    value = product.strip().lower()
    return value or None


def _sinria_home(home: Path | None = None) -> Path:
    if home is not None:
        return Path(home).expanduser()
    try:
        from sinria_constants import get_sinria_home

        return Path(get_sinria_home()).expanduser()
    except Exception:
        return Path(os.environ.get("SINRIA_HOME", Path.home() / ".sinria")).expanduser()


def _candidate_lock_paths(
    *, product: str | None = None, cwd: Path | str | None = None, home: Path | None = None
) -> list[Path]:
    paths: list[Path] = []
    env_path = os.getenv("SINRIA_GCP_PROJECT_LOCK")
    if env_path:
        paths.append(Path(env_path).expanduser())

    start = Path(cwd or os.getcwd()).expanduser().resolve()
    if start.is_file():
        start = start.parent
    for directory in (start, *start.parents):
        paths.append(directory / ".sinria" / "project-lock.yaml")
        paths.append(directory / "project-lock.yaml")

    home_dir = _sinria_home(home)
    if product:
        paths.append(home_dir / "projects" / f"{product}.yaml")
        paths.append(home_dir / "project-locks" / f"{product}.yaml")
    paths.append(home_dir / "project-lock.yaml")

    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        try:
            key = p.resolve()
        except OSError:
            key = p
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectLockError(f"project-lock file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ProjectLockError(f"invalid project-lock YAML: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectLockError(f"project-lock must be a YAML object: {path}")
    return raw


def _string_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProjectLockError(f"{field_name} must be a list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ProjectLockError(f"{field_name} entries must be non-empty strings")
        result.append(item.strip())
    return tuple(result)


def _parse_project_lock(raw: dict[str, Any], source_path: Path) -> ProjectLock:
    product = _norm_product(str(raw.get("product", "")))
    repo = str(raw.get("repo", "")).strip()
    gcp = raw.get("gcp")
    if not isinstance(gcp, dict):
        raise ProjectLockError("project-lock requires a gcp object")
    project_id = str(gcp.get("project_id", "")).strip()
    region = str(gcp.get("region", "")).strip()
    if not product:
        raise ProjectLockError("project-lock requires product")
    if not repo:
        raise ProjectLockError("project-lock requires repo")
    if not project_id:
        raise ProjectLockError("project-lock requires gcp.project_id")
    if not region:
        raise ProjectLockError("project-lock requires gcp.region")
    return ProjectLock(
        product=product,
        repo=repo,
        project_id=project_id,
        region=region,
        allowed_services=_string_list(gcp.get("allowed_services"), field_name="gcp.allowed_services"),
        forbidden_adjacent_projects=_string_list(
            raw.get("forbidden_adjacent_projects"), field_name="forbidden_adjacent_projects"
        ),
        source_path=source_path,
    )


def load_project_lock(
    *,
    product: str | None = None,
    cwd: Path | str | None = None,
    lock_file: Path | str | None = None,
    home: Path | None = None,
) -> ProjectLock:
    """Load the Sinria project-lock for ``product`` from repo or Sinria home."""

    wanted = _norm_product(product)
    paths = [Path(lock_file).expanduser()] if lock_file else _candidate_lock_paths(product=wanted, cwd=cwd, home=home)
    attempted: list[str] = []
    for path in paths:
        attempted.append(str(path))
        if not path.exists():
            continue
        lock = _parse_project_lock(_load_yaml(path), path)
        if wanted and lock.product != wanted:
            raise ProjectLockError(
                f"product mismatch: requested {wanted}, project-lock contains {lock.product} ({path})"
            )
        return lock
    raise ProjectLockError(
        "No Sinria GCP project-lock found. Create .sinria/project-lock.yaml in the repo "
        "or ~/.sinria/projects/<product>.yaml. Attempted: " + ", ".join(attempted[:8])
    )


def _run_quiet(args: Sequence[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(args, text=True, capture_output=True, check=False, timeout=30)
    except FileNotFoundError:
        return 127, "", f"{args[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{args[0]} timed out"
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def get_active_gcloud_project() -> str:
    code, out, err = _run_quiet(["gcloud", "config", "get-value", "project", "--quiet"])
    if code != 0:
        raise ProjectLockError(f"failed to read active gcloud project: {err or out or code}")
    return out.strip()


def check_gcloud_auth() -> tuple[bool, str]:
    checks = [
        ["gcloud", "auth", "print-access-token", "--quiet"],
        ["gcloud", "auth", "application-default", "print-access-token", "--quiet"],
    ]
    for args in checks:
        code, _out, err = _run_quiet(args)
        if code != 0:
            return False, f"auth check failed: {' '.join(args[:3])}: {err or code}"
    return True, "auth checks passed"


def preflight_project_lock(
    lock: ProjectLock,
    *,
    active_project: str | None = None,
    check_auth: bool = False,
) -> PreflightResult:
    active = (active_project if active_project is not None else get_active_gcloud_project()).strip()
    auth_ok: bool | None = None
    auth_message = ""
    if check_auth:
        auth_ok, auth_message = check_gcloud_auth()
        if not auth_ok:
            return PreflightResult(
                ok=False,
                exit_code=3,
                product=lock.product,
                expected_project=lock.project_id,
                active_project=active,
                region=lock.region,
                source_path=str(lock.source_path or ""),
                side_effect_blocked=True,
                message=f"GCP auth preflight failed. {auth_message}. No GCP mutation command was run.",
                auth_checked=True,
                auth_ok=False,
            )

    if active != lock.project_id:
        category = "forbidden adjacent project" if active in lock.forbidden_adjacent_projects else "unexpected project"
        return PreflightResult(
            ok=False,
            exit_code=2,
            product=lock.product,
            expected_project=lock.project_id,
            active_project=active,
            region=lock.region,
            source_path=str(lock.source_path or ""),
            side_effect_blocked=True,
            message=(
                f"GCP project mismatch ({category}). No GCP mutation command was run. "
                "Use commands with explicit --project/--region after resolving the requested product."
            ),
            auth_checked=check_auth,
            auth_ok=auth_ok,
        )

    return PreflightResult(
        ok=True,
        exit_code=0,
        product=lock.product,
        expected_project=lock.project_id,
        active_project=active,
        region=lock.region,
        source_path=str(lock.source_path or ""),
        side_effect_blocked=False,
        message=(
            f"GCP project preflight passed for {lock.product}: "
            f"project {lock.project_id}, region {lock.region}."
        ),
        auth_checked=check_auth,
        auth_ok=auth_ok,
    )


def _value_after_flag(args: Sequence[str], flag: str) -> str | None:
    prefix = f"{flag}="
    for i, item in enumerate(args):
        if item == flag and i + 1 < len(args):
            return args[i + 1]
        if item.startswith(prefix):
            return item.split("=", 1)[1]
    return None


def _has_flag(args: Sequence[str], flag: str) -> bool:
    prefix = f"{flag}="
    return any(item == flag or item.startswith(prefix) for item in args)


def append_required_gcloud_scope(command_args: Sequence[str], lock: ProjectLock) -> list[str]:
    """Return gcloud args with the locked --project/--region enforced.

    Existing matching values are preserved; conflicting values fail closed.
    """

    scoped = list(command_args)
    current_project = _value_after_flag(scoped, "--project")
    if current_project and current_project != lock.project_id:
        raise ProjectLockError(
            f"conflicting --project: command has {current_project}, project-lock requires {lock.project_id}"
        )
    current_region = _value_after_flag(scoped, "--region")
    if current_region and current_region != lock.region:
        raise ProjectLockError(f"conflicting --region: command has {current_region}, project-lock requires {lock.region}")
    if not _has_flag(scoped, "--project"):
        scoped.extend(["--project", lock.project_id])
    if not _has_flag(scoped, "--region"):
        scoped.extend(["--region", lock.region])
    return scoped


def _print_preflight(result: PreflightResult, *, as_json: bool = False) -> None:
    data = {
        "ok": result.ok,
        "product": result.product,
        "expected_project": result.expected_project,
        "active_project": result.active_project,
        "region": result.region,
        "source_path": result.source_path,
        "side_effect_blocked": result.side_effect_blocked,
        "auth_checked": result.auth_checked,
        "auth_ok": result.auth_ok,
        "message": result.message,
    }
    if as_json:
        print(_json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if result.ok:
        print("GCP project preflight passed")
    else:
        print("GCP project mismatch" if result.exit_code == 2 else "GCP project preflight failed")
    print(f"- product: {result.product}")
    print(f"- expected project: {result.expected_project}")
    print(f"- active project: {result.active_project or '(empty)'}")
    print(f"- region: {result.region}")
    print(f"- lock: {result.source_path}")
    print(f"- side effect blocked: {str(result.side_effect_blocked).lower()}")
    print(result.message)


def gcp_command(args: Any) -> int:
    sub = getattr(args, "gcp_command", None)
    if sub in {None, ""}:
        print("usage: sinria gcp <preflight|scope> ...")
        return 1
    try:
        lock = load_project_lock(
            product=getattr(args, "product", None),
            cwd=getattr(args, "cwd", None),
            lock_file=getattr(args, "lock_file", None),
        )
        if sub == "preflight":
            result = preflight_project_lock(
                lock,
                active_project=getattr(args, "active_project", None),
                check_auth=bool(getattr(args, "check_auth", False)),
            )
            _print_preflight(result, as_json=bool(getattr(args, "json", False)))
            return result.exit_code
        if sub == "scope":
            raw_args = list(getattr(args, "gcloud_args", []) or [])
            if raw_args and raw_args[0] == "--":
                raw_args = raw_args[1:]
            scoped = append_required_gcloud_scope(raw_args, lock)
            if bool(getattr(args, "json", False)):
                print(_json.dumps({"gcloud_args": scoped, "product": lock.product}, ensure_ascii=False, indent=2))
            else:
                print("gcloud " + " ".join(scoped))
            return 0
    except ProjectLockError as exc:
        if bool(getattr(args, "json", False)):
            print(_json.dumps({"ok": False, "error": str(exc), "side_effect_blocked": True}, ensure_ascii=False))
        else:
            print(f"Sinria GCP project-lock error: {exc}")
            print("No GCP mutation command was run.")
        return 2
    print(f"Unknown gcp subcommand: {sub}")
    return 1
