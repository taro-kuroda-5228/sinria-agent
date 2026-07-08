import os
from pathlib import Path
import subprocess
import sys



def _run_help(*args):
    return subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", *args, "--help"],
        capture_output=True,
        text=True,
        env={**os.environ, "HERMES_CLI_NAME": "sinria"},
        check=False,
    ).stdout



def test_sinria_fallback_add_help_uses_sinria():
    out = _run_help("fallback")
    assert "Pick a provider + model (same picker as `sinria" in out
    assert "model`) and append to the chain" in out
    assert "same picker as `hermes model`" not in out



def test_sinria_setup_reconfigure_help_uses_sinria():
    out = _run_help("setup")
    assert "backwards compatibility — a bare 'sinria setup' now" in out
    assert "a bare 'hermes setup' now does this" not in out



def test_sinria_gateway_migrate_legacy_help_uses_product_name():
    out = _run_help("gateway", "migrate-legacy")
    assert "legacy Sinria gateway unit files" in out
    assert "legacy Hermes gateway unit files" not in out


def test_main_help_source_drops_update_docstring_hermes_wording():
    source = Path("hermes_cli/main.py").read_text(encoding="utf-8")
    assert '"""Update Hermes via pip (for PyPI installs)."""' not in source
    assert '"""Update Sinria via pip (for PyPI installs)."""' in source
    assert 'Use Hermes URL heuristics; best for standard OpenAI-compatible endpoints.' not in source
    assert 'Use Sinria URL heuristics; best for standard OpenAI-compatible endpoints.' in source
    assert '"""Restore a Hermes backup from a zip file."""' not in source
    assert '"""Restore a Sinria backup from a zip file."""' in source
    assert 'Medical Horizon Portal' in Path('hermes_cli/status.py').read_text(encoding='utf-8')
    doctor_source = Path('hermes_cli/doctor.py').read_text(encoding='utf-8')
    assert '_nous_portal_label()' in doctor_source
    assert 'portal_auth_label' in doctor_source
