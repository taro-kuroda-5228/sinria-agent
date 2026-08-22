"""Profile-scoped encrypted local company-context store.

The database contains only authenticated ciphertext and blind-search tokens.  Keys
are supplied by an explicit provider; there is deliberately no plaintext or
machine-wide fallback.  The JSON v1 store is read only for migration and is
rejected unless its payload can be authenticated by the supplied legacy key.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class StoreError(RuntimeError): pass
class MissingKeyError(StoreError): pass
class EnvelopeError(StoreError): pass
class AuthenticationError(StoreError): pass
class OwnerMismatchError(StoreError): pass


class ProfileKeyProvider(Protocol):
    def active(self) -> tuple[str, bytes]: ...
    def get(self, key_id: str) -> bytes: ...
    def versions(self) -> tuple[str, ...]: ...
    def rotate(self, key: bytes | None = None, key_id: str | None = None) -> str: ...
    def retire(self, key_id: str) -> None: ...


class KeyProvider:
    """Small compatibility/test provider with explicit profile scoping.

    ``key`` is accepted for compatibility with the former API, but production
    callers should use :class:`KeychainKeyProvider` or an injected provider.
    """
    def __init__(self, key: bytes, *, profile_id: str = "test-profile", key_id: str = "k1"):
        self.profile_id = profile_id
        self._keys: dict[str, bytes] = {key_id: _check_key(key)}
        self._active = key_id
    def active(self): return self._active, self._keys[self._active]
    def get(self, key_id):
        try: return self._keys[key_id]
        except KeyError as exc: raise MissingKeyError("key is unavailable") from exc
    def versions(self): return tuple(self._keys)
    def get_key(self): return self.active()[1]  # legacy compatibility
    def rotate(self, key=None, key_id=None):
        kid = key_id or f"k{len(self._keys) + 1}"
        self._keys[kid] = _check_key(key or secrets.token_bytes(32)); self._active = kid
        return kid
    def retire(self, key_id):
        if key_id == self._active: raise ValueError("cannot retire active key")
        self._keys.pop(key_id, None)


class EnvKeyProvider(KeyProvider):
    """Explicit CI/headless provider. Requires SINRIA_CONTEXT_KEY (base64)."""
    def __init__(self, *, profile_id: str, environ: Mapping[str, str] | None = None):
        value = (environ or os.environ).get("SINRIA_CONTEXT_KEY")
        if not value: raise MissingKeyError("SINRIA_CONTEXT_KEY is required")
        try: key = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except Exception as exc: raise MissingKeyError("invalid environment key") from exc
        super().__init__(key, profile_id=profile_id, key_id="env-v1")


class KeychainKeyProvider(KeyProvider):
    """macOS Keychain-backed provider (no secret is emitted in subprocess args)."""
    def __init__(self, profile_id: str, *, service: str = "sinria.company-context"):
        self.profile_id, self.service = profile_id, service
        account = hashlib.sha256(profile_id.encode()).hexdigest()
        try:
            raw = subprocess.check_output(["security", "find-generic-password", "-s", service, "-a", account, "-w"], stderr=subprocess.DEVNULL)
            key = base64.urlsafe_b64decode(raw.strip() + b"=" * (-len(raw.strip()) % 4))
        except (subprocess.CalledProcessError, FileNotFoundError):
            key = secrets.token_bytes(32)
            encoded = base64.urlsafe_b64encode(key).decode()
            subprocess.run(["security", "add-generic-password", "-U", "-s", service, "-a", account, "-w", encoded], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        super().__init__(key, profile_id=profile_id, key_id="keychain-v1")


def _check_key(key: bytes) -> bytes:
    if not isinstance(key, bytes) or len(key) != 32: raise ValueError("AES-256 key must be 32 bytes")
    return key


def _canonical(*values: object) -> bytes:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":")).encode()


@dataclass(frozen=True)
class Document:
    doc_id: str; owner_id: str; text: str; metadata: dict


class EncryptedLocalStore:
    FORMAT = 2
    def __init__(self, path: str | Path, key_provider: ProfileKeyProvider, *, profile_id: str | None = None, workspace_id: str = "default-workspace", legacy_key: bytes | None = None):
        self.path, self.keys = Path(path), key_provider
        provider_profile = getattr(key_provider, "profile_id", None)
        if profile_id is not None and provider_profile is not None and profile_id != provider_profile:
            raise OwnerMismatchError("profile_id must match key provider profile_id")
        self.profile_id = profile_id or provider_profile or "default-profile"
        self.workspace_id = workspace_id
        legacy_rows = None
        legacy_backup = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(self.path.parent, 0o700)
        if self.path.exists() and self.path.read_bytes()[:1] in (b"{", b"["):
            if legacy_key is None:
                raise EnvelopeError("legacy JSON requires an explicit migration key")
            legacy_rows = self._decode_legacy(self.path, legacy_key)
            legacy_backup = self.path.with_name(self.path.name + ".migration-backup")
            os.replace(self.path, legacy_backup)
            if os.name == "posix":
                os.chmod(legacy_backup, 0o600)
        self.path.touch(mode=0o600, exist_ok=True)
        if os.name == "posix":
            os.chmod(self.path, 0o600)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA secure_delete=ON")
        self._schema()
        if os.name == "posix":
            for sqlite_path in (self.path, self.path.with_name(self.path.name + "-wal"), self.path.with_name(self.path.name + "-shm")):
                if sqlite_path.exists():
                    os.chmod(sqlite_path, 0o600)
        try:
            if legacy_backup is not None:
                for row in legacy_rows or []:
                    self.put(row["doc_id"], row["owner_id"], row["text"], row.get("metadata"), source=row.get("source", "company-context"))
                legacy_backup.unlink()
        except Exception:
            self.db.close()
            self.path.unlink(missing_ok=True)
            if legacy_backup is not None:
                os.replace(legacy_backup, self.path)
            raise

    @staticmethod
    def _decode_legacy(path: Path, key: bytes):
        _check_key(key)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            blob = base64.b64decode(raw["ciphertext"], validate=True)
            payload = json.loads(AESGCM(key).decrypt(blob[:12], blob[12:], None))
            rows = payload.get("documents", payload) if isinstance(payload, dict) else payload
            return list(rows)
        except InvalidTag as exc:
            raise AuthenticationError("legacy store authentication failed") from exc
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise EnvelopeError("unsupported legacy store") from exc

    def _schema(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS context_documents(
          doc_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
          owner_id TEXT NOT NULL, source TEXT NOT NULL, envelope TEXT NOT NULL,
          key_id TEXT NOT NULL, created_at REAL NOT NULL DEFAULT(unixepoch())
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS context_documents_fts USING fts5(doc_id UNINDEXED, tokens);
        CREATE TABLE IF NOT EXISTS context_quarantine(
          doc_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
          owner_id TEXT NOT NULL, source TEXT NOT NULL, envelope TEXT NOT NULL,
          reason TEXT NOT NULL, created_at REAL NOT NULL DEFAULT(unixepoch())
        );
        CREATE TABLE IF NOT EXISTS context_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        marker_key = f"scoped_doc_ids_v1:{self.profile_id}:{self.workspace_id}"
        marker = self.db.execute("SELECT value FROM context_meta WHERE key=?", (marker_key,)).fetchone()
        if not marker:
            prefix = self._scope_prefix()
            self.db.execute("BEGIN")
            try:
                for row in self.db.execute("SELECT doc_id FROM context_documents WHERE profile_id=? AND workspace_id=?", (self.profile_id, self.workspace_id)).fetchall():
                    old, scoped = row["doc_id"], prefix + row["doc_id"]
                    if old != scoped:
                        self.db.execute("UPDATE context_documents_fts SET doc_id=? WHERE doc_id=?", (scoped, old))
                        self.db.execute("UPDATE context_documents SET doc_id=? WHERE doc_id=? AND profile_id=? AND workspace_id=?", (scoped, old, self.profile_id, self.workspace_id))
                for row in self.db.execute("SELECT doc_id FROM context_quarantine WHERE profile_id=? AND workspace_id=?", (self.profile_id, self.workspace_id)).fetchall():
                    old, scoped = row["doc_id"], prefix + row["doc_id"]
                    if old != scoped:
                        self.db.execute("UPDATE context_quarantine SET doc_id=? WHERE doc_id=? AND profile_id=? AND workspace_id=?", (scoped, old, self.profile_id, self.workspace_id))
                self.db.execute("INSERT OR REPLACE INTO context_meta(key,value) VALUES(?,?)", (marker_key, "1"))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
    def close(self): self.db.close()
    def _scope_prefix(self): return f"{self.profile_id}::{self.workspace_id}::"
    def _storage_id(self, doc_id): return self._scope_prefix() + doc_id
    def _logical_id(self, storage_id):
        prefix = self._scope_prefix()
        return storage_id[len(prefix):] if storage_id.startswith(prefix) else storage_id
    def _aad(self, doc_id, owner_id, source): return _canonical(self.FORMAT, self.profile_id, self.workspace_id, owner_id, source, doc_id, "document")
    def _seal(self, value, *, doc_id, owner_id, source, key_id=None):
        kid, key = (key_id, self.keys.get(key_id)) if key_id else self.keys.active()
        nonce = os.urandom(12); body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        token = AESGCM(key).encrypt(nonce, body, self._aad(doc_id, owner_id, source))
        return json.dumps({"v": self.FORMAT, "alg": "AES-256-GCM", "kid": kid, "nonce": base64.b64encode(nonce).decode(), "ct": base64.b64encode(token).decode()}, separators=(",", ":"))
    def _open(self, envelope, *, doc_id, owner_id, source):
        try:
            e = json.loads(envelope); assert e["v"] == self.FORMAT and e["alg"] == "AES-256-GCM"
            nonce, ct = base64.b64decode(e["nonce"], validate=True), base64.b64decode(e["ct"], validate=True)
            if len(nonce) != 12: raise ValueError
            key = self.keys.get(e["kid"])
            return json.loads(AESGCM(key).decrypt(nonce, ct, self._aad(doc_id, owner_id, source)))
        except MissingKeyError: raise
        except InvalidTag as exc: raise AuthenticationError("ciphertext authentication failed") from exc
        except Exception as exc: raise EnvelopeError("malformed envelope") from exc
    def _tokens(self, text, key=None):
        key = key or self.keys.active()[1]
        return " ".join(hmac.new(key, w.encode(), hashlib.sha256).hexdigest() for w in sorted(set(re.findall(r"[\w-]+", text.lower()))))

    def put(self, doc_id, owner_id, text, metadata=None, *, source="company-context"):
        if not doc_id or not owner_id or not isinstance(text, str): raise ValueError("invalid document")
        value = {"text": text, "metadata": metadata or {}}
        storage_id = self._storage_id(doc_id)
        envelope = self._seal(value, doc_id=doc_id, owner_id=owner_id, source=source)
        if re.search(r"ignore (?:all|any|the )?previous instructions|system prompt|jailbreak", text, re.I):
            self.db.execute("INSERT OR REPLACE INTO context_quarantine(doc_id,profile_id,workspace_id,owner_id,source,envelope,reason) VALUES(?,?,?,?,?,?,?)", (storage_id,self.profile_id,self.workspace_id,owner_id,source,envelope,"prompt_injection"))
            self.db.execute("DELETE FROM context_documents WHERE doc_id=? AND profile_id=? AND workspace_id=?", (storage_id,self.profile_id,self.workspace_id))
            self.db.execute("DELETE FROM context_documents_fts WHERE doc_id=?", (storage_id,)); return
        self.db.execute(
            "INSERT OR REPLACE INTO context_documents"
            "(doc_id,profile_id,workspace_id,owner_id,source,envelope,key_id) "
            "VALUES(?,?,?,?,?,?,?)",
            (storage_id, self.profile_id, self.workspace_id, owner_id, source, envelope, json.loads(envelope)["kid"]),
        )
        self.db.execute("DELETE FROM context_documents_fts WHERE doc_id=?", (storage_id,)); self.db.execute("INSERT INTO context_documents_fts VALUES(?,?)", (storage_id,self._tokens(text)))
        self.db.execute("DELETE FROM context_quarantine WHERE doc_id=? AND profile_id=? AND workspace_id=?", (storage_id,self.profile_id,self.workspace_id))

    def search(self, owner_id, query, limit=20):
        if not owner_id or not query: return []
        terms = set(re.findall(r"[\w-]+", query.lower())); matches = []
        for row in self.db.execute("SELECT * FROM context_documents WHERE profile_id=? AND workspace_id=? AND owner_id=?", (self.profile_id,self.workspace_id,owner_id)):
            try:
                logical_id = self._logical_id(row["doc_id"])
                d = self._open(row["envelope"], doc_id=logical_id, owner_id=owner_id, source=row["source"])
                score = sum(1 for token in terms if any(hmac.compare_digest(hmac.new(k, token.encode(), hashlib.sha256).hexdigest(), x) for k in (self.keys.get(i) for i in self.keys.versions()) for x in self.db.execute("SELECT tokens FROM context_documents_fts WHERE doc_id=?", (row["doc_id"],)).fetchone()[0].split()))
                if score: matches.append((score, Document(logical_id, owner_id, d["text"], d.get("metadata", {}))))
            except StoreError: continue
        return [d for _, d in sorted(matches, key=lambda x: (-x[0], x[1].doc_id))[:limit]]

    def revoke(self, owner_id):
        ids = [r[0] for r in self.db.execute("SELECT doc_id FROM context_documents WHERE profile_id=? AND workspace_id=? AND owner_id=?", (self.profile_id,self.workspace_id,owner_id))]
        self.db.execute("DELETE FROM context_documents WHERE profile_id=? AND workspace_id=? AND owner_id=?", (self.profile_id,self.workspace_id,owner_id))
        self.db.execute("DELETE FROM context_quarantine WHERE profile_id=? AND workspace_id=? AND owner_id=?", (self.profile_id,self.workspace_id,owner_id))
        if ids:
            self.db.execute(f"DELETE FROM context_documents_fts WHERE doc_id IN ({','.join('?'*len(ids))})", ids)

    def prune_source(self, owner_id: str, source: str, keep_doc_ids: set[str]) -> int:
        """Remove synchronized source documents no longer present in its manifest."""
        rows = self.db.execute(
            "SELECT doc_id FROM context_documents WHERE profile_id=? AND workspace_id=? AND owner_id=? AND source=?",
            (self.profile_id, self.workspace_id, owner_id, source),
        ).fetchall()
        stale = [row["doc_id"] for row in rows if self._logical_id(row["doc_id"]) not in keep_doc_ids]
        self.db.execute("BEGIN")
        try:
            for storage_id in stale:
                self.db.execute("DELETE FROM context_documents_fts WHERE doc_id=?", (storage_id,))
                self.db.execute("DELETE FROM context_documents WHERE doc_id=?", (storage_id,))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return len(stale)

    def quarantined(self, doc_id):
        row = self.db.execute("SELECT doc_id,owner_id,reason FROM context_quarantine WHERE doc_id=? AND profile_id=? AND workspace_id=?", (self._storage_id(doc_id),self.profile_id,self.workspace_id)).fetchone()
        if not row: return None
        result = dict(row); result["doc_id"] = doc_id; return result
    def purge_quarantine(self, *, owner_id=None):
        if owner_id is None: return self.db.execute("DELETE FROM context_quarantine WHERE profile_id=? AND workspace_id=?", (self.profile_id,self.workspace_id)).rowcount
        return self.db.execute("DELETE FROM context_quarantine WHERE profile_id=? AND workspace_id=? AND owner_id=?", (self.profile_id,self.workspace_id,owner_id)).rowcount
    def rotate_key(self, new_key=None):
        self.db.execute("BEGIN")
        try:
            kid = self.keys.rotate(new_key)
            for row in self.db.execute("SELECT * FROM context_documents WHERE profile_id=? AND workspace_id=?", (self.profile_id, self.workspace_id)).fetchall():
                logical_id = self._logical_id(row["doc_id"])
                value = self._open(row["envelope"], doc_id=logical_id, owner_id=row["owner_id"], source=row["source"])
                env = self._seal(value, doc_id=logical_id, owner_id=row["owner_id"], source=row["source"], key_id=kid)
                self.db.execute("UPDATE context_documents SET envelope=?,key_id=? WHERE doc_id=? AND profile_id=? AND workspace_id=?", (env,kid,row["doc_id"],self.profile_id,self.workspace_id))
                self.db.execute("DELETE FROM context_documents_fts WHERE doc_id=?", (row["doc_id"],))
                self.db.execute(
                    "INSERT INTO context_documents_fts(doc_id,tokens) VALUES(?,?)",
                    (row["doc_id"], self._tokens(value["text"])),
                )
            self.db.execute("COMMIT")
            return kid
        except Exception:
            self.db.execute("ROLLBACK")
            raise
