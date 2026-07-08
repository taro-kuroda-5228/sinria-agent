from pathlib import Path

import yaml


BASE = Path("deploy/k8s/sinria-local/base")


def test_phase5_k8s_manifests_are_valid_yaml_and_include_expected_components():
    docs = []
    for path in sorted(BASE.glob("*.yaml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert loaded["kind"] == "List"
        docs.extend(loaded["items"])

    names = {doc["metadata"]["name"] for doc in docs}

    assert "sinria-local" in names
    assert "sinria-bridge" in names
    assert "sinria-worker-short" in names
    assert "sinria-tool-executor" in names
    assert "sinria-local-default-deny" in names
    assert "sinria-tool-executor-restricted" in names


def test_phase5_runbook_states_no_inbound_ports_and_secret_warning():
    text = Path("deploy/k8s/sinria-local/README.md").read_text(encoding="utf-8")

    assert "without opening\ninbound ports" in text or "not expose inbound ports" in text
    assert "never commit production values" in text
    assert "Cloud apps are shared UI/event surfaces" in text
