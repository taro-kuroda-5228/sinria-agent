"""Production Google Workspace adapters with strict Sinria ownership boundaries."""
from __future__ import annotations
import base64, hashlib, io, json, random, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from sinria_constants import get_sinria_home
DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GMAIL_METADATA_SCOPE = "https://www.googleapis.com/auth/gmail.metadata"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
PRIVATE_SIGNAL_HEADER = "X-Sinria-Private-Signal"
class CredentialError(RuntimeError): pass

def load_stored_user_credentials(profile: str | None = None, *, token_name: str = "google_token.json", scopes: tuple[str, ...] = ()):
    home = Path(get_sinria_home()).expanduser().resolve()
    if profile and not (home.parent.name == "profiles" and home.name == profile):
        raise CredentialError(f"credential profile mismatch for {profile!r}")
    path = home / token_name
    if not path.is_file(): raise CredentialError(f"Google credentials missing: {path}")
    try: payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc: raise CredentialError("Google credentials are malformed JSON") from exc
    if payload.get("type", "authorized_user") != "authorized_user" or not {"client_id", "client_secret", "refresh_token"}.issubset(payload):
        raise CredentialError("Google credentials require authorized-user OAuth fields")
    stored_scopes = set(payload.get("scopes") or ())
    if scopes and stored_scopes and not set(scopes).issubset(stored_scopes): raise CredentialError("requested Google scope is not stored")
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        creds = Credentials.from_authorized_user_file(str(path), list(scopes) or None)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request()); path.write_text(creds.to_json(), encoding="utf-8")
        if not creds.valid: raise CredentialError("stored Google credentials are expired or invalid")
        return creds
    except CredentialError: raise
    except Exception as exc: raise CredentialError("unable to load Google credentials") from exc

@dataclass
class JsonCheckpoint:
    path: Path
    cursor: str | None = None
    def __post_init__(self):
        if self.path.exists():
            try: self.cursor = json.loads(self.path.read_text(encoding="utf-8"))["cursor"]
            except (OSError, ValueError, KeyError): raise ValueError("invalid Drive checkpoint")
    def save(self, cursor: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp"); tmp.write_text(json.dumps({"cursor": cursor}, sort_keys=True)); tmp.replace(self.path); self.cursor = cursor

class GoogleDriveChangesAdapter:
    def __init__(self, service: Any, *, checkpoint: JsonCheckpoint | None = None, drive_id: str | None = None, page_size: int = 100, max_retries: int = 5, sleeper: Callable[[float], None] = time.sleep, rng: Callable[[], float] = random.random):
        self.service, self.checkpoint, self.drive_id = service, checkpoint, drive_id; self.page_size, self.max_retries, self.sleeper, self.rng = page_size, max_retries, sleeper, rng
    def _kwargs(self, cursor: str | None) -> dict[str, Any]:
        args = {"pageSize": self.page_size, "includeItemsFromAllDrives": True, "supportsAllDrives": True, "spaces": "drive"}
        if cursor: args["pageToken"] = cursor
        if self.drive_id: args.update({"driveId": self.drive_id, "corpora": "drive"})
        return args
    def _execute(self, request):
        for attempt in range(self.max_retries + 1):
            try: return request.execute()
            except Exception as exc:
                status = getattr(getattr(exc, "resp", None), "status", None)
                if status not in {429, 500, 502, 503, 504} or attempt >= self.max_retries: raise
                headers = getattr(getattr(exc, "resp", None), "headers", {}) or {}; retry_after = headers.get("Retry-After")
                self.sleeper(float(retry_after) if retry_after else min(60.0, .25 * 2 ** attempt) * (.5 + self.rng()))
    def changes_since(self, cursor: str | None = None) -> dict[str, Any]:
        """Return a complete changes window without persisting a checkpoint."""
        if cursor is None:
            args = {"supportsAllDrives": True}
            if self.drive_id: args["driveId"] = self.drive_id
            cursor = self._execute(self.service.changes().getStartPageToken(**args)).get("startPageToken")
        changes = []
        while True:
            page = self._execute(self.service.changes().list(**self._kwargs(cursor)))
            changes.extend(page.get("changes", []))
            nxt = page.get("nextPageToken")
            if nxt:
                cursor = str(nxt)
                continue
            return {"changes": changes, "next_token": page.get("newStartPageToken", cursor)}
    def sync(self, apply: Callable[[dict[str, Any]], None]) -> int:
        cursor = self.checkpoint.cursor if self.checkpoint else None; count = 0
        if cursor is None:
            args = {"supportsAllDrives": True}
            if self.drive_id: args["driveId"] = self.drive_id
            cursor = self._execute(self.service.changes().getStartPageToken(**args)).get("startPageToken")
        while True:
            page = self._execute(self.service.changes().list(**self._kwargs(cursor)))
            for change in page.get("changes", []): apply(change); count += 1
            nxt = page.get("nextPageToken")
            if nxt:
                cursor = str(nxt)
                if self.checkpoint: self.checkpoint.save(cursor)
                continue
            start = page.get("newStartPageToken")
            if start and self.checkpoint: self.checkpoint.save(str(start))
            return count
    def export(self, file_id: str, *, mime_type: str = "text/plain", max_bytes: int = 2_000_000) -> bytes:
        if mime_type not in {"text/plain", "text/csv", "application/pdf"}: raise ValueError("unsupported export MIME type")
        request = self.service.files().export_media(fileId=file_id, mimeType=mime_type)
        data = request.execute()
        if not isinstance(data, (bytes, bytearray)) or len(data) > max_bytes: raise ValueError("invalid or oversized Drive export")
        return bytes(data)

class GoogleGmailMetadataAdapter:
    def __init__(self, service: Any, *, user_id: str = "me", page_size: int = 100): self.service, self.user_id, self.page_size = service, user_id, page_size
    def list_metadata(self, *, query: str = "", private_only: bool = False) -> list[dict[str, Any]]:
        result, cursor = [], None
        while True:
            args = {"userId": self.user_id, "maxResults": self.page_size}
            if query: args["q"] = query
            if cursor: args["pageToken"] = cursor
            page = self.service.users().messages().list(**args).execute()
            for item in page.get("messages", []):
                msg = self.service.users().messages().get(userId=self.user_id, id=item["id"], format="metadata", metadataHeaders=["From", "To", "Subject", "Date", PRIVATE_SIGNAL_HEADER]).execute()
                headers = {h.get("name", "").lower(): h.get("value", "") for h in msg.get("payload", {}).get("headers", [])}
                signal = headers.get(PRIVATE_SIGNAL_HEADER.lower(), "")
                row = {"id": msg.get("id", item["id"]), "thread_id": msg.get("threadId", item.get("threadId")), "label_ids": tuple(msg.get("labelIds", ())), "private_signal": signal, "headers": headers}
                if not private_only or signal: result.append(row)
            cursor = page.get("nextPageToken")
            if not cursor: return result

class GoogleGmailApprovalAdapter:
    def __init__(self, service: Any, *, owner_id: str, user_id: str = "me", clock: Callable[[], float] = time.time):
        if not owner_id: raise ValueError("owner required")
        self.service, self.owner_id, self.user_id, self.clock = service, owner_id, user_id, clock
    def send(self, *, raw_message: str, approval: dict[str, Any]) -> dict[str, Any]:
        digest = hashlib.sha256(raw_message.encode()).hexdigest()
        if approval.get("owner_id") != self.owner_id or approval.get("payload_hash") != digest or approval.get("revoked") or float(approval.get("expires_at", 0)) < self.clock(): raise PermissionError("approval owner, expiry, revoke, or payload mismatch")
        sent = self.service.users().messages().send(userId=self.user_id, body={"raw": base64.urlsafe_b64encode(raw_message.encode()).decode()}).execute()
        message_id = sent.get("id")
        if not message_id: raise RuntimeError("Gmail send returned no message id")
        return self.service.users().messages().get(userId=self.user_id, id=message_id, format="metadata").execute()


def build_google_workspace_adapters(*, profile: str | None = None, owner_id: str, drive_id: str | None = None, checkpoint: JsonCheckpoint | None = None):
    """Production composition hook; local callers can continue injecting fakes."""
    from googleapiclient.discovery import build
    drive = build("drive", "v3", credentials=load_stored_user_credentials(profile, scopes=(DRIVE_READONLY_SCOPE,)))
    gmail = build("gmail", "v1", credentials=load_stored_user_credentials(profile, scopes=(GMAIL_METADATA_SCOPE, GMAIL_SEND_SCOPE)))
    return {"drive": GoogleDriveChangesAdapter(drive, checkpoint=checkpoint, drive_id=drive_id), "gmail_metadata": GoogleGmailMetadataAdapter(gmail), "gmail_send": GoogleGmailApprovalAdapter(gmail, owner_id=owner_id)}