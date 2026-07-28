"""Behavioral release/distribution contracts for the public Sinria core."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def _load_release_module():
    spec = importlib.util.spec_from_file_location(
        "_sinria_release_contract", ROOT / "scripts" / "release.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_product_versions_are_in_lockstep():
    version = _project_version()

    cli_init = (ROOT / "hermes_cli" / "__init__.py").read_text(encoding="utf-8")
    cli_match = re.search(r'^__version__\s*=\s*"([^"]+)"', cli_init, re.MULTILINE)
    assert cli_match is not None

    manifest = json.loads(
        (ROOT / "acp_registry" / "agent.json").read_text(encoding="utf-8")
    )
    chart = (ROOT / "deploy" / "helm" / "sinria-local" / "Chart.yaml").read_text(
        encoding="utf-8"
    )
    chart_match = re.search(r'^appVersion:\s*"([^"]+)"', chart, re.MULTILINE)
    assert chart_match is not None

    assert cli_match.group(1) == version
    assert manifest["version"] == version
    assert manifest["distribution"]["uvx"]["package"] == f"sinria-agent[acp]=={version}"
    assert chart_match.group(1) == version


def test_release_tags_use_the_product_semver():
    release = _load_release_module()
    assert release.release_tag_for_version("1.2.3") == "v1.2.3"


def test_release_workflow_builds_and_attaches_verified_artifacts():
    workflow = ROOT / ".github" / "workflows" / "release.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "scripts/run_tests.sh" in text
    assert "uv build --sdist --wheel" in text
    assert "scripts/audit_release_artifacts.py" in text
    assert "sha256sum" in text
    assert "scripts/install.sh" in text
    assert "scripts/install.ps1" in text
    assert "softprops/action-gh-release" in text


def test_distribution_sources_use_the_canonical_public_repository():
    canonical = "taro-kuroda-5228/sinria-agent"
    paths = [
        ROOT / "README.md",
        ROOT / "scripts" / "install.sh",
        ROOT / "scripts" / "install.full.ps1",
        ROOT / "website" / "docs" / "getting-started" / "installation.md",
        ROOT / "hermes_cli" / "main.py",
        ROOT / "hermes_cli" / "banner.py",
        ROOT / "scripts" / "release.py",
        ROOT / "gateway" / "platforms" / "telegram.py",
        ROOT / "tools" / "discord_tool.py",
    ]

    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert canonical in combined
    assert "Medical-Horizon/sinria-agent" not in combined
    assert "NousResearch/hermes-agent" not in combined
    assert "NousResearch/sinria-agent" not in combined

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "rolling `main` channel" in readme
    assert "GitHub Releases" in readme
    assert "sinria update --check" in readme


def test_public_core_and_sdist_manifest_exclude_private_overlays():
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "prune tests" in manifest
    assert "global-exclude .env" in manifest
    assert "global-exclude *.pem" in manifest
    assert "global-exclude *.key" in manifest
    assert "global-exclude test_*.py" in manifest
    assert "global-exclude *_test.py" in manifest
    assert "global-exclude conftest.py" in manifest
    assert "global-exclude pytest.ini" in manifest
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'exclude = ["tests", "tests.*", "*.tests", "*.tests.*"]' in pyproject
    assert '[tool.setuptools.exclude-package-data]' in pyproject
    assert '"*" = ["tests/*", "*/tests/*", "test_*.py", "*_test.py", "conftest.py", "pytest.ini"]' in pyproject

    forbidden = ("/Users/" + "tarokuroda", "exbrain-" + "vault")
    suffixes = {".py", ".md", ".json", ".toml", ".yml", ".yaml", ".sh", ".ps1"}
    violations: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in {".git", ".venv", "dist", "build"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(value in text for value in forbidden):
            violations.append(str(path.relative_to(ROOT)))
    assert not violations, f"private overlay references in public core: {violations}"
