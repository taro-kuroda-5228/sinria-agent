import importlib.util
import subprocess
from pathlib import Path


def load_script(name):
    path = Path(__file__).parents[1] / 'scripts' / name
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_peer_notifier_includes_sanitized_report_content_and_required_action():
    module = load_script('sinria-peer-notify.py')
    payload = {
        'status': 'decision_required',
        'runId': 'run_123',
        'authorMemberId': 'member_kikuchi',
        'authorInstanceId': 'inst_kikuchi_local',
        'sanitizedPreview': '具体的な共有内容です。',
    }
    assert module.build_message(payload) == (
        '菊地Sinriaから報告を受信しました。\n'
        '内容: 具体的な共有内容です。\n'
        '対応: 内容を確認し、判断または返信してください。\n'
        '送信元: member_kikuchi / inst_kikuchi_local\n'
        'status: decision_required / run: run_123'
    )
    assert module.build_message({'status': 'poll_error', 'runId': 'run_123'}) is None
    assert module.build_message({'status': 'accepted', 'runId': 'run_123'}) is None
    assert 'secret' not in module.build_message({**payload, 'raw': 'secret'})


def test_peer_notifier_sanitizes_and_limits_preview():
    module = load_script('sinria-peer-notify.py')
    message = module.build_message({
        'status': 'accepted',
        'runId': 'run_123',
        'authorMemberId': 'member_kikuchi',
        'sanitizedPreview': '報告\x00本文' + ('長' * 2000),
    })
    assert message is not None
    assert '\x00' not in message
    assert len(message) < 1400


def test_worker_preserves_sanitized_notification_delivery_receipt():
    module = load_script('sinria-peer-worker.py')
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='{"success": true, "target": "discord:123", "messageId": "msg_456"}\n',
        stderr='',
    )

    assert module.notification_receipt(completed) == {
        'status': 'notified',
        'target': 'discord:123',
        'messageId': 'msg_456',
    }


def test_worker_rejects_unverifiable_notification_success():
    module = load_script('sinria-peer-worker.py')
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout='not-json', stderr='')

    assert module.notification_receipt(completed) == {
        'status': 'notify_error',
        'error': 'peer notification delivery failed',
    }


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
