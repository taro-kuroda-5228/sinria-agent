import importlib.util
from pathlib import Path


def load_script(name):
    path = Path(__file__).parents[1] / 'scripts' / name
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_peer_notifier_only_formats_sanitized_validator_results():
    module = load_script('sinria-peer-notify.py')
    assert module.build_message({'status': 'accepted', 'runId': 'run_123'}) == (
        '菊地Sinriaから報告を受信しました。status: accepted / run: run_123'
    )
    assert module.build_message({'status': 'poll_error', 'runId': 'run_123'}) is None
    assert module.build_message({'status': 'accepted', 'runId': 'run_123', 'raw': 'secret'}) == (
        '菊地Sinriaから報告を受信しました。status: accepted / run: run_123'
    )


def test_notify_target_is_validator_only(tmp_path, monkeypatch):
    module = load_script('install-sinria-peer-service.py')
    root = tmp_path / 'sinria-agent'
    (root / 'scripts').mkdir(parents=True)
    for name in ('sinria-peer-worker.py', 'peer-consultation-executor.py', 'synthetic-peer-validator.py'):
        (root / 'scripts' / name).write_text('')
    python = root / '.venv/bin/python'
    python.parent.mkdir(parents=True)
    python.write_text('')
    monkeypatch.setattr(module.Path, 'home', classmethod(lambda cls: tmp_path))
    common = dict(root=root, member_id='m', instance_id='i', subject='s', base_url='https://example.test', poll_interval=15, notify_target='discord:123')
    validator = module.build_plist(mode='validator', **common)
    executor = module.build_plist(mode='executor', **common)
    assert validator['EnvironmentVariables']['PEER_NOTIFY_TARGET'] == 'discord:123'
    assert 'PEER_NOTIFY_TARGET' not in executor['EnvironmentVariables']
