from __future__ import annotations

import importlib.util
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[2]


def _module():
    spec = importlib.util.spec_from_file_location(
        "audit_release_artifacts", ROOT / "scripts" / "audit_release_artifacts.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wheel(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return path


def test_release_artifact_audit_accepts_runtime_only_wheel(tmp_path):
    artifact = _wheel(tmp_path / "safe.whl", {"sinria_cli/main.py": b"print('ok')"})

    assert _module().audit_artifact(artifact) == []


def test_release_artifact_audit_blocks_private_overlay_and_tests(tmp_path):
    private_path = b"/Users/" + b"tarokuroda" + b"/private"
    artifact = _wheel(
        tmp_path / "unsafe.whl",
        {
            "sinria_cli/main.py": private_path,
            "plugins/example/tests/test_runtime.py": b"pass",
            ".env": b"placeholder",
        },
    )

    violations = _module().audit_artifact(artifact)

    assert any("private overlay marker" in item for item in violations)
    assert any("test code" in item for item in violations)
    assert any("credential file" in item for item in violations)
