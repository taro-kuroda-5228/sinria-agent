from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(
    not Path("docs/sinria-boundary-control-layer.md").exists(),
    reason="Boundary Control product documentation is not included in this distribution",
)


def test_boundary_control_product_doc_names_modes_and_no_raw_data_boundary():
    doc = Path("docs/sinria-boundary-control-layer.md").read_text(encoding="utf-8")

    assert "Sinria Boundary Control Layer" in doc
    assert "Full On-Prem" in doc
    assert "Hybrid Confidential" in doc
    assert "Cloud-Enhanced" in doc
    assert "PHI/PII" in doc
    assert "raw content is not exported" in doc
    assert "Provider Trust Registry" in doc
    assert "scripts/sinria_boundary_control_report.py" in doc
    assert "Hermes Agent" not in doc
    assert "~/.hermes" not in doc


def test_boundary_control_demo_runbook_is_buyer_ready_and_synthetic():
    doc = Path("docs/sinria-boundary-control-demo-runbook.md").read_text(encoding="utf-8")

    assert "Sinria Boundary Control Layer" in doc
    # Names every deployment mode and data class for a reviewer.
    for mode in ("full_on_prem", "hybrid_confidential", "cloud_enhanced"):
        assert mode in doc
    for cls in ("public", "internal", "phi_pii", "credential", "classified"):
        assert cls in doc
    # Demonstrates the local, no-external-send demo path.
    assert "scripts/sinria_boundary_egress_preview.py" in doc
    assert "scripts/sinria_boundary_control_report.py" in doc
    # Synthetic-only and clearly distinguishes readiness from production.
    assert "P-12345" in doc
    assert "What this proves" in doc
    assert "requires explicit human approval" in doc
    # No Hermes residue as a current user-facing default.
    assert "Hermes Agent" not in doc
    assert "~/.hermes" not in doc
