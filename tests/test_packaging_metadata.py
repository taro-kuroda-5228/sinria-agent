from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_faster_whisper_is_not_a_base_dependency():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]

    assert not any(dep.startswith("faster-whisper") for dep in deps)

    voice_extra = data["project"]["optional-dependencies"]["voice"]
    assert any(dep.startswith("faster-whisper") for dep in voice_extra)


def test_manifest_includes_bundled_skills():
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "graft skills" in manifest
    assert "graft optional-skills" in manifest


def test_setuptools_includes_sinria_cli_package():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    includes = data["tool"]["setuptools"]["packages"]["find"]["include"]

    assert "sinria_cli" in includes
    assert "sinria_cli.*" in includes


def test_setuptools_includes_all_top_level_modules():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packaged_modules = set(data["tool"]["setuptools"]["py-modules"])
    source_modules = {path.stem for path in REPO_ROOT.glob("*.py")}

    assert source_modules <= packaged_modules
