"""Canonical improvement bundle activation.

The bundle is local-only.  A revision directory is the single filesystem
commit unit; ``manifest.json`` is the metadata/hash-chain commit point.
Company OS integrations receive :meth:`company_os_payload`, never content.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback keeps thread safety
    fcntl = None

ARTIFACTS = ("skill", "rulebook", "test", "runbook")
_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


class ActivationError(RuntimeError): pass
class EligibilityError(ActivationError): pass
class CASConflict(ActivationError): pass
class ScopeViolation(ActivationError): pass
class ManifestError(ActivationError): pass
class ReindexError(ActivationError): pass


def _digest(value: bytes | str) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _safe(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or not _SAFE.fullmatch(value) or value in {".", ".."}:
        raise ScopeViolation(f"invalid {label}")
    return value


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    profile_id: str
    team_id: str
    revision: int
    artifacts: Mapping[str, str]
    approved: bool = False
    replay_success: bool = False
    canary_success: bool = False

    def __post_init__(self) -> None:
        _safe(self.proposal_id, "proposal_id"); _safe(self.profile_id, "profile_id"); _safe(self.team_id, "team_id")
        if self.revision < 1 or set(self.artifacts) != set(ARTIFACTS):
            raise ValueError("a proposal must contain exactly the four artifacts")
        if any(not isinstance(v, str) for v in self.artifacts.values()): raise ValueError("artifact content must be text")


@dataclass(frozen=True)
class ActivationResult:
    proposal_id: str
    profile_id: str
    team_id: str
    revision: int
    status: str
    manifest_hash: str
    previous_manifest_hash: str | None
    artifact_hashes: Mapping[str, str]

    def company_os_payload(self) -> dict[str, Any]:
        return {"proposal_id": self.proposal_id, "profile_id": self.profile_id,
                "team_id": self.team_id, "revision": self.revision,
                "status": self.status, "manifest_hash": self.manifest_hash,
                "previous_manifest_hash": self.previous_manifest_hash,
                "artifact_hashes": dict(self.artifact_hashes)}


class CanonicalActivation:
    """Filesystem-backed activation service, scoped by (profile_id, team_id)."""
    def __init__(self, root: str | Path, *, indexer: Callable[[Path, dict[str, Any]], Any] | None = None,
                 company_os_sink: Callable[[dict[str, Any]], Any] | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.indexer = indexer or self._default_indexer
        self.company_os_sink = company_os_sink

    def scope_path(self, profile_id: str, team_id: str) -> Path:
        profile = self.root / _safe(profile_id, "profile_id")
        team = profile / _safe(team_id, "team_id")
        if profile.exists() and profile.is_symlink(): raise ScopeViolation("profile symlink rejected")
        if team.exists() and team.is_symlink(): raise ScopeViolation("team symlink rejected")
        profile.mkdir(exist_ok=True)
        team.mkdir(exist_ok=True)
        return team

    @contextmanager
    def _lock(self, scope: Path):
        path = scope / ".activation.lock"
        with open(path, "a+b") as fh:
            if fcntl is not None: fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try: yield
            finally:
                if fcntl is not None: fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with open(path, "rb") as fh: os.fsync(fh.fileno())

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)

    def _manifest(self, scope: Path) -> dict[str, Any]:
        path = scope / "manifest.json"
        if not path.exists(): return {"head": None, "entries": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("entries", []), list): raise ValueError
            self._verify_manifest(data)
            return data
        except Exception as exc: raise ManifestError("manifest is corrupt or tampered") from exc

    @staticmethod
    def _verify_manifest(manifest: dict[str, Any]) -> None:
        previous = None
        for entry in manifest["entries"]:
            if entry.get("previous_manifest_hash") != previous: raise ManifestError("broken manifest chain")
            body = dict(entry); actual = body.pop("manifest_hash", None)
            if actual != _digest(_json(body)): raise ManifestError("manifest hash mismatch")
            previous = actual
        if manifest.get("head") != previous: raise ManifestError("manifest head mismatch")

    def _write_manifest(self, scope: Path, manifest: dict[str, Any], expected_head: str | None) -> str:
        on_disk = None
        path = scope / "manifest.json"
        if path.exists():
            try: on_disk = json.loads(path.read_text(encoding="utf-8")).get("head")
            except Exception as exc: raise ManifestError("manifest is corrupt") from exc
        if on_disk != expected_head: raise CASConflict("manifest head changed")
        payload = _json(manifest)
        tmp = scope / f".manifest.{os.getpid()}.{time.time_ns()}.tmp"
        tmp.write_bytes(payload); os.chmod(tmp, 0o600); self._fsync_file(tmp)
        os.replace(tmp, scope / "manifest.json"); self._fsync_dir(scope)
        return str(manifest["head"])

    def activate(self, proposal: Proposal, *, expected_revision: int | None = None,
                 expected_head: str | None = None) -> ActivationResult:
        if not (proposal.approved and proposal.replay_success and proposal.canary_success):
            raise EligibilityError("proposal requires approved, replay_success, and canary_success")
        scope = self.scope_path(proposal.profile_id, proposal.team_id)
        with self._lock(scope):
            manifest = self._manifest(scope); prior = manifest.get("head")
            if expected_head is not None and prior != expected_head: raise CASConflict("expected manifest head mismatch")
            current = self.current(scope)
            if expected_revision is not None and current and current["revision"] != expected_revision:
                raise CASConflict("expected revision mismatch")
            for entry in manifest["entries"]:
                if entry.get("proposal_id") == proposal.proposal_id and entry.get("status") == "active":
                    latest = next((e for e in reversed(manifest["entries"]) if e.get("revision") == entry.get("revision")), entry)
                    if latest.get("status") != "active": break
                    if entry.get("revision") != proposal.revision: raise CASConflict("proposal revision mismatch")
                    return self._result(entry)
            revision_dir = scope / "revisions" / str(proposal.revision)
            if revision_dir.exists():
                raise CASConflict("revision already contains a different bundle")
            staging = scope / "revisions" / f".staging-{proposal.revision}-{os.getpid()}-{time.time_ns()}"
            try:
                staging.mkdir(parents=True); hashes = {}
                for name in ARTIFACTS:
                    data = proposal.artifacts[name].encode("utf-8"); hashes[name] = _digest(data)
                    path = staging / name; path.write_bytes(data); os.chmod(path, 0o600); self._fsync_file(path)
                self._fsync_dir(staging); staging.rename(revision_dir); self._fsync_dir(revision_dir.parent)
                entry = {"proposal_id": proposal.proposal_id, "profile_id": proposal.profile_id,
                         "team_id": proposal.team_id, "revision": proposal.revision, "status": "active",
                         "artifact_hashes": hashes, "previous_manifest_hash": prior, "activated_at": time.time()}
                entry["manifest_hash"] = _digest(_json(entry))
                new_manifest = {"head": entry["manifest_hash"], "entries": manifest["entries"] + [entry]}
                self._write_manifest(scope, new_manifest, prior)
                try: self.indexer(revision_dir, entry)
                except Exception as exc: raise ReindexError("activation committed; reindex pending") from exc
                result = self._result(entry)
                if self.company_os_sink: self.company_os_sink(result.company_os_payload())
                return result
            except Exception:
                if staging.exists(): shutil.rmtree(staging, ignore_errors=True)
                # A failed manifest write cannot expose a partial revision.  A revision
                # already committed remains valid and is reported as reindex-pending.
                raise

    def revoke(self, profile_id: str, team_id: str, revision: int, *, expected_head: str | None = None) -> ActivationResult:
        scope = self.scope_path(profile_id, team_id)
        with self._lock(scope):
            manifest = self._manifest(scope); prior = manifest.get("head")
            if expected_head is not None and prior != expected_head: raise CASConflict("expected manifest head mismatch")
            target = next((e for e in reversed(manifest["entries"]) if e.get("revision") == revision and e.get("status") == "active"), None)
            if not target: raise ActivationError("active revision not found")
            replacement = next((e for e in reversed(manifest["entries"]) if e.get("status") == "active" and e.get("revision") != revision), None)
            entry = {"proposal_id": target["proposal_id"], "profile_id": profile_id, "team_id": team_id,
                     "revision": revision, "status": "revoked", "artifact_hashes": target["artifact_hashes"],
                     "previous_manifest_hash": prior, "restored_revision": replacement.get("revision") if replacement else None,
                     "revoked_at": time.time()}
            entry["manifest_hash"] = _digest(_json(entry)); new = {"head": entry["manifest_hash"], "entries": manifest["entries"] + [entry]}
            self._write_manifest(scope, new, prior)
            if replacement: self.indexer(scope / "revisions" / str(replacement["revision"]), replacement)
            else: self.indexer(None, {"status": "revoked", "revision": revision})  # type: ignore[arg-type]
            result = self._result(entry)
            if self.company_os_sink: self.company_os_sink(result.company_os_payload())
            return result

    def current(self, scope: Path | str) -> dict[str, Any] | None:
        scope = Path(scope); manifest = self._manifest(scope)
        active = {e["revision"]: e for e in manifest["entries"] if e.get("status") == "active"}
        revoked = {e["revision"] for e in manifest["entries"] if e.get("status") == "revoked"}
        candidates = [e for rev, e in active.items() if rev not in revoked]
        return max(candidates, key=lambda e: e["revision"]) if candidates else None

    @staticmethod
    def _result(entry: dict[str, Any]) -> ActivationResult:
        return ActivationResult(entry["proposal_id"], entry["profile_id"], entry["team_id"], entry["revision"], entry["status"], entry["manifest_hash"], entry.get("previous_manifest_hash"), entry["artifact_hashes"])

    @staticmethod
    def _default_indexer(revision_dir: Path | None, entry: dict[str, Any]) -> None:
        if revision_dir is None: return
        index = revision_dir.parent.parent / "local-context-index.json"
        data = {"active_revision": entry["revision"], "artifact_hashes": entry["artifact_hashes"]}
        tmp = index.with_suffix(".tmp"); tmp.write_bytes(_json(data)); os.chmod(tmp, 0o600); os.replace(tmp, index)

    def retrieve(self, profile_id: str, team_id: str, query: str) -> list[str]:
        current = self.current(self.scope_path(profile_id, team_id));
        if not current: return []
        text = " ".join((self.scope_path(profile_id, team_id) / "revisions" / str(current["revision"]) / n).read_text() for n in ARTIFACTS)
        return [text] if any(term.lower() in text.lower() for term in query.split()) else []

    def decide(self, profile_id: str, team_id: str, query: str) -> str:
        current = self.current(self.scope_path(profile_id, team_id))
        return f"revision={current['revision']}" if current and self.retrieve(profile_id, team_id, query) else "no-canonical-context"

    def verify(self, profile_id: str, team_id: str) -> dict[str, Any]:
        scope = self.scope_path(profile_id, team_id); m = self._manifest(scope); c = self.current(scope)
        return {"head": m["head"], "current_revision": c["revision"] if c else None, "chain_valid": True,
                "staging残骸なし": not any(p.name.startswith(".staging-") for p in (scope / "revisions").glob(".staging-*") if (scope / "revisions").exists())}
