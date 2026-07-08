import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.sinria_boundary_control_report import build_status_report, main
from tests.test_sinria_boundary_control_layer import BASE_CONFIG


def test_boundary_control_status_report_is_metadata_only_and_counts_audit_decisions(tmp_path):
    audit_path = tmp_path / "sinria-egress-audit.jsonl"
    audit_path.write_text(
        json.dumps(
            {
                "decision": {"action": "block", "destination_type": "model_provider"},
                "provider_key": "openai_enterprise",
                "model": "gpt-enterprise-synthetic",
                "boundary_decision": {"data_class": "phi_pii", "allowed": False},
                "sanitized_sample": "患者ID: [REDACTED]",
                "raw_content_included": False,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_status_report(BASE_CONFIG, audit_path=audit_path)

    assert report["product"] == "Sinria Boundary Control Layer"
    assert report["raw_content_included"] is False
    assert report["audit"]["raw_content_included"] is False
    assert report["audit"]["total_records"] == 1
    assert report["audit"]["actions"] == {"block": 1}
    assert report["audit"]["providers"] == {"openai_enterprise": 1}
    serialized = json.dumps(report, ensure_ascii=False)
    assert "TEST-12345" not in serialized
    assert "raw sensitive fixture" not in serialized


def test_boundary_control_report_cli_outputs_json_without_hermes_residue(tmp_path, capsys):
    output_path = tmp_path / "boundary-report.json"

    exit_code = main(["--output", str(output_path), "--format", "json"])

    assert exit_code == 0
    captured = capsys.readouterr().out
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["product"] == "Sinria Boundary Control Layer"
    assert report["external_action_performed"] is False
    assert "Sinria Boundary Control Layer" in captured
    assert "Hermes Agent" not in captured
    assert "~/.hermes" not in captured


def test_boundary_control_report_script_prefers_repo_imports_over_external_pythonpath(tmp_path):
    fake_root = tmp_path / "external"
    fake_agent = fake_root / "agent"
    fake_agent.mkdir(parents=True)
    (fake_agent / "__init__.py").write_text("", encoding="utf-8")
    (fake_agent / "sinria_egress.py").write_text("# missing expected export on purpose\n", encoding="utf-8")
    output_path = tmp_path / "report.json"
    env = dict(os.environ, PYTHONPATH=str(fake_root))

    result = subprocess.run(
        [sys.executable, "scripts/sinria_boundary_control_report.py", "--output", str(output_path)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(output_path.read_text(encoding="utf-8"))["product"] == "Sinria Boundary Control Layer"
