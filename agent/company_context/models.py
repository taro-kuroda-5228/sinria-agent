"""Validated, secret-free configuration models for the company context loop."""
from __future__ import annotations
from dataclasses import dataclass
import os

@dataclass(frozen=True)
class ContextConfig:
    workspace_id: str
    owner_id: str
    provider: str = "local"
    enabled: bool = True
    page_size: int = 100
    max_retries: int = 5
    backoff_base: float = 0.25

    def __post_init__(self) -> None:
        if not self.workspace_id or not self.owner_id: raise ValueError("workspace and owner are required")
        if self.provider not in {"local", "fake", "google"}: raise ValueError("unsupported provider")
        if not 1 <= self.page_size <= 1000: raise ValueError("invalid page_size")
        if not 0 <= self.max_retries <= 10 or self.backoff_base < 0: raise ValueError("invalid retry settings")

    @classmethod
    def from_env(cls, *, prefix: str = "SINRIA_CONTEXT_") -> "ContextConfig":
        def val(name: str, default: str) -> str: return os.getenv(prefix + name, default)
        return cls(workspace_id=val("WORKSPACE_ID", "local"), owner_id=val("OWNER_ID", "local"),
                   provider=val("PROVIDER", "local"), enabled=val("ENABLED", "1").lower() not in {"0","false","no"},
                   page_size=int(val("PAGE_SIZE", "100")), max_retries=int(val("MAX_RETRIES", "5")),
                   backoff_base=float(val("BACKOFF_BASE", ".25")))

@dataclass(frozen=True)
class ModelSelection:
    provider: str = "local"
    model: str = "context-retriever"
    temperature: float = 0.0
    def __post_init__(self) -> None:
        if not self.provider or not self.model or not 0 <= self.temperature <= 2: raise ValueError("invalid model selection")
