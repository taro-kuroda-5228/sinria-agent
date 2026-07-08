from pathlib import Path

import yaml


def test_helm_chart_starter_has_required_files_and_values():
    root = Path("deploy/helm/sinria-local")

    chart = yaml.safe_load((root / "Chart.yaml").read_text(encoding="utf-8"))
    values = yaml.safe_load((root / "values.yaml").read_text(encoding="utf-8"))

    assert chart["name"] == "sinria-local"
    assert values["bridge"]["transport"] == "polling"
    assert values["bridge"]["dryRun"] is True
    assert (root / "templates/bridge-deployment.yaml").exists()
    assert (root / "templates/configmap.yaml").exists()
    assert "secret manager" in (root / "README.md").read_text(encoding="utf-8")
