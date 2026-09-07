#!/usr/bin/env python3
"""Fixed allowlist dispatcher for local team-project capabilities."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sinria_constants import get_sinria_home
from sinria_peer_runtime import (
    MAINTENANCE_CAPABILITY,
    OFFICIAL_ORIGIN,
    RuntimeMaintenanceError,
    execute_maintenance,
    write_activation_request,
)

ROOT = Path(__file__).resolve().parents[1]


def _run_canary(meta: dict) -> dict:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/team-project-control-plane-canary.py")],
        input=json.dumps(meta, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeMaintenanceError("runtime_canary_failed")
    try:
        result = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeMaintenanceError("runtime_canary_failed") from exc
    if not isinstance(result, dict):
        raise RuntimeMaintenanceError("runtime_canary_failed")
    return result


def _schedule_activation(release_root: Path, _metadata: dict) -> None:
    write_activation_request(release_root, _metadata, Path(get_sinria_home()) / "runtime")


def _run_maintenance(meta: dict) -> dict:
    source_root = Path(os.environ.get("SINRIA_MAINTENANCE_SOURCE_ROOT", ROOT))
    runtime_root = Path(get_sinria_home()) / "runtime"
    return execute_maintenance(
        meta,
        source_root=source_root,
        runtime_root=runtime_root,
        allowed_origin=OFFICIAL_ORIGIN,
        schedule_activation=_schedule_activation,
    )


def dispatch(
    meta: dict,
    *,
    canary: Callable[[dict], dict] = _run_canary,
    maintenance: Callable[[dict], dict] = _run_maintenance,
) -> dict:
    capability = meta.get("capability") if isinstance(meta, dict) else None
    if capability == "control-plane-canary":
        return canary(meta)
    if capability == MAINTENANCE_CAPABILITY:
        return maintenance(meta)
    raise RuntimeMaintenanceError("runtime_capability_not_allowlisted")


def main() -> int:
    try:
        meta = json.load(sys.stdin)
        result = dispatch(meta)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except RuntimeMaintenanceError as exc:
        print(json.dumps({"errorCode": str(exc)}))
        return 2
    except Exception:
        print(json.dumps({"errorCode": "runtime_execution_rejected"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
