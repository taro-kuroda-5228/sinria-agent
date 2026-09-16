"""Gateway-backed runner for generic Company OS inbox tasks (Stage 1 貫通).

The bridge worker tick has a hard cron budget (`cron.script_timeout_seconds`,
default 120s) while a real agent task can take minutes. This runner therefore
NEVER blocks on execution: it starts an agent run on the local Sinria gateway
(`POST /v1/runs` returns a run_id immediately), persists task_id → run_id in a
small local state file, and reports ``in_progress``. Later ticks re-dispatch
the same task; the runner polls ``GET /v1/runs/{run_id}`` and only produces a
terminal result (completed / waiting_review / failed_recoverable) when the run
has finished. Raw run output stays local — only a sanitized, truncated summary
ever leaves this module.

Design source: docs/plans/2026-07-08-company-os-agent-os-stage1-design.md §4.2.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY_URL = "http://127.0.0.1:8642"
DEFAULT_MAX_RUN_SECONDS = 3600
SUMMARY_MAX_CHARS = 800
ACTIVE_RUN_STATUSES = frozenset({"queued", "started", "running", "stopping"})
REVIEW_REQUIRED_RUN_STATUSES = frozenset({"waiting_for_approval"})

# Self-report marker the safety preamble asks the agent to emit when the task
# needs a human approval before any external side effect is performed.
TASK_STATUS_MARKER = re.compile(
    r"SINRIA_TASK_STATUS:\s*(waiting_review|blocked|completed)", re.IGNORECASE
)


def _last_task_status(text: str) -> str:
    """Return the final explicit marker, not an earlier planning marker."""
    matches = list(TASK_STATUS_MARKER.finditer(text or ""))
    return matches[-1].group(1).lower() if matches else ""

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{8,}", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?:api[_-]?key|client_secret|refresh_token|password)\s*[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),  # JWT
]

# Stage 1 execution constraint injected into every run: internal work is done
# for real; external side effects are planned, never performed, and the agent
# self-reports the approval need via the marker line.
SAFETY_PREAMBLE = (
    "あなたは Medical Horizon の Company OS 経由でルーティングされたタスクを実行しています。\n"
    "制約(Stage 1):\n"
    "- 社内の調査・分析・文書作成・提案作成は実行してよい。\n"
    "- 外部送信(メール/SNS/フォーム)・本番変更/デプロイ・削除・請求/契約/支払い・"
    "認証/権限変更・患者/臨床データ操作は、承認済みと明示されない限り実行しない。"
    "代わりに必要な操作の計画を出力し、回答の最終行に "
    "`SINRIA_TASK_STATUS: waiting_review` と書くこと。\n"
    "- 承認は不要または既に取得済みだが、入力資料・接続・権限など実行前提が不足して"
    "完了できない場合は、承認待ちに戻さず最終行に `SINRIA_TASK_STATUS: blocked` と書くこと。\n"
    "- 上記が不要で完了した場合は最終行に `SINRIA_TASK_STATUS: completed` と書くこと。\n"
    "- 秘密情報(APIキー・トークン・患者情報)を出力に含めない。\n"
)

def sanitize_run_output(text: str) -> str:
    """Redact secret-looking spans and truncate to a cloud-safe summary."""
    out = text or ""
    for rx in _SECRET_PATTERNS:
        out = rx.sub("[redacted]", out)
    out = out.strip()
    if len(out) > SUMMARY_MAX_CHARS:
        out = out[:SUMMARY_MAX_CHARS] + "…"
    return out


def _default_state_path() -> Path:
    try:
        from hermes_constants import get_sinria_home

        return get_sinria_home() / "company_os" / "inbox_runs.json"
    except Exception:
        return Path.home() / ".sinria" / "company_os" / "inbox_runs.json"


def _load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _task_field(task: dict[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        value = task.get(name)
        if value:
            return value
    return default


def _build_run_input(task: dict[str, Any]) -> str:
    title = str(_task_field(task, "title"))
    instruction = str(_task_field(task, "instruction", "instruction_summary"))
    payload = task.get("payload") or {}
    parts = []
    if title:
        parts.append(f"タスク: {title}")
    parts.append(f"依頼内容: {instruction}")
    background = payload.get("background") or payload.get("背景")
    if background:
        parts.append(f"背景: {background}")
    desired = payload.get("desiredOutput") or payload.get("希望出力")
    if desired:
        parts.append(f"希望出力: {desired}")
    return "\n".join(parts)


@dataclass(frozen=True)
class ApprovedTaskScope:
    """Immutable task-level approval context sent to the gateway agent.

    Company OS currently records approval against a task, not against an
    independently structured list of operations. The safest precise scope is
    therefore the exact gateway input snapshot for that same task. Keeping this
    as a value object prevents a vague global "approved" flag from being
    interpreted as approval for newly invented work.
    """

    task_id: str
    gateway_input: str

    @classmethod
    def from_task(cls, task: dict[str, Any], gateway_input: str) -> "ApprovedTaskScope":
        return cls(
            task_id=str(_task_field(task, "task_id", "id")),
            gateway_input=gateway_input,
        )

    def gateway_instructions(self) -> str:
        snapshot = json.dumps(
            {"taskId": self.task_id, "gatewayInput": self.gateway_input},
            ensure_ascii=False,
            sort_keys=True,
        )
        return (
            "\n承認コンテキスト:\n"
            "- humanApprovalGranted=true。人間の承認は company-os の ReviewRequest として記録済みです。\n"
            f"- 承認対象の固定スナップショット: {snapshot}\n"
            "- 承認範囲は、上記の同一 task の gatewayInput に明記された操作だけです。"
            "単一レーンでも複数レーンでも、明記された各操作はすべて承認済みです。\n"
            "- 承認範囲内の操作について、外部副作用であることだけを理由に承認を再要求しないでください。"
            "実行して、完了時は `SINRIA_TASK_STATUS: completed` と報告してください。\n"
            "- 新規または依頼範囲外の操作、送信先・対象・金額・権限・データ範囲の追加、"
            "実行中に新たに判明した副作用は未承認です。推測で承認範囲を拡張せず、その操作を実行しないでください。"
            "必要な追加範囲を説明し、最終行を `SINRIA_TASK_STATUS: waiting_review` としてください。\n"
        )


def build_gateway_runs_runner(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    session: Any = None,
    state_path: Path | None = None,
    max_run_seconds: int | None = None,
) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    """Build an async-across-ticks inbox runner bound to the local gateway.

    The returned callable follows the inbox-request runner contract: it accepts
    ``(task, identity)`` and returns a dict whose ``status`` is one of
    ``in_progress`` / ``completed`` / ``failed_recoverable`` (plus
    ``reviewRequested`` when the agent self-reported an approval need).
    """
    resolved_url = (base_url or os.environ.get("SINRIA_LOCAL_API_URL") or DEFAULT_GATEWAY_URL).rstrip("/")
    resolved_key = api_key if api_key is not None else os.environ.get("SINRIA_LOCAL_API_KEY", "")
    resolved_state = state_path or _default_state_path()
    resolved_max = (
        max_run_seconds
        if max_run_seconds is not None
        else int(os.environ.get("SINRIA_INBOX_RUN_MAX_SECONDS", DEFAULT_MAX_RUN_SECONDS))
    )

    def _session() -> Any:
        nonlocal session
        if session is None:
            import requests

            session = requests.Session()
        return session

    def _headers() -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if resolved_key:
            headers["Authorization"] = f"Bearer {resolved_key}"
        return headers

    def _recoverable(summary: str) -> dict[str, Any]:
        return {"status": "failed_recoverable", "sanitizedSummary": sanitize_run_output(summary)}

    def _stop_run(run_id: str) -> None:
        try:
            _session().post(f"{resolved_url}/v1/runs/{run_id}/stop", headers=_headers(), json={})
        except Exception:
            logger.debug("best-effort stop for run %s failed", run_id)

    def _start(task: dict[str, Any], task_id: str, state: dict[str, Any]) -> dict[str, Any]:
        gateway_input = _build_run_input(task)
        preamble = SAFETY_PREAMBLE
        if (task.get("payload") or {}).get("humanApprovalGranted") is True:
            approval_scope = ApprovedTaskScope.from_task(task, gateway_input)
            preamble += approval_scope.gateway_instructions()
        body = {"input": gateway_input, "instructions": preamble}
        task_payload = task.get("payload") or {}
        source_platform = str(
            task_payload.get("sourcePlatform") or task_payload.get("source_platform") or ""
        ).strip().lower()
        if source_platform == "line":
            # Keep HTTP session binding non-delivering, while constructing the
            # shared AIAgent with LINE's configured tool surface and action policy.
            body["execution_platform"] = "line"
        try:
            response = _session().post(f"{resolved_url}/v1/runs", headers=_headers(), json=body)
            response.raise_for_status()
            run_id = str((response.json() or {}).get("run_id") or "")
        except Exception as exc:
            logger.warning("inbox runner could not reach the gateway: %s", type(exc).__name__)
            return _recoverable("gateway への接続に失敗したため次 tick で再試行します")
        if not run_id:
            return _recoverable("gateway が run_id を返しませんでした")
        state[task_id] = {"run_id": run_id, "started_at": time.time()}
        _save_state(resolved_state, state)
        return {"status": "in_progress", "runRef": run_id}

    def _poll(task_id: str, entry: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        run_id = str(entry.get("run_id") or "")
        started_at = float(entry.get("started_at") or 0)

        def _finish(result: dict[str, Any]) -> dict[str, Any]:
            state.pop(task_id, None)
            _save_state(resolved_state, state)
            return result

        if resolved_max >= 0 and time.time() - started_at > resolved_max:
            _stop_run(run_id)
            return _finish(_recoverable(f"実行が時間予算({resolved_max}s)を超過したため回収しました"))
        try:
            response = _session().get(f"{resolved_url}/v1/runs/{run_id}", headers=_headers())
        except Exception as exc:
            logger.warning("inbox runner poll failed: %s", type(exc).__name__)
            return {"status": "in_progress", "runRef": run_id}
        if getattr(response, "status_code", 200) == 404:
            # Gateway restarted and lost in-memory run state — retry from scratch.
            return _finish(_recoverable("gateway 再起動により run が失われたため再試行します"))
        try:
            payload = response.json() or {}
        except ValueError:
            return {"status": "in_progress", "runRef": run_id}
        status = str(payload.get("status") or "running")
        if status in ACTIVE_RUN_STATUSES:
            return {"status": "in_progress", "runRef": run_id}
        if status in REVIEW_REQUIRED_RUN_STATUSES:
            # A tool-level safety gate is a human-review outcome, not an
            # execution failure. Stop the local run before handing the task to
            # Company OS so an expired approval wait cannot continue orphaned.
            _stop_run(run_id)
            return _finish(
                {
                    "status": "completed",
                    "sanitizedSummary": "Sinria の安全ゲートで追加承認が必要です",
                    "runRef": run_id,
                    "reviewRequested": True,
                }
            )
        if status == "interrupted":
            # Durable-runtime gateway: a crash marks the run interrupted and the
            # journal auto-resumes it under a new run_id. Follow the resumed run
            # instead of failing; if auto-resume has not fired yet, keep waiting.
            resumed_id = str(payload.get("resumed_run_id") or "")
            if resumed_id:
                entry["run_id"] = resumed_id
                state[task_id] = entry
                _save_state(resolved_state, state)
                return {"status": "in_progress", "runRef": resumed_id}
            return {"status": "in_progress", "runRef": run_id}
        if status == "completed":
            output = str(payload.get("output") or "")
            result: dict[str, Any] = {
                "status": "completed",
                "sanitizedSummary": sanitize_run_output(output) or "実行完了（出力なし）",
                "runRef": run_id,
            }
            task_status = _last_task_status(output)
            if task_status == "waiting_review":
                result["reviewRequested"] = True
            elif task_status == "blocked":
                result["status"] = "failed_recoverable"
            return _finish(result)
        error = sanitize_run_output(str(payload.get("error") or status))
        return _finish(_recoverable(f"run が {status} で終了: {error}"))

    def run(task: dict[str, Any], identity: Any) -> dict[str, Any]:
        task_id = str(_task_field(task, "task_id", "id"))
        if not task_id:
            return _recoverable("task_id がありません")
        state = _load_state(resolved_state)
        entry = state.get(task_id)
        if entry:
            return _poll(task_id, entry, state)
        return _start(task, task_id, state)

    return run


def build_synthetic_inbox_runner() -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    """Offline runner for backend E2E smoke: NO gateway, NO model call.

    Returns a deterministic ``completed`` result for an approved inbox task while
    keeping the same safety envelope as the live runner (no external action, no
    raw local context / credential / clinical content in the sanitized summary).
    Only for synthetic verification; production dispatch uses
    :func:`build_gateway_runs_runner`.
    """

    def run(task: dict[str, Any], identity: Any) -> dict[str, Any]:
        del identity
        task_id = str(_task_field(task, "task_id", "id"))
        if not task_id:
            return {
                "status": "failed_recoverable",
                "sanitizedSummary": "task_id がありません",
                "externalActionPerformed": False,
                "rawLocalContextStored": False,
            }
        payload = task.get("payload") or {}
        policy = task.get("policy") or {}
        approval_required = bool(policy.get("humanApprovalRequired", True))
        approval_granted = payload.get("humanApprovalGranted") is True
        if approval_required and not approval_granted:
            return {
                "status": "waiting_review",
                "reviewRequested": True,
                "sanitizedSummary": "Human approval is required before local execution.",
                "externalActionPerformed": False,
                "rawLocalContextStored": False,
            }
        action = str(payload.get("action") or "inbox_reconcile")
        summary = sanitize_run_output(
            f"synthetic inbox execution completed for {action} (no external action, metadata only)"
        )
        return {
            "status": "completed",
            "sanitizedSummary": summary or "synthetic inbox execution completed",
            "externalActionPerformed": False,
            "rawLocalContextStored": False,
            "runRef": f"synthetic:{task_id}",
        }

    return run
