"""Sinria-native Google OAuth profile: metadata only, never credentials."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone

READ_SCOPES = frozenset({"workspace_read"})
OPTIONAL_SCOPES = frozenset({"gmail_read", "workspace_action"})

@dataclass(frozen=True)
class GoogleAuthProfile:
    profile_id: str
    account_subject: str
    email_hint: str
    granted_scopes: frozenset[str]
    provider: str = "google"
    created_at: str = ""
    def __post_init__(self):
        if not self.profile_id or not self.account_subject or not self.email_hint: raise ValueError("OAuth profile metadata required")
        if not self.granted_scopes <= READ_SCOPES | OPTIONAL_SCOPES: raise ValueError("unsupported OAuth scope")
        if not self.created_at: object.__setattr__(self, "created_at", datetime.now(timezone.utc).isoformat())
    @property
    def read_only(self) -> bool: return "workspace_action" not in self.granted_scopes
    def can(self, scope: str) -> bool: return scope in self.granted_scopes
    def public_metadata(self) -> dict[str, object]:
        return {"profile_id": self.profile_id, "account_subject": self.account_subject, "email_hint": self.email_hint, "granted_scopes": sorted(self.granted_scopes), "provider": self.provider, "created_at": self.created_at}

class OAuthProfileStore:
    """Stores only public metadata. Token/code/secret parameters are rejected."""
    def __init__(self): self._profiles: dict[str, GoogleAuthProfile] = {}
    def save(self, profile: GoogleAuthProfile, **credential_values) -> None:
        if credential_values: raise ValueError("credential values must not be stored")
        self._profiles[profile.profile_id] = profile
    def get(self, profile_id: str) -> GoogleAuthProfile | None: return self._profiles.get(profile_id)
