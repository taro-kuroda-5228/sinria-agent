"""Workshop (Canonical LXD sandbox) execution environment.

Attaches to a pre-provisioned, operator-approved workshop (created with
``workshop init`` / ``workshop launch``) and runs every command through
``workshop exec <name> -- bash -c ...``.

Design notes:

* The adapter **attaches** — it never creates, reconfigures, or widens a
  sandbox.  Mounts, network, and GPU access live in the workshop's own
  YAML definition, which stays a separately reviewable artifact.
* Host environment variables are **never forwarded** into the sandbox.
  Unlike the Docker backend's init-time ``-e`` injection, not inheriting
  host secrets is the point of running inside Workshop.
* stdin is embedded as a heredoc (``_stdin_mode = "heredoc"``) because
  ``workshop exec`` stdin relay is not documented; this works regardless.
"""

import logging
import os
import re
import shutil
import subprocess
from typing import Optional

from tools.environments.base import BaseEnvironment, _popen_bash

logger = logging.getLogger(__name__)

# Well-known snap install location, checked when 'workshop' is not in PATH
# (systemd user sessions and launchd services often lack /snap/bin).
_WORKSHOP_SEARCH_PATHS = [
    "/snap/bin/workshop",
]

_workshop_executable: Optional[str] = None  # resolved once, cached

# Workshop names come from `workshop init <name>`; reject anything that
# could read as shell metacharacters before any CLI call is attempted.
_WORKSHOP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_PROBE_TIMEOUT = 10    # `workshop exec <name> -- true`
_START_TIMEOUT = 120   # `workshop start <name>` may wake SDK services


def _reset_find_cache() -> None:
    """Clear the cached CLI path (tests and post-install rediscovery)."""
    global _workshop_executable
    _workshop_executable = None


def find_workshop() -> Optional[str]:
    """Locate the workshop CLI binary.

    Resolution order:
    1. ``TERMINAL_WORKSHOP_BINARY`` env var — explicit override
    2. ``workshop`` on PATH via ``shutil.which``
    3. Well-known snap install location (/snap/bin/workshop)

    Returns the absolute path, or ``None`` if not found.
    """
    global _workshop_executable
    if _workshop_executable is not None:
        return _workshop_executable

    override = os.getenv("TERMINAL_WORKSHOP_BINARY")
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        _workshop_executable = override
        logger.info("Using TERMINAL_WORKSHOP_BINARY override: %s", override)
        return override

    found = shutil.which("workshop")
    if found:
        _workshop_executable = found
        return found

    for path in _WORKSHOP_SEARCH_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            _workshop_executable = path
            logger.info("Found workshop at non-PATH location: %s", path)
            return path

    return None


class WorkshopEnvironment(BaseEnvironment):
    """Run commands inside a pre-provisioned Canonical Workshop sandbox.

    The sandbox (an LXD system container managed by workshopd) must already
    exist on this host; create one with ``workshop init`` + ``workshop
    launch``.  Capability changes (mounts/network/GPU interfaces) happen by
    editing the workshop YAML and running ``workshop refresh`` — this
    backend deliberately has no flags to widen access.
    """

    # `workshop exec` stdin relay is unverified; heredoc embedding works
    # regardless (same approach as the Modal/Daytona backends).
    _stdin_mode = "heredoc"

    # First exec after a cold start can be slow while services settle.
    _snapshot_timeout = 60

    def __init__(
        self,
        workshop_name: str,
        cwd: str = "/project",
        timeout: int = 60,
        auto_start: bool = False,
        stop_on_cleanup: bool = False,
    ):
        # Initialize cleanup-relevant state first so __del__-driven cleanup
        # is safe even when validation below raises.
        self._stop_on_cleanup = False

        name = (workshop_name or "").strip()
        if not name:
            raise ValueError(
                "Workshop backend requires a workshop name "
                "(set TERMINAL_WORKSHOP_NAME)."
            )
        if not _WORKSHOP_NAME_RE.match(name):
            raise ValueError(f"Invalid workshop name: {name!r}")
        self._workshop_name = name

        exe = find_workshop()
        if not exe:
            raise RuntimeError(
                "Workshop CLI not found. Install it on an Ubuntu host with "
                "`sudo snap install --classic workshop` (requires LXD 6.8+), "
                "or point TERMINAL_WORKSHOP_BINARY at the binary. "
                "Workshop does not run on macOS/Windows hosts."
            )
        self._workshop_exe = exe

        self._ensure_workshop_available(auto_start=auto_start)
        self._stop_on_cleanup = stop_on_cleanup

        super().__init__(cwd=cwd, timeout=timeout)
        self.init_session()

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def _probe(self) -> subprocess.CompletedProcess:
        """Cheapest format-agnostic liveness check: exec `true` inside."""
        try:
            return subprocess.run(
                [self._workshop_exe, "exec", self._workshop_name, "--", "true"],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                args=[],
                returncode=124,
                stdout="",
                stderr=(
                    f"probe timed out after {_PROBE_TIMEOUT}s "
                    "(is workshopd responding?)"
                ),
            )

    def _ensure_workshop_available(self, *, auto_start: bool) -> None:
        result = self._probe()
        if result.returncode == 0:
            return

        if auto_start:
            logger.info(
                "Workshop %r probe failed; attempting `workshop start`",
                self._workshop_name,
            )
            try:
                subprocess.run(
                    [self._workshop_exe, "start", self._workshop_name],
                    capture_output=True,
                    text=True,
                    timeout=_START_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                logger.warning(
                    "`workshop start %s` timed out after %ss",
                    self._workshop_name,
                    _START_TIMEOUT,
                )
            result = self._probe()
            if result.returncode == 0:
                return

        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"Workshop {self._workshop_name!r} is not executable via "
            "`workshop exec`"
            + (f": {detail}" if detail else ".")
            + " Check `workshop list` on this host — create the sandbox with "
            "`workshop init`/`workshop launch`, or start a stopped one with "
            "`workshop start` (TERMINAL_WORKSHOP_AUTO_START=true automates "
            "the latter)."
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _run_bash(
        self,
        cmd_string: str,
        *,
        login: bool = False,
        timeout: int = 120,
        stdin_data: str | None = None,
    ) -> subprocess.Popen:
        """Spawn a bash process inside the workshop sandbox."""
        cmd = [self._workshop_exe, "exec", self._workshop_name, "--"]
        if login:
            cmd.extend(["bash", "-l", "-c", cmd_string])
        else:
            cmd.extend(["bash", "-c", cmd_string])
        return _popen_bash(cmd, stdin_data)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def cleanup(self):
        """No-op by default: the sandbox is operator-owned and persistent.

        With ``stop_on_cleanup=True`` (TERMINAL_WORKSHOP_STOP_ON_CLEANUP),
        stops the workshop on teardown.  Resetting sandbox contents is an
        operator action (``workshop restore``), never an agent side effect.
        """
        if not getattr(self, "_stop_on_cleanup", False):
            return
        self._stop_on_cleanup = False  # idempotent: stop once
        try:
            subprocess.run(
                [self._workshop_exe, "stop", self._workshop_name],
                capture_output=True,
                text=True,
                timeout=60,
            )
            logger.info("Stopped workshop %s", self._workshop_name)
        except Exception as e:
            logger.warning(
                "Failed to stop workshop %s: %s", self._workshop_name, e
            )
