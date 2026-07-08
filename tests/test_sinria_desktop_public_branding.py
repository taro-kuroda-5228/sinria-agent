"""Sinria desktop (Electron) app must not leak Hermes branding to users.

User-facing strings in ``apps/sinria-desktop`` must say "Sinria". Legacy
``hermes``/``Hermes`` tokens are tolerated ONLY as documented compatibility
internals: env-var aliases (HERMES_HOME / HERMES_CLI_NAME / HERMES_WEB_DIST /
HERMES_DASHBOARD_TUI), backend-protocol identifiers (the injected session-token
global and its header), internal module / route names, and comments that are
compat-framed. Rendered UI strings, window titles, menu labels, product
metadata, and JSON values must be Sinria.

The guard mirrors tests/test_sinria_installers_branding.py and
tests/test_sinria_gateway_script_branding.py: module-level ROOT, utf-8
read_text, forbidden-substring assertions, and a forward-looking skip until the
app exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "apps" / "sinria-desktop"

SCAN_EXTENSIONS = ("*.ts", "*.tsx", "*.js", "*.jsx", "*.cjs", "*.mjs", "*.json", "*.html", "*.css")
# `tests` holds branding/hardening guard fixtures that intentionally embed
# forbidden-pattern literals (e.g. `spawn("hermes"`); they are never shipped UI.
SKIP_DIR_PARTS = {"node_modules", "dist", "out", "build", ".next", ".turbo", ".vite", "release", "tests"}

# Documented compatibility internals. A Hermes token on a line is forgiven when
# the line contains one of these (env aliases, backend-protocol identifiers,
# internal module/route names).
COMPAT_ALLOW_TOKENS = (
    # env-var compatibility aliases (same value/identity as the SINRIA_* vars)
    "HERMES_HOME",
    "HERMES_CLI_NAME",
    "HERMES_OPTIONAL_SKILLS",
    "HERMES_DISABLE_ACTIVE_PROFILE",
    "HERMES_WEB_DIST",
    "HERMES_DASHBOARD_TUI",
    # backend-protocol identifiers (injected token global + auth header)
    "__HERMES_SESSION_TOKEN__",
    "X-Hermes-Session-Token",
    # internal module / route names kept for upstream-merge compatibility
    "hermes_cli",
    "hermes_constants",
    "hermes_bootstrap",
    "/api/hermes",
    "hermes_home",  # backend status field name (the resolved SINRIA_HOME)
    ".hermes",  # legacy on-disk fallback path
)

COMPAT_COMMENT_MARKER = "sinria-compat"
_HERMES_RE = re.compile(r"hermes", re.IGNORECASE)
_COMMENT_PREFIXES = ("//", "*", "/*", "*/", "#")


def _scan_files() -> list[Path]:
    files: list[Path] = []
    for pattern in SCAN_EXTENSIONS:
        for path in DESKTOP.rglob(pattern):
            if SKIP_DIR_PARTS.intersection(path.parts):
                continue
            files.append(path)
    return sorted(files)


def _is_comment_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith(_COMMENT_PREFIXES)


def _line_is_allowlisted(line: str) -> bool:
    if COMPAT_COMMENT_MARKER in line:
        return True
    if _is_comment_line(line):
        # Comments are never user-facing UI; compat-framed prose is allowed.
        return True
    return any(token in line for token in COMPAT_ALLOW_TOKENS)


def test_sinria_desktop_app_directory_is_scannable():
    if not DESKTOP.exists():
        pytest.skip("apps/sinria-desktop not created yet")
    assert _scan_files(), "apps/sinria-desktop has no scannable source files"


def test_sinria_desktop_has_no_user_facing_hermes_leakage():
    if not DESKTOP.exists():
        pytest.skip("apps/sinria-desktop not created yet")

    offenders: list[str] = []
    for path in _scan_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not _HERMES_RE.search(line):
                continue
            if _line_is_allowlisted(line):
                continue
            rel = path.relative_to(ROOT)
            offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert offenders == [], (
        "User-facing Hermes branding leaked into apps/sinria-desktop. Rename to "
        "Sinria, or — if this is a documented compatibility internal — add it to "
        "COMPAT_ALLOW_TOKENS or mark the line with a `sinria-compat` comment:\n"
        + "\n".join(offenders)
    )


def test_sinria_desktop_product_metadata_is_sinria():
    if not DESKTOP.exists():
        pytest.skip("apps/sinria-desktop not created yet")
    import json

    pkg = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))
    assert pkg["name"] == "sinria-desktop"
    assert pkg.get("productName") == "Sinria"
    assert "Sinria" in pkg.get("description", "")
    assert "Hermes" not in pkg.get("description", "")
    # No foreign-product dependency.
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    for name in deps:
        assert "hermes-agent" not in name, f"{name} must not depend on hermes-agent"


def test_sinria_desktop_boots_clean_sinria_home_contract():
    if not DESKTOP.exists():
        pytest.skip("apps/sinria-desktop not created yet")

    blob = "\n".join(p.read_text(encoding="utf-8") for p in _scan_files())

    # Sinria-native identity is required for the backend boot.
    assert "SINRIA_HOME" in blob, "desktop must export SINRIA_HOME for backend boot"
    assert "SINRIA_CLI_NAME" in blob, "desktop must export SINRIA_CLI_NAME=sinria"

    # No dependency on a bare `hermes` executable; the entry point is `sinria`.
    for forbidden in ('spawn("hermes"', "spawn('hermes'", '"hermes-agent"', "'hermes-agent'"):
        assert forbidden not in blob, f"desktop must not invoke {forbidden!r}; use the sinria entry point"

    # The backend is launched via the sinria_cli module, never a PATH binary.
    assert "sinria_cli.main" in blob, "backend must launch via `python -m sinria_cli.main dashboard`"
