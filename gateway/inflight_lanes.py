"""Durable record of which session lanes had an agent turn in flight.

Why this exists
---------------
``GatewayRunner._running_agents`` is the authoritative answer to "which lanes
are mid-turn right now", but it lives in process memory and dies with the
process.  A *graceful* shutdown can still read it — ``_stop_impl`` enumerates
it at the drain deadline and calls ``SessionStore.mark_resume_pending`` for
each lane that was still running — which is why the drain-timeout restart path
resumes exactly the right lanes.

An *unclean* exit (SIGKILL, OOM, panic, power loss) runs no shutdown code at
all, so the next process has to reconstruct that set from durable state.
Historically it guessed: ``SessionStore.suspend_recently_active()`` marked
every session whose ``updated_at`` fell inside a 120s window.  ``updated_at``
is bumped by *any* session touch, including a turn that completed and replied
moments before the crash, so the guess swept in finished lanes and the startup
auto-resume pass posted an unprompted message into their channel.

This module makes the missing evidence durable instead of tightening the
guess.  The journal mirrors the two chokepoints that already maintain
``_running_agents``:

    acquire   the pre-await ``_AGENT_PENDING_SENTINEL`` claim in
              ``_handle_message_with_agent_guard``
    release   ``GatewayRunner._release_running_agent_state()``, documented as
              *the* site that ends a running turn regardless of cause

Because the release site is reached on completion, error, interrupt, /stop and
stale-eviction alike, a record survives in the journal only when the turn
never returned at all — which is precisely the crash case.

Generation ownership and replay
-------------------------------
Every process stamps its records with a generation token minted at
construction.  Startup calls :meth:`InFlightLaneJournal.claim_previous_generation`,
which returns the lanes owned by *other* (dead) generations and then rewrites
the file containing only this generation's records.  Claiming is destructive on
purpose: it is what stops a second, third, ... restart from replaying the same
recovery obligation forever.  The durable obligation is handed off to
``SessionEntry.resume_pending``, which has its own lifecycle and is retired
only by a genuinely completed turn.

Failure posture
---------------
Recovery failures must never produce an unprompted message, so every error
path fails toward *not* resuming:

* write failure on acquire  -> the lane simply is not crash-recoverable
* unreadable / corrupt file -> reported present with an empty lane set
* file absent entirely      -> reported *not* present, so the caller may fall
  back to the legacy heuristic exactly once (the upgrade window); the claim
  writes a fresh empty journal, so every later restart gets the precise
  contract
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, FrozenSet, Optional

from utils import atomic_replace

logger = logging.getLogger(__name__)

# Alongside ``.clean_shutdown`` in the Sinria home: both answer questions the
# next process asks about how the previous one died.
INFLIGHT_LANES_FILENAME = ".inflight_lanes.json"

_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class InFlightClaim:
    """Result of claiming the previous process generation's in-flight lanes.

    ``journal_present`` distinguishes "the previous process recorded nothing"
    (an empty ``session_keys`` with ``journal_present=True`` — authoritative,
    nothing was mid-turn) from "there is no durable record to consult"
    (``journal_present=False`` — the caller may fall back to its legacy
    heuristic).  Collapsing the two would either lose crash recovery on the
    first start after upgrade or keep the heuristic forever.
    """

    session_keys: FrozenSet[str] = frozenset()
    journal_present: bool = False


class InFlightLaneJournal:
    """Crash-durable set of session lanes whose agent turn has not returned.

    Thread-safe: turn start/end can be driven from the gateway event loop, cron
    worker threads, and the API server thread.
    """

    def __init__(
        self,
        home_provider: Callable[[], Optional[Path]],
        *,
        filename: str = INFLIGHT_LANES_FILENAME,
    ) -> None:
        # Resolved lazily on every access rather than captured once: the Sinria
        # home is a module-level global that ``/sethome`` rebinds at runtime
        # (and tests monkeypatch after the runner is constructed).
        self._home_provider = home_provider
        self._filename = filename
        self._generation = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._lanes: Dict[str, float] = {}

    @property
    def generation(self) -> str:
        """Token identifying this process's records inside the journal."""
        return self._generation

    # -- paths -------------------------------------------------------------

    def _path(self) -> Optional[Path]:
        try:
            home = self._home_provider()
        except Exception:
            return None
        if home is None:
            return None
        try:
            return Path(home) / self._filename
        except (TypeError, ValueError):
            return None

    # -- turn lifecycle ----------------------------------------------------

    def mark_in_flight(self, session_key: str) -> bool:
        """Record that ``session_key`` has a turn executing.

        Called from the same statement that claims the in-memory lane, so the
        durable record cannot lag the sentinel across an await point.
        """
        if not session_key:
            return False
        with self._lock:
            if session_key in self._lanes:
                return True
            self._lanes[session_key] = time.time()
            return self._flush_locked("mark in-flight", session_key)

    def mark_settled(self, session_key: str) -> bool:
        """Record that ``session_key``'s turn has terminated.

        Terminal for this purpose means "the turn is no longer executing" —
        completion, failure, interrupt and /stop all qualify, because none of
        them leaves an agent mid-flight for the next process to rescue.  The
        drain-timeout path is unaffected: lanes force-interrupted during
        shutdown were already given a durable ``resume_pending`` marker by
        ``_stop_impl`` before the interrupt fired, and
        ``suspend_recently_active`` skips entries that already carry one.
        """
        if not session_key:
            return False
        with self._lock:
            if self._lanes.pop(session_key, None) is None:
                return False
            return self._flush_locked("mark settled", session_key)

    def in_flight_keys(self) -> FrozenSet[str]:
        """Lanes this process currently believes are mid-turn."""
        with self._lock:
            return frozenset(self._lanes)

    # -- process boundary --------------------------------------------------

    def claim_previous_generation(self) -> InFlightClaim:
        """Take ownership of lanes left in flight by a previous process.

        Returns those lanes and rewrites the journal with only this
        generation's records, so the obligation is delivered exactly once no
        matter how many times the gateway restarts afterwards.
        """
        path = self._path()
        if path is None:
            return InFlightClaim()

        with self._lock:
            try:
                raw = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                # No durable record at all — the upgrade window.  Still write
                # an (empty) journal so the very next restart is precise.
                self._flush_locked("initialize", None)
                return InFlightClaim(frozenset(), journal_present=False)
            except OSError as exc:
                logger.warning(
                    "In-flight lane journal unreadable (%s); "
                    "crash recovery will resume nothing this start",
                    exc,
                )
                return InFlightClaim(frozenset(), journal_present=True)

            claimed = self._parse_foreign_lanes(raw)
            # Anything this process had recorded stays; at startup that set is
            # empty, but keeping it makes a repeat call idempotent rather than
            # destructive.
            self._flush_locked("claim", None)

        if claimed:
            logger.info(
                "Claimed %d in-flight lane(s) from the previous gateway process",
                len(claimed),
            )
        return InFlightClaim(claimed, journal_present=True)

    def _parse_foreign_lanes(self, raw: str) -> FrozenSet[str]:
        """Extract other generations' lane keys from journal text.

        A malformed journal yields the empty set rather than an exception: an
        unreadable record is not evidence that a lane was mid-turn, and
        guessing here is what this whole module exists to stop.
        """
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning(
                "In-flight lane journal is corrupt; "
                "crash recovery will resume nothing this start"
            )
            return frozenset()
        if not isinstance(data, dict):
            return frozenset()
        if data.get("version") != _SCHEMA_VERSION:
            # Written by a version whose record shape we cannot vouch for
            # (most likely a downgrade).  Reading it as if it were v1 could
            # resume the wrong lanes; refusing costs one restart's worth of
            # crash recovery, which is the cheaper mistake.
            logger.warning(
                "In-flight lane journal has unsupported version %r; "
                "crash recovery will resume nothing this start",
                data.get("version"),
            )
            return frozenset()
        lanes = data.get("lanes")
        if not isinstance(lanes, dict):
            return frozenset()

        claimed = set()
        for session_key, record in lanes.items():
            if not isinstance(session_key, str) or not session_key:
                continue
            if not isinstance(record, dict):
                # Corrupt within a version we do claim to understand — no
                # trustworthy provenance, so do not resume on its say-so.
                continue
            if record.get("generation") == self._generation:
                continue
            claimed.add(session_key)
        return frozenset(claimed)

    # -- persistence -------------------------------------------------------

    def _flush_locked(self, action: str, session_key: Optional[str]) -> bool:
        """Atomically persist the current lane set. Caller holds ``_lock``."""
        path = self._path()
        if path is None:
            return False
        payload = {
            "version": _SCHEMA_VERSION,
            "generation": self._generation,
            "lanes": {
                key: {"started_at": started_at, "generation": self._generation}
                for key, started_at in self._lanes.items()
            },
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=".inflight_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                    handle.flush()
                    # The whole point is surviving a power loss / OOM kill, so
                    # the record has to be on the platter before the turn that
                    # it describes is allowed to proceed.
                    os.fsync(handle.fileno())
                atomic_replace(tmp_path, path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as exc:
            # Never break a turn over bookkeeping.  The cost is bounded and
            # falls on the safe side: an unrecorded lane is not auto-resumed.
            logger.warning(
                "Could not %s lane %s in the in-flight journal: %s",
                action,
                session_key or "(none)",
                exc,
            )
            return False
        return True
