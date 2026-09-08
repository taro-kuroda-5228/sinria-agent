import os
import sys


def test_native_compression_preserves_bytes_and_path(tmp_path):
    from agent.local_storage import compress_inactive
    p = tmp_path / 'old.json'
    data = b'{"synthetic": "' + b'a' * 500000 + b'"}'
    p.write_bytes(data)
    os.utime(p, (1, 1))
    result = compress_inactive(p, min_age_days=30)
    assert p.read_bytes() == data
    if sys.platform == 'darwin':
        assert result['status'] == 'compressed'
        assert result['saved_bytes'] > 0
    else:
        assert result['status'] == 'unsupported'


def test_recent_files_and_symlinks_are_not_replaced(tmp_path):
    from agent.local_storage import compress_inactive
    p = tmp_path / 'recent.json'
    p.write_bytes(b'a' * 500000)
    assert compress_inactive(p)['status'] == 'skipped'
    link = tmp_path / 'link.json'
    link.symlink_to(p)
    assert compress_inactive(link)['status'] == 'skipped'
    assert link.is_symlink()
