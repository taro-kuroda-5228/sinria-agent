"""Deterministic, metadata-only Sinria peer runtime maintenance."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sinria_team_project_transport import validate_team_project_metadata


OFFICIAL_ORIGIN = "https://github.com/taro-kuroda-5228/sinria-agent.git"
MAINTENANCE_APPROVAL_REF = "company-os://review/sinria-runtime-autoupdate-v1"
MAINTENANCE_CAPABILITY = "sinria-runtime-maintenance"
MAINTENANCE_INPUT_REF = "local://sinria-agent/origin/main"
MAINTENANCE_CRITERIA = ("release staged", "activation scheduled")


class RuntimeMaintenanceError(RuntimeError):
    """A bounded runtime-maintenance failure safe for peer metadata."""


@dataclass(frozen=True)
class StagedRelease:
    revision: str
    release_root: Path
    python: Path


def _safe_environment_value(environment: dict[str, str], key: str) -> str:
    value = str(environment.get(key, "")).strip()
    if not value or len(value) > 300 or "\n" in value:
        raise RuntimeMaintenanceError("runtime_activation_config_invalid")
    return value


def activation_arguments(release_root: Path, environment: dict[str, str]) -> list[list[str]]:
    """Build credential-free installer commands for both worker roles."""

    release_root = release_root.resolve()
    python = next(
        (
            candidate
            for candidate in (release_root / ".venv/bin/python", release_root / "venv/bin/python")
            if candidate.exists()
        ),
        None,
    )
    installer = release_root / "scripts/install-sinria-peer-service.py"
    dispatcher = release_root / "scripts/sinria-team-project-executor.py"
    if python is None:
        raise RuntimeMaintenanceError("runtime_python_missing")
    values = {
        key: _safe_environment_value(environment, key)
        for key in (
            "COMPANY_OS_BASE_URL",
            "COMPANY_OS_MEMBER_ID",
            "COMPANY_OS_INSTANCE_ID",
            "COMPANY_OS_TRANSPORT_SUBJECT",
            "SINRIA_TEAM_SPACE_ID",
            "SINRIA_TEAM_CONVERSATION_ID",
        )
    }
    for key in ("COMPANY_OS_MEMBER_ID", "COMPANY_OS_INSTANCE_ID"):
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", values[key]):
            raise RuntimeMaintenanceError("runtime_activation_config_invalid")
    common = [
        str(python),
        str(installer),
        "--member-id", values["COMPANY_OS_MEMBER_ID"],
        "--instance-id", values["COMPANY_OS_INSTANCE_ID"],
        "--subject", values["COMPANY_OS_TRANSPORT_SUBJECT"],
        "--base-url", values["COMPANY_OS_BASE_URL"],
        "--poll-interval", str(int(environment.get("PEER_POLL_INTERVAL", "15"))),
        "--root", str(release_root),
    ]
    executor = [
        *common,
        "--mode", "executor",
        "--team-capability", "control-plane-canary",
        "--team-capability", MAINTENANCE_CAPABILITY,
        "--team-space-id", values["SINRIA_TEAM_SPACE_ID"],
        "--team-conversation-id", values["SINRIA_TEAM_CONVERSATION_ID"],
        "--team-executor-command", shlex.join([str(python), str(dispatcher)]),
    ]
    validator = [*common, "--mode", "validator"]
    notify_target = str(environment.get("PEER_NOTIFY_TARGET", "")).strip()
    if notify_target:
        if len(notify_target) > 200 or "\n" in notify_target:
            raise RuntimeMaintenanceError("runtime_activation_config_invalid")
        validator.extend(["--notify-target", notify_target])
    return [executor, validator]


def write_activation_request(release_root: Path, metadata: dict, runtime_root: Path) -> None:
    runtime_root = runtime_root.resolve()
    release_root = release_root.resolve()
    if release_root.parent != runtime_root / "releases":
        raise RuntimeMaintenanceError("runtime_activation_path_invalid")
    dispatch_id = str(metadata.get("dispatchId", ""))
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", dispatch_id):
        raise RuntimeMaintenanceError("runtime_activation_config_invalid")
    requests = runtime_root / "activation-requests"
    requests.mkdir(parents=True, exist_ok=True)
    path = requests / f"{dispatch_id}.json"
    payload = {
        "schemaVersion": "sinria.peer-runtime-activation-request.v1",
        "dispatchId": dispatch_id,
        "releaseRoot": str(release_root),
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def launch_activation(
    local_ref: str,
    runtime_root: Path,
    environment: dict[str, str],
    *,
    popen: Callable = subprocess.Popen,
) -> None:
    match = re.fullmatch(
        r"local://peer-runtime-activation/([A-Za-z0-9._:-]{1,120})\.json",
        local_ref,
    )
    if not match:
        raise RuntimeMaintenanceError("runtime_activation_ref_invalid")
    runtime_root = runtime_root.resolve()
    request_path = runtime_root / "activation-requests" / f"{match.group(1)}.json"
    claimed_path = request_path.with_suffix(".claimed")
    if not request_path.exists() and claimed_path.exists():
        return
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeMaintenanceError("runtime_activation_request_invalid") from exc
    release_root = Path(str(request.get("releaseRoot", ""))).resolve()
    if (
        request.get("schemaVersion") != "sinria.peer-runtime-activation-request.v1"
        or request.get("dispatchId") != match.group(1)
        or release_root.parent != runtime_root / "releases"
    ):
        raise RuntimeMaintenanceError("runtime_activation_request_invalid")
    python = next(
        (candidate for candidate in (release_root / ".venv/bin/python", release_root / "venv/bin/python") if candidate.exists()),
        None,
    )
    helper = release_root / "scripts/sinria-peer-runtime-activate.py"
    if python is None or not helper.exists():
        raise RuntimeMaintenanceError("runtime_activation_unavailable")
    try:
        request_path.replace(claimed_path)
    except FileNotFoundError:
        if claimed_path.exists():
            return
        raise RuntimeMaintenanceError("runtime_activation_request_invalid")
    env = {**environment, "SINRIA_RUNTIME_RELEASE_ROOT": str(release_root)}
    try:
        popen(
            [str(python), str(helper)],
            cwd=release_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        claimed_path.replace(request_path)
        raise RuntimeMaintenanceError("runtime_activation_unavailable") from exc


def _git(*args: str, cwd: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeMaintenanceError("runtime_git_unavailable") from exc
    if completed.returncode != 0:
        raise RuntimeMaintenanceError("runtime_git_failed")
    return completed.stdout.strip()


def validate_maintenance_request(value: dict) -> dict:
    try:
        meta = validate_team_project_metadata(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeMaintenanceError("runtime_request_invalid") from exc
    if not meta or meta.get("type") != "task_request":
        raise RuntimeMaintenanceError("runtime_request_invalid")
    required = {
        "capability": MAINTENANCE_CAPABILITY,
        "operation": "write",
        "scope": "local",
        "reversible": True,
        "approvalRef": MAINTENANCE_APPROVAL_REF,
        "inputRefs": [MAINTENANCE_INPUT_REF],
        "acceptanceCriteria": list(MAINTENANCE_CRITERIA),
        "rawContextStored": False,
        "externalActionPerformed": False,
    }
    if any(meta.get(key) != expected for key, expected in required.items()):
        raise RuntimeMaintenanceError("runtime_request_not_allowlisted")
    return meta


def _default_tests(root: Path, python: Path) -> None:
    command = [
        str(root / "scripts/run_tests.sh"),
        "tests/test_peer_runtime_maintenance.py",
        "tests/test_peer_worker_entrypoint.py",
        "tests/test_peer_service_installer.py",
        "tests/test_team_project_transport.py",
        "tests/test_team_project_control_plane_canary.py",
        "-q",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            text=True,
            capture_output=True,
            timeout=600,
            check=False,
            env={**os.environ, "SINRIA_RUNTIME_PYTHON": str(python)},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeMaintenanceError("runtime_tests_failed") from exc
    if completed.returncode != 0:
        raise RuntimeMaintenanceError("runtime_tests_failed")


def stage_release(
    source_root: Path,
    runtime_root: Path,
    *,
    allowed_origin: str = OFFICIAL_ORIGIN,
    run_tests: Callable[[Path, Path], None] = _default_tests,
) -> StagedRelease:
    source_root = source_root.resolve()
    runtime_root = runtime_root.resolve()
    origin = _git("remote", "get-url", "origin", cwd=source_root)
    if origin.rstrip("/") != allowed_origin.rstrip("/"):
        raise RuntimeMaintenanceError("runtime_origin_not_allowlisted")
    _git("fetch", "--prune", "origin", "main", cwd=source_root)
    revision = _git("rev-parse", "origin/main", cwd=source_root)
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise RuntimeMaintenanceError("runtime_revision_invalid")

    releases = runtime_root / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    release_root = releases / revision[:12]
    if not release_root.exists():
        temporary = releases / f".{revision[:12]}.tmp"
        if temporary.exists():
            raise RuntimeMaintenanceError("runtime_release_stage_conflict")
        _git("clone", "--no-checkout", allowed_origin, str(temporary), cwd=releases)
        _git("checkout", "--detach", revision, cwd=temporary)
        temporary.replace(release_root)

    if _git("rev-parse", "HEAD", cwd=release_root) != revision:
        raise RuntimeMaintenanceError("runtime_release_revision_mismatch")
    exclude = release_root / ".git/info/exclude"
    try:
        current_excludes = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        required_excludes = [name for name in (".venv", "venv") if name not in current_excludes.splitlines()]
        if required_excludes:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text(
                current_excludes + "".join(f"{name}\n" for name in required_excludes),
                encoding="utf-8",
            )
    except OSError as exc:
        raise RuntimeMaintenanceError("runtime_release_config_failed") from exc
    if _git("status", "--porcelain", cwd=release_root):
        raise RuntimeMaintenanceError("runtime_release_dirty")

    source_venv = next(
        (candidate for candidate in (source_root / ".venv", source_root / "venv") if candidate.exists()),
        None,
    )
    if source_venv is not None and not (release_root / ".venv").exists():
        (release_root / ".venv").symlink_to(source_venv, target_is_directory=True)
    python = next(
        (
            candidate
            for candidate in (
                release_root / ".venv/bin/python",
                release_root / "venv/bin/python",
                Path(sys.executable),
            )
            if candidate.exists()
        ),
        Path(sys.executable),
    )
    run_tests(release_root, python)
    return StagedRelease(revision=revision, release_root=release_root, python=python)


def _result_for(receipt: dict) -> dict:
    receipt_ref = f"local://peer-runtime-maintenance/{receipt['dispatchId']}.json"
    activation_ref = f"local://peer-runtime-activation/{receipt['dispatchId']}.json"
    return {
        "summary": f"Approved Sinria runtime {receipt['revision'][:12]} staged and activation scheduled.",
        "evidence": [receipt_ref],
        "criteriaEvidence": {criterion: receipt_ref for criterion in MAINTENANCE_CRITERIA},
        "verdict": "accepted",
        "rawContextStored": False,
        "externalActionPerformed": False,
        "_localPostAction": activation_ref,
    }


def execute_maintenance(
    value: dict,
    *,
    source_root: Path,
    runtime_root: Path,
    allowed_origin: str = OFFICIAL_ORIGIN,
    run_tests: Callable[[Path, Path], None] = _default_tests,
    schedule_activation: Callable[[Path, dict], None],
) -> dict:
    meta = validate_maintenance_request(value)
    receipts = runtime_root.resolve() / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    receipt_path = receipts / f"{meta['dispatchId']}.json"
    if receipt_path.exists():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeMaintenanceError("runtime_receipt_invalid") from exc
        return _result_for(receipt)

    staged = stage_release(
        source_root,
        runtime_root,
        allowed_origin=allowed_origin,
        run_tests=run_tests,
    )
    receipt = {
        "schemaVersion": "sinria.peer-runtime-maintenance.v1",
        "dispatchId": meta["dispatchId"],
        "revision": staged.revision,
        "releaseRoot": f"local://sinria-runtime/releases/{staged.revision[:12]}",
        "activationScheduled": True,
        "rawContextStored": False,
        "externalActionPerformed": False,
    }
    schedule_activation(staged.release_root, receipt)
    temporary = receipt_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(receipt_path)
    return _result_for(receipt)
