"""Gateway boundary for locally diagnosing user-reported Sinria errors."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from agent.defect_capture import record_external_defect
from agent.repair.intake import _load_config_best_effort, run_intake
from agent.repair.user_report import intake_user_report
from sinria_constants import get_sinria_home


_REPAIR_PREFIXES = (
    "/repair-report", "repair:", "fix this error", "diagnose this error",
    "エラー修正", "エラーを修正", "このエラーを修正", "原因を特定して修正",
)

_SAFE_DIAGNOSIS_PATTERNS = {
    "error_class": re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"),
    "timeout": re.compile(r"^[A-Za-z0-9 .:=_-]{1,80}$"),
    "location": re.compile(r"^[A-Za-z0-9_.-]{1,128}:[0-9]{1,9}$"),
}
_SAFE_REPORT_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def should_route_user_repair_report(text: str, *, has_image: bool) -> bool:
    normalized = (text or "").strip().lower()
    if any(normalized.startswith(prefix) for prefix in _REPAIR_PREFIXES):
        return True
    return has_image and ("修正して" in normalized or "原因を特定" in normalized)


def _local_image(event: Any) -> Path | None:
    for url, media_type in zip(
        getattr(event, "media_urls", ()) or (), getattr(event, "media_types", ()) or ()
    ):
        if not str(media_type).startswith("image/"):
            continue
        value = str(url)
        if value.startswith(("http://", "https://")):
            continue
        path = Path(value).expanduser()
        if path.is_file():
            return path
    return None


def process_gateway_repair_report(
    event: Any, *, home: str | Path | None = None, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Persist raw evidence locally, record sanitized telemetry, and queue intake."""
    root = Path(home or get_sinria_home()).expanduser().resolve()
    args = str(event.get_command_args() or "").strip()
    image = _local_image(event)

    def write_defect(safe: dict[str, Any]) -> None:
        diagnosis = safe.get("diagnosis") or {}
        error_class = diagnosis.get("error_class") or "UserReportedFailure"
        status = diagnosis.get("status")
        structural_message = f"user-reported {error_class}"
        if status is not None:
            structural_message += f" status={status}"
        record_external_defect(
            repo="sinria",
            exc_class=error_class,
            message=structural_message,
            code_location=diagnosis.get("location") or "user_report:0",
            func_name="gateway_user_report",
            severity="high",
            session_kind="user_report",
            path=root / "repair" / "code_defects.jsonl",
        )

    result = intake_user_report(
        args or None, screenshot_path=image, home=root, defect_writer=write_defect
    )
    resolved_config = config if config is not None else _load_config_best_effort()
    intake = run_intake(config=resolved_config, home=root)
    intake.setdefault("status", "enabled" if intake.get("enabled") else "disabled")
    result["intake"] = intake
    return result


def format_gateway_repair_response(result: dict[str, Any]) -> str:
    diagnosis = result.get("diagnosis") or {}
    parts = [f"エラー報告 `{result.get('report_id', 'unknown')}` をローカル保存しました。"]
    if diagnosis.get("error_class"):
        parts.append(f"原因候補: `{diagnosis['error_class']}`")
    if diagnosis.get("status"):
        parts.append(f"HTTP状態: `{diagnosis['status']}`")
    if diagnosis.get("location"):
        parts.append(f"発生箇所: `{diagnosis['location']}`")
    intake = result.get("intake") or {}
    created = intake.get("created") or []
    if created:
        parts.append("隔離修正チケットを作成しました。修正・テスト後もPR/反映は人間レビュー待ちです。")
    elif intake.get("status") == "disabled":
        parts.append("自己修復は無効のため、診断記録のみ作成しました。")
    else:
        parts.append("診断記録を自己修復キューへ渡しました。")
    parts.append("スクショ本文・貼付ログは外部へ送信していません。")
    return "\n".join(parts)


def build_gateway_repair_continuation(result: dict[str, Any]) -> str:
    """Build a confidentiality-safe prompt that continues the requested fix.

    Raw text, OCR output, screenshots, and the sanitized excerpt stay behind the
    local repair boundary. Only bounded deterministic diagnosis fields are
    forwarded to the normal agent loop.
    """
    diagnosis = result.get("diagnosis") or {}
    safe_fields = []
    for key in ("error_class", "timeout", "location"):
        value = str(diagnosis.get(key) or "")
        if _SAFE_DIAGNOSIS_PATTERNS[key].fullmatch(value):
            safe_fields.append(f"- {key}: {value}")
    status = diagnosis.get("status")
    if isinstance(status, int) and 100 <= status <= 599:
        safe_fields.append(f"- status: {status}")
    diagnosis_text = "\n".join(safe_fields) or "- deterministic diagnosis: unavailable"
    raw_report_id = str(result.get("report_id") or "")
    report_id = raw_report_id if _SAFE_REPORT_ID.fullmatch(raw_report_id) else "unknown"
    return (
        "The user asked Sinria to diagnose and fix the attached error. The raw "
        "screenshot, pasted logs, OCR text, and sanitized excerpt were confined "
        "locally and must not be requested from or sent to an external service.\n\n"
        f"Local repair report: {report_id}\n"
        f"Safe deterministic diagnosis:\n{diagnosis_text}\n\n"
        "Continue the actual requested work now: inspect the relevant local code "
        "and logs, identify the root cause, implement the safe fix, run targeted "
        "tests, and verify the real workflow. Saving this report or queueing a "
        "repair record is supplementary and is not task completion. Ask the user "
        "only if a required approval gate or genuinely unrecoverable ambiguity "
        "prevents execution."
    )


def apply_gateway_repair_continuation(
    event: Any,
    prompt: str,
    *,
    text_message_type: Any = None,
) -> None:
    """Replace a repair command with its safe text-only continuation in place."""
    event.text = prompt
    event.media_urls = []
    event.media_types = []
    event.reply_to_text = None
    event.channel_context = None
    if text_message_type is not None:
        event.message_type = text_message_type
