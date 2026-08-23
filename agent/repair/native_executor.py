"""Subprocess boundary for Sinria-native repair workers.

The worker runs in an already isolated worktree. Prompts and output are passed
through private files rather than argv; only allowlisted structural fields are
returned to the caller.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable, Any

from .storage import (
    PrivateStorageUnsupportedError,
    ensure_private_dir,
    open_private,
    write_private_text,
)


class NativeRepairError(RuntimeError):
    pass


class NativeRepairExecutor:
    def __init__(
        self, *, worker_path: str | Path | None = None,
        output_dir: str | Path | None = None,
        runner: Callable[..., Any] = subprocess.run,
        timeout: int = 1800,
    ) -> None:
        self.worker_path = Path(worker_path or Path(__file__).parents[2] / "scripts" / "repair_native_worker.py").resolve()
        if output_dir is None:
            repair_root = Path.home() / ".sinria" / "repair"
            self.output_dir = (repair_root / "native-runs").absolute()
            self._storage_root = repair_root.absolute()
        else:
            self.output_dir = Path(output_dir).expanduser().absolute()
            # Caller-owned parents (for example /tmp) are outside our scope.
            self._storage_root = self.output_dir
        self.runner = runner
        self.timeout = timeout
        self.last_artifacts: dict[str, Path] = {}

    @staticmethod
    def _private_dir(path: Path) -> None:
        ensure_private_dir(path)

    def run(self, worktree: str | Path, sanitized_prompt: str) -> dict[str, Any]:
        root = Path(worktree).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise NativeRepairError("repair worktree is not a directory")
        if not self.worker_path.is_file():
            raise NativeRepairError("Sinria native repair worker is unavailable")
        run_dir = self.output_dir / f"run-{time.time_ns()}"
        repair_root = self._storage_root
        try:
            ensure_private_dir(run_dir, root=repair_root)
        except PrivateStorageUnsupportedError as exc:
            raise NativeRepairError("private repair storage is unsupported on this platform") from exc
        request_path = run_dir / "request.json"
        stdout_path = run_dir / "stdout.json"
        stderr_path = run_dir / "stderr.txt"
        write_private_text(request_path, json.dumps({"prompt": sanitized_prompt}), root=repair_root)
        self.last_artifacts = {"request": request_path, "stdout": stdout_path, "stderr": stderr_path}
        checkout_root = str(self.worker_path.resolve().parent.parent)
        child_env = dict(os.environ)
        existing_pythonpath = child_env.get("PYTHONPATH", "")
        child_env["PYTHONPATH"] = (
            checkout_root
            if not existing_pythonpath
            else f"{checkout_root}{os.pathsep}{existing_pythonpath}"
        )
        try:
            with request_path.open("r", encoding="utf-8") as stdin, open_private(stdout_path, "w+", encoding="utf-8", root=repair_root) as stdout, open_private(stderr_path, "w+", encoding="utf-8", root=repair_root) as stderr:
                completed = self.runner(
                    [sys.executable, str(self.worker_path)], cwd=root, stdin=stdin,
                    stdout=stdout, stderr=stderr, text=True, shell=False,
                    timeout=self.timeout, check=False, env=child_env,
                )
                stdout.flush()
                if completed.returncode != 0:
                    raise NativeRepairError(f"Sinria native repair failed (exit {completed.returncode})")
                stdout.seek(0)
                try:
                    payload = json.load(stdout)
                except (json.JSONDecodeError, TypeError) as exc:
                    raise NativeRepairError("Sinria native repair returned invalid structural output") from exc
        except subprocess.TimeoutExpired as exc:
            raise NativeRepairError("Sinria native repair timed out") from exc
        if not isinstance(payload, dict):
            raise NativeRepairError("Sinria native repair returned invalid structural output")
        result = {
            "ok": payload.get("ok") is True,
            "status": str(payload.get("status", "unknown"))[:80],
        }
        summary = payload.get("sanitizedSummary")
        if isinstance(summary, str) and summary:
            result["sanitizedSummary"] = summary[:500]
        return result
