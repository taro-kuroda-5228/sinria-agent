#!/usr/bin/env python3
"""Activate a staged Sinria peer release after its response is durable."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sinria_constants import get_sinria_home
from sinria_peer_runtime import RuntimeMaintenanceError, activation_arguments


def main() -> int:
    load_dotenv(Path(get_sinria_home()) / ".env", override=False)
    release_text = os.environ.get("SINRIA_RUNTIME_RELEASE_ROOT", "").strip()
    if not release_text:
        return 2
    release_root = Path(release_text).resolve()
    try:
        commands = activation_arguments(release_root, dict(os.environ))
    except RuntimeMaintenanceError:
        return 2
    time.sleep(float(os.environ.get("SINRIA_RUNTIME_ACTIVATION_DELAY", "5")))
    status = {"schemaVersion": "sinria.peer-runtime-activation.v1", "releaseRoot": release_root.name, "roles": []}
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=release_root,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
        role = command[command.index("--mode") + 1]
        status["roles"].append({"role": role, "installed": completed.returncode == 0})
        if completed.returncode != 0:
            break
    status["rawContextStored"] = False
    status["externalActionPerformed"] = False
    output_dir = Path(get_sinria_home()) / "runtime" / "activation-receipts"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{release_root.name}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(status, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return 0 if len(status["roles"]) == 2 and all(item["installed"] for item in status["roles"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
