from pathlib import Path

import pytest

from tests.hermes_cli.test_doctor_command_install import _run_doctor, _setup_doctor_env


@pytest.mark.skipif(__import__('sys').platform == 'win32', reason='Symlink check is Unix-only')
def test_sinria_doctor_uses_sinria_command_names(monkeypatch, tmp_path):
    home, project, sinria_bin = _setup_doctor_env(monkeypatch, tmp_path, venv_name='.venv', cli_name='sinria')

    cmd_link_dir = tmp_path / '.local' / 'bin'
    cmd_link_dir.mkdir(parents=True)
    cmd_link = cmd_link_dir / 'sinria'
    cmd_link.symlink_to(sinria_bin)

    monkeypatch.setattr(Path, 'home', lambda: tmp_path)

    out = _run_doctor(fix=False)
    assert '.local/bin/sinria → correct target' in out
    assert '~/.local/bin/hermes' not in out


@pytest.mark.skipif(__import__('sys').platform == 'win32', reason='Symlink check is Unix-only')
def test_sinria_doctor_fix_guidance_uses_sinria(monkeypatch, tmp_path):
    _setup_doctor_env(monkeypatch, tmp_path, cli_name='sinria')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)

    out = _run_doctor(fix=False)
    assert 'sinria doctor --fix' in out
    assert 'hermes doctor --fix' not in out
