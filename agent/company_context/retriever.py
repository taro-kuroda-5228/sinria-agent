"""Bounded, citation-bearing local context provider."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .store import EncryptedLocalStore


class ContextProvider:
    def __init__(self, store: EncryptedLocalStore, *, per_document_chars: int = 1200, total_chars: int = 6000):
        self.store, self.per_document_chars, self.total_chars = store, per_document_chars, total_chars

    def retrieve(
        self,
        *,
        owner_id: str,
        query: str,
        limit: int = 5,
        profile_id: str | None = None,
        workspace_id: str | None = None,
        session_id: str | None = None,
        source_id: str | None = None,
        source_allowlist: set[str] | frozenset[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not owner_id or not isinstance(query, str) or limit < 1:
            return []
        if profile_id is not None and profile_id != self.store.profile_id:
            return []
        if workspace_id is not None and workspace_id != self.store.workspace_id:
            return []
        try:
            docs = self.store.search(owner_id, query, min(limit, 100))
        except Exception:
            return []
        allowed_sources = set(source_allowlist or ())
        output: list[dict[str, Any]] = []
        used = 0
        for doc in docs:
            if doc.owner_id != owner_id or self.store.quarantined(doc.doc_id) is not None:
                continue
            source_type = str(doc.metadata.get("source", "company_context"))
            if allowed_sources and source_type not in allowed_sources:
                continue
            metadata = dict(doc.metadata or {})
            if metadata.get("profile_id") not in {None, "", self.store.profile_id}:
                continue
            if metadata.get("workspace_id") not in {None, "", self.store.workspace_id}:
                continue
            expires_at = metadata.get("expires_at")
            if isinstance(expires_at, str) and expires_at:
                try:
                    expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    if expires <= datetime.now(timezone.utc):
                        continue
                except ValueError:
                    continue
            text = doc.text[: self.per_document_chars]
            if used + len(text) > self.total_chars:
                text = text[: max(0, self.total_chars - used)]
            if not text:
                break
            output.append(
                {
                    "text": text,
                    "labels": list(metadata.get("labels") or []),
                    "profile_id": self.store.profile_id,
                    "workspace_id": self.store.workspace_id,
                    "owner_id": owner_id,
                    "session_id": session_id,
                    "source_id": str(metadata.get("citation") or source_id or doc.doc_id),
                    "source": {
                        "type": source_type,
                        "id": doc.doc_id,
                        "citation": str(metadata.get("citation") or f"local:{doc.doc_id}"),
                    },
                    "score": 1,
                }
            )
            used += len(text)
            if used >= self.total_chars:
                break
        return output

    def context(self, owner_id: str, query: str, limit: int = 5) -> str:
        return "\n".join(
            f"[{item['source']['citation']}] {item['text']}"
            for item in self.retrieve(owner_id=owner_id, query=query, limit=limit)
        )


Retriever = ContextProvider
