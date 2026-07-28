#!/usr/bin/env python3
"""Fail release builds that contain private overlays, tests, or credentials."""

from __future__ import annotations

import argparse
import re
import tarfile
import zipfile
from pathlib import Path

_PRIVATE_MARKERS = (b"/Users/" + b"tarokuroda", b"exbrain-" + b"vault")
_SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9]{24,}"),
    re.compile(rb"ghp_[A-Za-z0-9]{30,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
)


def _entries(path: Path) -> list[tuple[str, bytes]]:
    if path.suffix == ".whl" or path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            return [(name, archive.read(name)) for name in archive.namelist()]
    if path.name.endswith(".tar.gz"):
        entries: list[tuple[str, bytes]] = []
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                handle = archive.extractfile(member)
                entries.append((member.name, handle.read() if handle else b""))
        return entries
    raise ValueError(f"unsupported artifact: {path}")


def audit_artifact(path: Path) -> list[str]:
    violations: list[str] = []
    for name, data in _entries(path):
        leaf = name.rsplit("/", 1)[-1]
        if (
            ("/tests/" in name and (leaf.endswith(".py") or leaf == "pytest.ini"))
            or leaf.startswith("test_")
            or leaf.endswith("_test.py")
        ):
            violations.append(f"test code: {name}")
        if leaf.startswith(".env") or leaf.endswith((".pem", ".key")):
            violations.append(f"credential file: {name}")
        if any(marker in data for marker in _PRIVATE_MARKERS):
            violations.append(f"private overlay marker: {name}")
        if any(pattern.search(data) for pattern in _SECRET_PATTERNS):
            violations.append(f"credential-like value: {name}")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for artifact in args.artifacts:
        violations = audit_artifact(artifact)
        print(f"{artifact}: {'clean' if not violations else 'BLOCKED'}")
        for violation in violations:
            print(f"  - {violation}")
        failed = failed or bool(violations)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
