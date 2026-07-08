from unittest.mock import MagicMock, patch

import hermes_cli.main as main


def _ps_line(pid: int, cmd: str) -> str:
    return f"{pid:>7} {cmd}"


def test_find_stale_dashboard_pids_matches_sinria_dashboard(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="\n".join([
                _ps_line(12345, "sinria dashboard --port 9119"),
                _ps_line(12346, "python3 -m hermes_cli.main dashboard --port 9120"),
            ]) + "\n",
            stderr="",
        )
        assert sorted(main._find_stale_dashboard_pids()) == [12345, 12346]


def test_main_source_avoids_remaining_update_gateway_comment_literal():
    source = open(main.__file__, encoding="utf-8").read()
    assert "When running as ``hermes update --gateway``" not in source
    assert "When running as update gateway mode" in source
