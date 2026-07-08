import json
import subprocess
import sys
from pathlib import Path

from scripts.sinria_boundary_egress_preview import build_preview, main


def test_preview_script_blocks_synthetic_phi_without_external_action():
    preview = build_preview(
        "患者ID: P-12345 の検査結果を要約",
        provider="openai_enterprise",
        deployment_mode="cloud_enhanced",
    )

    assert preview["data_class"] == "phi_pii"
    assert preview["allowed"] is False
    assert preview["action"] == "block"
    assert preview["external_action_performed"] is False
    assert preview["raw_content_included"] is False
    serialized = json.dumps(preview, ensure_ascii=False)
    assert "P-12345" not in serialized


def test_preview_script_cli_emits_sanitized_json(capsys):
    exit_code = main(
        [
            "--text",
            "患者ID: synthetic-clinical-fixture を外部送信",
            "--provider",
            "openai_enterprise",
            "--deployment-mode",
            "cloud_enhanced",
            "--format",
            "json",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    payload = json.loads(out)
    assert payload["external_action_performed"] is False
    assert payload["raw_content_included"] is False
    assert "MRN-123456" not in out
    assert "raw sensitive fixture" not in out


def test_preview_script_subprocess_runs_locally_without_network():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/sinria_boundary_egress_preview.py",
            "--text",
            "public weather summary",
            "--provider",
            "openai_enterprise",
            "--format",
            "json",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["data_class"] == "public"
    assert payload["external_action_performed"] is False
