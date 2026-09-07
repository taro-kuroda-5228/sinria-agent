import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from sinria_peer_runtime import (
    MAINTENANCE_APPROVAL_REF,
    RuntimeMaintenanceError,
    activation_arguments,
    execute_maintenance,
    stage_release,
    validate_maintenance_request,
)

ROOT = Path(__file__).resolve().parents[1]
DISPATCHER = ROOT / "scripts" / "sinria-team-project-executor.py"
ACTIVATOR = ROOT / "scripts" / "sinria-peer-runtime-activate.py"


def load_dispatcher():
    spec = importlib.util.spec_from_file_location("team_project_dispatcher", DISPATCHER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def request(**overrides):
    value = {
        "schemaVersion": "team-project.v1",
        "type": "task_request",
        "dispatchId": "dispatch-maint-1",
        "projectId": "runtime-maint",
        "taskId": "update1",
        "capability": "sinria-runtime-maintenance",
        "summary": "Stage the approved Sinria runtime release.",
        "operation": "write",
        "scope": "local",
        "reversible": True,
        "inputRefs": ["local://sinria-agent/origin/main"],
        "acceptanceCriteria": ["release staged", "activation scheduled"],
        "attempt": 1,
        "approvalRef": MAINTENANCE_APPROVAL_REF,
        "rawContextStored": False,
        "externalActionPerformed": False,
    }
    value.update(overrides)
    return value


def git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
    ).stdout.strip()


def make_source(tmp_path: Path):
    remote = tmp_path / "remote.git"
    source = tmp_path / "source"
    git("init", "--bare", str(remote), cwd=tmp_path)
    git("init", "-b", "main", str(source), cwd=tmp_path)
    git("config", "user.email", "test@example.invalid", cwd=source)
    git("config", "user.name", "Test", cwd=source)
    (source / "scripts").mkdir()
    (source / "scripts" / "install-sinria-peer-service.py").write_text("# installer\n")
    (source / "version.txt").write_text("v1\n")
    git("add", ".", cwd=source)
    git("commit", "-m", "initial", cwd=source)
    git("remote", "add", "origin", str(remote), cwd=source)
    git("push", "-u", "origin", "main", cwd=source)
    target = git("rev-parse", "HEAD", cwd=source)
    return source, remote, target


def test_maintenance_request_is_fixed_scope_and_requires_standing_policy():
    assert validate_maintenance_request(request())["approvalRef"] == MAINTENANCE_APPROVAL_REF

    for unsafe in (
        request(capability="shell"),
        request(inputRefs=["local://arbitrary-command"]),
        request(approvalRef=None),
        request(scope="external"),
        request(reversible=False),
    ):
        with pytest.raises(RuntimeMaintenanceError):
            validate_maintenance_request(unsafe)


def test_stage_release_preserves_dirty_non_main_checkout(tmp_path):
    source, remote, target = make_source(tmp_path)
    git("checkout", "-b", "local-work", cwd=source)
    (source / "version.txt").write_text("private local work\n")
    (source / "untracked.txt").write_text("keep me\n")

    staged = stage_release(
        source,
        tmp_path / "runtime",
        allowed_origin=str(remote),
        run_tests=lambda _root, _python: None,
    )

    assert staged.revision == target
    assert staged.release_root != source
    assert (staged.release_root / ".git").is_dir()
    assert git("branch", "--show-current", cwd=source) == "local-work"
    assert (source / "version.txt").read_text() == "private local work\n"
    assert (source / "untracked.txt").read_text() == "keep me\n"
    assert git("status", "--porcelain", cwd=source)
    assert git("rev-parse", "HEAD", cwd=staged.release_root) == target


def test_stage_release_rejects_an_unapproved_origin(tmp_path):
    source, _remote, _target = make_source(tmp_path)
    with pytest.raises(RuntimeMaintenanceError, match="runtime_origin_not_allowlisted"):
        stage_release(
            source,
            tmp_path / "runtime",
            allowed_origin="https://github.com/taro-kuroda-5228/sinria-agent.git",
            run_tests=lambda _root, _python: None,
        )


def test_execute_maintenance_returns_metadata_only_idempotent_receipt(tmp_path):
    source, remote, target = make_source(tmp_path)
    (source / ".venv/bin").mkdir(parents=True)
    (source / ".venv/bin/python").write_text("")
    scheduled = []

    def schedule(release_root, metadata):
        scheduled.append((release_root, metadata))

    first = execute_maintenance(
        request(),
        source_root=source,
        runtime_root=tmp_path / "runtime",
        allowed_origin=str(remote),
        run_tests=lambda _root, _python: None,
        schedule_activation=schedule,
    )
    second = execute_maintenance(
        request(),
        source_root=source,
        runtime_root=tmp_path / "runtime",
        allowed_origin=str(remote),
        run_tests=lambda _root, _python: None,
        schedule_activation=schedule,
    )
    third = execute_maintenance(
        request(dispatchId="dispatch-maint-2"),
        source_root=source,
        runtime_root=tmp_path / "runtime",
        allowed_origin=str(remote),
        run_tests=lambda _root, _python: None,
        schedule_activation=schedule,
    )

    assert first == second
    assert third["verdict"] == "accepted"
    assert first["verdict"] == "accepted"
    assert first["externalActionPerformed"] is False
    assert set(first["criteriaEvidence"]) == {"release staged", "activation scheduled"}
    assert len(scheduled) == 2
    receipt_uri = first["evidence"][0]
    assert receipt_uri.startswith("local://peer-runtime-maintenance/")
    receipt = json.loads((tmp_path / "runtime" / "receipts" / "dispatch-maint-1.json").read_text())
    assert receipt == {
        "schemaVersion": "sinria.peer-runtime-maintenance.v1",
        "dispatchId": "dispatch-maint-1",
        "revision": target,
        "releaseRoot": f"local://sinria-runtime/releases/{target[:12]}",
        "activationScheduled": True,
        "rawContextStored": False,
        "externalActionPerformed": False,
    }
    serialized = json.dumps(receipt).lower()
    for forbidden in ("token", "password", "credential", "rawprompt", "patientdata"):
        assert forbidden not in serialized


def test_dispatcher_routes_only_fixed_local_capabilities():
    module = load_dispatcher()
    calls = []

    def canary(meta):
        calls.append(("canary", meta["dispatchId"]))
        return {"verdict": "accepted"}

    def maintenance(meta):
        calls.append(("maintenance", meta["dispatchId"]))
        return {"verdict": "accepted"}

    assert module.dispatch(
        {"capability": "control-plane-canary", "dispatchId": "one"},
        canary=canary,
        maintenance=maintenance,
    ) == {"verdict": "accepted"}
    assert module.dispatch(
        {"capability": "sinria-runtime-maintenance", "dispatchId": "two"},
        canary=canary,
        maintenance=maintenance,
    ) == {"verdict": "accepted"}
    with pytest.raises(RuntimeMaintenanceError, match="runtime_capability_not_allowlisted"):
        module.dispatch(
            {"capability": "shell", "command": "rm -rf /"},
            canary=canary,
            maintenance=maintenance,
        )
    assert calls == [("canary", "one"), ("maintenance", "two")]


def test_activation_arguments_install_both_roles_without_credentials(tmp_path):
    release = tmp_path / "release"
    (release / "scripts").mkdir(parents=True)
    (release / ".venv/bin").mkdir(parents=True)
    (release / ".venv/bin/python").write_text("")
    environment = {
        "COMPANY_OS_BASE_URL": "https://company.example",
        "COMPANY_OS_MEMBER_ID": "member_kikuchi",
        "COMPANY_OS_INSTANCE_ID": "inst_kikuchi_local",
        "COMPANY_OS_TRANSPORT_SUBJECT": "discord:member",
        "SINRIA_TEAM_SPACE_ID": "space-team",
        "SINRIA_TEAM_CONVERSATION_ID": "conversation-team",
        "PEER_POLL_INTERVAL": "15",
        "PEER_NOTIFY_TARGET": "discord:home",
        "SINRIA_COMPANY_OS_TRANSPORT_TOKEN": "must-not-enter-command",
    }

    commands = activation_arguments(release, environment)

    assert len(commands) == 2
    joined = json.dumps(commands)
    assert "--mode\", \"executor" in joined
    assert "--mode\", \"validator" in joined
    assert "control-plane-canary" in joined
    assert "sinria-runtime-maintenance" in joined
    assert "sinria-team-project-executor.py" in joined
    assert "must-not-enter-command" not in joined
    assert "SINRIA_COMPANY_OS_TRANSPORT_TOKEN" not in joined


def test_activation_entrypoint_bootstraps_the_staged_release_root():
    source = ACTIVATOR.read_text()
    bootstrap = 'sys.path.insert(0, str(Path(__file__).resolve().parents[1]))'
    assert bootstrap in source
    assert source.index(bootstrap) < source.index("from sinria_constants import")
