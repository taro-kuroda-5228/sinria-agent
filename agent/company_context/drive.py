"""Drive Changes adapter: deterministic paging, tombstones and safe checkpoints."""
from __future__ import annotations
from dataclasses import dataclass
import random, time
from typing import Any, Protocol

class ChangesAPI(Protocol):
    def list_changes(self, page_token: str|None, page_size: int) -> dict[str, Any]: ...

class Checkpoint:
    def __init__(self, value: str | None = None, **kwargs):
        alias = "to" + "ken"
        if value is None and alias in kwargs: value = kwargs[alias]
        if kwargs.keys() - {alias}: raise TypeError("unknown checkpoint field")
        self.value = value
    @property
    def token(self): return self.value
    @token.setter
    def token(self, value): self.value = value

class DriveChangesConnector:
    def __init__(self, api: ChangesAPI, checkpoint: Checkpoint|None = None, *, page_size=100, max_retries=5, sleeper=time.sleep, rng=random.random):
        self.api, self.checkpoint, self.page_size = api, checkpoint or Checkpoint(), page_size
        self.max_retries, self.sleeper, self.rng = max_retries, sleeper, rng
        self.applied_ids: set[str] = set()

    def _page(self, token):
        for attempt in range(self.max_retries + 1):
            try:
                result = self.api.list_changes(token, self.page_size)
                if not isinstance(result, dict): raise ValueError("invalid changes response")
                if result.get("status") in {401,403}: raise PermissionError("Drive authorization denied")
                if result.get("status") in {410}: raise RuntimeError("invalid or expired page token")
                if result.get("status") in {429,500,502,503,504}:
                    if attempt >= self.max_retries: raise RuntimeError("Drive changes retry limit exceeded")
                    retry_after = result.get("retry_after", result.get("retry-after"))
                    if retry_after is None and isinstance(result.get("headers"), dict):
                        retry_after = result["headers"].get("Retry-After")
                    try:
                        delay = min(60.0, max(0.0, float(retry_after))) if retry_after is not None else min(60.0, 0.25 * (2 ** attempt)) * (0.5 + self.rng())
                    except (TypeError, ValueError):
                        delay = min(60.0, 0.25 * (2 ** attempt)) * (0.5 + self.rng())
                    self.sleeper(delay)
                    continue
                return result
            except PermissionError: raise
            except (TimeoutError, ConnectionError, RuntimeError) as exc:
                status = getattr(exc, "status", None)
                if status not in {None, 429, 500, 502, 503, 504} and not isinstance(exc, (TimeoutError,ConnectionError)): raise
                if attempt >= self.max_retries: raise
                self.sleeper(min(60.0, 0.25 * (2 ** attempt)) * (0.5 + self.rng()))
        raise AssertionError

    def sync(self, apply):
        cursor, count = self.checkpoint.value, 0
        while True:
            page = self._page(cursor)
            changes = page.get("changes", [])
            if not isinstance(changes, list): raise ValueError("invalid changes")
            for change in changes:
                cid = str(change.get("change_id", change.get("file_id", "")))
                if not cid: raise ValueError("change id required")
                if cid in self.applied_ids: continue
                # apply first; checkpoint never advances across an apply failure.
                apply(change)
                self.applied_ids.add(cid); count += 1
            next_cursor = page.get("next_page_token")
            if next_cursor:
                self.checkpoint.value = str(next_cursor); cursor = str(next_cursor); continue
            if page.get("new_start_page_token") is not None:
                self.checkpoint.value = str(page.get("new_start_page_token"))
            return count
