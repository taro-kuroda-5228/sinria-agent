"""Shared sanitization guards for Sinria Context Share v2."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)authorization\s*:\s*bearer\s+\S+"),
    re.compile(r"(?i)bearer\s+(?:sk-|[a-z0-9_\-]{12,})"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bMRN[-_ ]?\d{3,}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"(?i)patient\s+(?:id|identifier)\s*[:=]\s*\S+"),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?:\+?\d[\d ()-]{8,}\d)"),
]

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def contains_sensitive_text(text: str | None) -> bool:
    if not text:
        return False
    return any(pattern.search(text) for pattern in SENSITIVE_PATTERNS)


def assert_sanitized_text(text: str | None, *, field: str = "text", error_cls: type[Exception] = ValueError) -> None:
    if contains_sensitive_text(text):
        raise error_cls(f"{field} contains raw secret/PHI/PII-like content; store a sanitized category or source reference instead")


def assert_safe_identifier(value: str | None, *, field: str = "identifier", error_cls: type[Exception] = ValueError) -> None:
    if not value or not _SAFE_ID.match(str(value)) or contains_sensitive_text(str(value)):
        raise error_cls(f"{field} must be a compact opaque identifier, not raw/private content")


def assert_sanitized_metadata(value, *, field: str = "metadata", error_cls: type[Exception] = ValueError) -> None:
    """Recursively validate metadata values before they become shared rows/prompts."""
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        assert_sanitized_text(value, field=field, error_cls=error_cls)
        return
    if isinstance(value, Mapping):
        for key, nested in value.items():
            assert_sanitized_text(str(key), field=f"{field}.key", error_cls=error_cls)
            assert_sanitized_metadata(nested, field=f"{field}.{key}", error_cls=error_cls)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for idx, nested in enumerate(value):
            assert_sanitized_metadata(nested, field=f"{field}[{idx}]", error_cls=error_cls)
        return
    assert_sanitized_text(str(value), field=field, error_cls=error_cls)
