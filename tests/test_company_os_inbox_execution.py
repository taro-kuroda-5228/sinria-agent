import json
from unittest.mock import Mock

from sinria_agentos_handlers import LocalExecutionIdentity
from sinria_company_os_inbox_runner import build_gateway_runs_runner


IDENTITY = LocalExecutionIdentity("medical-horizon", "member_taro", "inst_taro")


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def make_task(source_platform):
    return {
        "task_id": "aot_inbox_1",
        "workspace_id": "medical-horizon",
        "agentOsId": "company_os",
        "taskKind": "inbox_request",
        "title": "LINE task",
        "instruction": "依頼された内容を実行する",
        "payload": {"sourcePlatform": source_platform},
        "policy": {"humanApprovalRequired": False},
    }


def make_runner(tmp_path, session):
    return build_gateway_runs_runner(
        base_url="http://127.0.0.1:8642",
        api_key="local-key",
        session=session,
        state_path=tmp_path / "inbox_runs.json",
    )


def test_gateway_runner_marks_line_task_for_shared_line_execution_surface(tmp_path):
    session = Mock()
    session.post.return_value = FakeResponse({"run_id": "run_line", "status": "running"})
    result = make_runner(tmp_path, session)(make_task("line"), IDENTITY)

    assert result["status"] == "in_progress"
    body = session.post.call_args.kwargs["json"]
    assert body["execution_platform"] == "line"
    assert "依頼された内容" in json.dumps(body, ensure_ascii=False)


def test_gateway_runner_does_not_spoof_line_surface_for_other_tasks(tmp_path):
    session = Mock()
    session.post.return_value = FakeResponse({"run_id": "run_other", "status": "running"})
    result = make_runner(tmp_path, session)(make_task("discord"), IDENTITY)

    assert result["status"] == "in_progress"
    body = session.post.call_args.kwargs["json"]
    assert "execution_platform" not in body
