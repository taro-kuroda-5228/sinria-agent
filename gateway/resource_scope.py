"""Canonical resource scopes used by cross-channel task coordination."""

from __future__ import annotations

import posixpath
from typing import Literal

ClaimMode = Literal["read", "write", "side_effect"]


def normalize_resource_scope(scope: str) -> str:
    """Normalize a metadata-only scope without resolving or reading local files."""
    value = scope.strip()
    marker = ":path:"
    if marker not in value:
        return value.rstrip(":")
    prefix, raw_path = value.split(marker, 1)
    normalized = posixpath.normpath("/" + raw_path.lstrip("/")).lstrip("/")
    if normalized in ("", "."):
        raise ValueError("resource path must not be empty")
    return f"{prefix}{marker}{normalized}"


def _path_parts(scope: str) -> tuple[str, str] | None:
    marker = ":path:"
    normalized = normalize_resource_scope(scope)
    if marker not in normalized:
        return None
    prefix, path = normalized.split(marker, 1)
    return prefix, path


def _same_or_ancestor(left: str, right: str) -> bool:
    return left == right or right.startswith(left.rstrip("/") + "/")


def resource_scopes_overlap(left: str, right: str) -> bool:
    left = normalize_resource_scope(left)
    right = normalize_resource_scope(right)
    left_path = _path_parts(left)
    right_path = _path_parts(right)
    if left_path is None or right_path is None:
        return left == right
    if left_path[0] != right_path[0]:
        return False
    return _same_or_ancestor(left_path[1], right_path[1]) or _same_or_ancestor(
        right_path[1], left_path[1]
    )


def resource_scopes_conflict(
    left_scope: str,
    left_mode: ClaimMode,
    right_scope: str,
    right_mode: ClaimMode,
) -> bool:
    if left_mode not in ("read", "write", "side_effect"):
        raise ValueError(f"unsupported claim mode: {left_mode}")
    if right_mode not in ("read", "write", "side_effect"):
        raise ValueError(f"unsupported claim mode: {right_mode}")
    if left_mode == right_mode == "read":
        return False
    return resource_scopes_overlap(left_scope, right_scope)
