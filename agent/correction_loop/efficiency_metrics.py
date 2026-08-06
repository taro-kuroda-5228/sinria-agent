"""Local, metadata-only efficiency telemetry for Sinria agent turns.

The ledger deliberately stores counts, categories, and pseudonymous references
only. Raw prompts, responses, tool arguments, and tool results never cross this
boundary. The resulting records can therefore be joined to the existing
Goal→Actual→Gap outcome ledger without creating a second conversation log.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sinria_constants import get_sinria_home

from agent.privacy.sanitization import assert_sanitized_text

TURN_EFFICIENCY_RELATIVE_PATH = Path("corrections") / "efficiency_turns.jsonl"
_SCHEMA_VERSION = 3
_SAFE_METADATA_TOKEN = re.compile(r"^[A-Za-z0-9_.:/=()+-]{0,128}$")


def _counter(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _safe_delta(current: int, previous: int) -> int:
    """Return a non-negative delta, tolerating provider/fallback counter resets."""

    current = _counter(current)
    previous = _counter(previous)
    return current - previous if current >= previous else current


@dataclass(frozen=True)
class UsageSnapshot:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    api_calls: int = 0

    @classmethod
    def from_agent(cls, agent: Any) -> "UsageSnapshot":
        return cls(
            input_tokens=_counter(getattr(agent, "session_input_tokens", 0)),
            output_tokens=_counter(getattr(agent, "session_output_tokens", 0)),
            reasoning_tokens=_counter(getattr(agent, "session_reasoning_tokens", 0)),
            cache_read_tokens=_counter(getattr(agent, "session_cache_read_tokens", 0)),
            cache_write_tokens=_counter(getattr(agent, "session_cache_write_tokens", 0)),
            api_calls=_counter(getattr(agent, "session_api_calls", 0)),
        )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def delta_from(self, previous: "UsageSnapshot") -> "UsageSnapshot":
        return UsageSnapshot(
            input_tokens=_safe_delta(self.input_tokens, previous.input_tokens),
            output_tokens=_safe_delta(self.output_tokens, previous.output_tokens),
            reasoning_tokens=_safe_delta(self.reasoning_tokens, previous.reasoning_tokens),
            cache_read_tokens=_safe_delta(self.cache_read_tokens, previous.cache_read_tokens),
            cache_write_tokens=_safe_delta(self.cache_write_tokens, previous.cache_write_tokens),
            api_calls=_safe_delta(self.api_calls, previous.api_calls),
        )


def _wire_chars(value: Any) -> int:
    """Approximate JSON wire chars without materializing request content."""

    if value is None:
        return 4
    if isinstance(value, bool):
        return 4 if value else 5
    if isinstance(value, str):
        return len(value) + 2
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, (int, float)):
        return len(str(value))
    if isinstance(value, dict):
        if not value:
            return 2
        item_chars = sum(
            _wire_chars(str(key)) + 1 + _wire_chars(item)
            for key, item in value.items()
        )
        return 2 + item_chars + len(value) - 1
    if isinstance(value, (list, tuple)):
        if not value:
            return 2
        return 2 + sum(_wire_chars(item) for item in value) + len(value) - 1
    return len(str(value))


def _content_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, dict):
        return sum(_content_chars(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_content_chars(item) for item in value)
    return 0


@dataclass
class RequestFootprint:
    """Aggregate request sizes for one turn; request content is never retained."""

    request_count: int = 0
    system_chars: int = 0
    history_chars: int = 0
    tool_result_chars: int = 0
    tool_schema_chars: int = 0
    total_chars: int = 0
    max_total_chars: int = 0
    api_duration_ms: int = 0
    wall_duration_ms: int = 0

    def observe(self, api_kwargs: dict[str, Any] | None) -> None:
        if not isinstance(api_kwargs, dict):
            return
        system_chars = 0
        history_chars = 0
        tool_result_chars = 0
        messages = api_kwargs.get("messages")
        if messages is None:
            messages = api_kwargs.get("input")
        if not isinstance(messages, list):
            messages = []
        for message in messages:
            if not isinstance(message, dict):
                history_chars += _content_chars(message)
                continue
            role = str(message.get("role") or "")
            content = message.get("content")
            if content is None and "output" in message:
                content = message.get("output")
            chars = _content_chars(content)
            if role in {"system", "developer"}:
                system_chars += chars
            elif role == "tool" or message.get("type") in {
                "function_call_output",
                "computer_call_output",
            }:
                tool_result_chars += chars
            else:
                history_chars += chars
        system_chars += _content_chars(api_kwargs.get("instructions"))
        tool_schema_chars = _wire_chars(api_kwargs.get("tools") or [])
        total_chars = _wire_chars(api_kwargs)

        self.request_count += 1
        self.system_chars += system_chars
        self.history_chars += history_chars
        self.tool_result_chars += tool_result_chars
        self.tool_schema_chars += tool_schema_chars
        self.total_chars += total_chars
        self.max_total_chars = max(self.max_total_chars, total_chars)

    def observe_api_duration(self, seconds: Any) -> None:
        """Add one provider-call duration without retaining request content."""

        try:
            milliseconds = max(0, round(float(seconds) * 1000))
        except (TypeError, ValueError, OverflowError):
            return
        self.api_duration_ms += milliseconds

    def to_dict(self) -> dict[str, int]:
        return {
            "request_count": self.request_count,
            "system_chars": self.system_chars,
            "history_chars": self.history_chars,
            "tool_result_chars": self.tool_result_chars,
            "tool_schema_chars": self.tool_schema_chars,
            "total_chars": self.total_chars,
            "max_total_chars": self.max_total_chars,
            "api_duration_ms": self.api_duration_ms,
            "wall_duration_ms": self.wall_duration_ms,
        }


@dataclass(frozen=True)
class TurnEfficiencyRecord:
    record_id: str
    timestamp: str
    session_ref: str
    outcome_record_id: str
    platform: str
    model: str
    provider: str
    policy_variant: str
    goal_kind: str
    actual_kind: str
    turn_exit_reason: str
    completed: bool
    verified_completion: bool
    gap_detected: bool
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int
    request_count: int
    retry_count: int
    tool_turn_count: int
    tool_call_count: int
    tool_error_count: int
    system_chars: int
    history_chars: int
    tool_result_chars: int
    tool_schema_chars: int
    request_chars: int
    max_request_chars: int
    schema_version: int = _SCHEMA_VERSION
    failure_signature: str = ""
    interventions: tuple[str, ...] = ()
    tool_selection_mode: str = "off"
    tool_selection_applied_requests: int = 0
    tool_selection_original_count: int = 0
    tool_selection_selected_count: int = 0
    tool_selection_schema_chars_before: int = 0
    tool_selection_schema_chars_after: int = 0
    tool_selection_task_classes: tuple[str, ...] = ()
    tool_selection_fallback_reasons: tuple[str, ...] = ()
    raw_context_stored: bool = False
    workload_ref: str = ""
    api_duration_ms: int = 0
    wall_duration_ms: int = 0

    def __post_init__(self) -> None:
        try:
            parsed_timestamp = datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("timestamp must be ISO-8601") from exc
        if parsed_timestamp.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        for field_name in (
            "record_id",
            "session_ref",
            "outcome_record_id",
            "platform",
            "model",
            "provider",
            "policy_variant",
            "goal_kind",
            "actual_kind",
            "turn_exit_reason",
            "failure_signature",
            "tool_selection_mode",
            "workload_ref",
        ):
            value = str(getattr(self, field_name))
            assert_sanitized_text(value, field=field_name)
            if not _SAFE_METADATA_TOKEN.fullmatch(value):
                raise ValueError(f"{field_name} must be a bounded metadata token")
        for collection_name in (
            "interventions",
            "tool_selection_task_classes",
            "tool_selection_fallback_reasons",
        ):
            for item in getattr(self, collection_name):
                value = str(item)
                assert_sanitized_text(value, field=collection_name)
                if not _SAFE_METADATA_TOKEN.fullmatch(value):
                    raise ValueError(
                        f"{collection_name} must contain bounded metadata tokens"
                    )
        if self.raw_context_stored:
            raise ValueError("efficiency telemetry must never store raw context")
        if self.schema_version < 1:
            raise ValueError("schema_version must be positive")
        for field_name in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "total_tokens",
            "request_count",
            "retry_count",
            "tool_turn_count",
            "tool_call_count",
            "tool_error_count",
            "system_chars",
            "history_chars",
            "tool_result_chars",
            "tool_schema_chars",
            "request_chars",
            "max_request_chars",
            "tool_selection_applied_requests",
            "tool_selection_original_count",
            "tool_selection_selected_count",
            "tool_selection_schema_chars_before",
            "tool_selection_schema_chars_after",
            "api_duration_ms",
            "wall_duration_ms",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TurnEfficiencyRecord":
        known = {item.name for item in fields(cls)}
        payload = {key: value for key, value in data.items() if key in known}
        for tuple_field in (
            "interventions",
            "tool_selection_task_classes",
            "tool_selection_fallback_reasons",
        ):
            if isinstance(payload.get(tuple_field), list):
                payload[tuple_field] = tuple(
                    str(item) for item in payload[tuple_field]
                )
        return cls(**payload)


def _letters_digest(value: str, *, length: int = 24) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
    return digest.translate(str.maketrans("0123456789", "abcdefghij"))


def _session_ref(session_id: Any) -> str:
    return f"session:{_letters_digest(str(session_id or 'unknown'))}"


def _current_turn_messages(messages: list[Any], source_user_message: str | None) -> list[Any]:
    start: int | None = None
    if source_user_message is not None:
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if (
                isinstance(message, dict)
                and message.get("role") == "user"
                and message.get("content") == source_user_message
            ):
                start = index
                break
        if start is None:
            return []
    else:
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if isinstance(message, dict) and message.get("role") == "user":
                start = index
                break
    return messages[start or 0 :]


def _tool_counts(messages: list[Any], source_user_message: str | None) -> tuple[int, int, int]:
    scoped = _current_turn_messages(messages, source_user_message)
    tool_turn_count = 0
    tool_call_count = 0
    tool_error_count = 0
    try:
        from agent.tool_guardrails import classify_tool_failure
    except Exception:  # pragma: no cover - dependency is present in normal runtime
        classify_tool_failure = None

    for message in scoped:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant" and isinstance(message.get("tool_calls"), list):
            calls = message["tool_calls"]
            if calls:
                tool_turn_count += 1
                tool_call_count += len(calls)
        if message.get("role") != "tool" or classify_tool_failure is None:
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            failed, _reason = classify_tool_failure(str(message.get("name") or "unknown"), content)
        except Exception:
            failed = False
        if failed:
            tool_error_count += 1
    return tool_turn_count, tool_call_count, tool_error_count


def _safe_category(value: Any, *, default: str = "unknown", limit: int = 96) -> str:
    text = str(value or default).strip()[:limit]
    try:
        assert_sanitized_text(text, field="category")
    except ValueError:
        return default
    if not _SAFE_METADATA_TOKEN.fullmatch(text):
        return default
    return text or default


def build_turn_efficiency_record(
    *,
    agent: Any,
    usage_before: UsageSnapshot,
    request_footprint: RequestFootprint,
    result: dict[str, Any] | None,
    outcome_record_id: str = "",
    goal_kind: str = "unknown",
    actual_kind: str = "unknown",
    gap_detected: bool = False,
    failure_signature: str = "",
    turn_exit_reason: str = "unknown",
    source_user_message: str | None = None,
    timestamp: str | None = None,
    policy_variant: str = "baseline",
    interventions: tuple[str, ...] = (),
) -> TurnEfficiencyRecord:
    result = result if isinstance(result, dict) else {}
    raw_messages = result.get("messages")
    messages: list[Any] = raw_messages if isinstance(raw_messages, list) else []
    tool_turn_count, tool_call_count, tool_error_count = _tool_counts(
        messages, source_user_message
    )
    usage = UsageSnapshot.from_agent(agent).delta_from(usage_before)
    footprint = request_footprint.to_dict()
    request_count = footprint["request_count"] or _counter(result.get("api_calls")) or usage.api_calls
    retry_count = max(0, request_count - tool_turn_count - 1)
    ts = timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    session_ref = _session_ref(getattr(agent, "session_id", ""))
    id_material = "|".join((session_ref, ts, str(outcome_record_id or ""), str(request_count)))
    record_id = "eff-" + _letters_digest(id_material)
    completed = bool(result.get("completed", False))
    verified_completion = actual_kind == "verified_practical_completion"
    selection = getattr(agent, "_turn_tool_selection_observation", {})
    if not isinstance(selection, dict):
        selection = {}
    selection_classes = selection.get("task_classes", ())
    if not isinstance(selection_classes, (list, tuple)):
        selection_classes = ()
    selection_fallbacks = selection.get("fallback_reasons", ())
    if not isinstance(selection_fallbacks, (list, tuple)):
        selection_fallbacks = ()

    return TurnEfficiencyRecord(
        record_id=record_id,
        timestamp=ts,
        session_ref=session_ref,
        outcome_record_id=_safe_category(outcome_record_id, default=""),
        platform=_safe_category(getattr(agent, "platform", "unknown")),
        model=_safe_category(getattr(agent, "model", "unknown")),
        provider=_safe_category(getattr(agent, "provider", "unknown")),
        policy_variant=_safe_category(policy_variant, default="baseline"),
        goal_kind=_safe_category(goal_kind),
        actual_kind=_safe_category(actual_kind),
        turn_exit_reason=_safe_category(turn_exit_reason),
        completed=completed,
        verified_completion=verified_completion,
        gap_detected=bool(gap_detected),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        total_tokens=usage.total_tokens,
        request_count=request_count,
        retry_count=retry_count,
        tool_turn_count=tool_turn_count,
        tool_call_count=tool_call_count,
        tool_error_count=tool_error_count,
        system_chars=footprint["system_chars"],
        history_chars=footprint["history_chars"],
        tool_result_chars=footprint["tool_result_chars"],
        tool_schema_chars=footprint["tool_schema_chars"],
        request_chars=footprint["total_chars"],
        max_request_chars=footprint["max_total_chars"],
        failure_signature=_safe_category(failure_signature, default=""),
        interventions=tuple(_safe_category(item) for item in interventions),
        tool_selection_mode=_safe_category(selection.get("mode"), default="off"),
        tool_selection_applied_requests=_counter(selection.get("applied_request_count")),
        tool_selection_original_count=_counter(selection.get("original_tool_count_total")),
        tool_selection_selected_count=_counter(selection.get("selected_tool_count_total")),
        tool_selection_schema_chars_before=_counter(selection.get("schema_chars_before")),
        tool_selection_schema_chars_after=_counter(selection.get("schema_chars_after")),
        tool_selection_task_classes=tuple(
            _safe_category(item) for item in selection_classes
        ),
        tool_selection_fallback_reasons=tuple(
            _safe_category(item) for item in selection_fallbacks
        ),
        workload_ref=_safe_category(
            getattr(agent, "_efficiency_workload_ref", ""), default=""
        ),
        api_duration_ms=_counter(footprint.get("api_duration_ms")),
        wall_duration_ms=_counter(footprint.get("wall_duration_ms")),
    )


def turn_efficiency_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / TURN_EFFICIENCY_RELATIVE_PATH


@contextmanager
def _ledger_lock(target: Path):
    lock_path = target.with_name(target.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(lock_path.parent, 0o700)
    except OSError:
        pass
    with lock_path.open("a+b") as handle:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        if os.name == "nt":  # pragma: no cover - Windows CI
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_turn_efficiency_record(
    record: TurnEfficiencyRecord, *, path: Path | None = None
) -> Path:
    target = path or turn_efficiency_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    payload = json.dumps(asdict(record), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with _ledger_lock(target):
        descriptor = os.open(target, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, (payload + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    return target


def load_turn_efficiency_records(*, path: Path | None = None) -> list[TurnEfficiencyRecord]:
    target = path or turn_efficiency_path()
    if not target.exists():
        return []
    records: list[TurnEfficiencyRecord] = []
    with _ledger_lock(target):
        lines = target.read_text(encoding="utf-8").splitlines()
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise TypeError("row is not a JSON object")
            records.append(TurnEfficiencyRecord.from_dict(payload))
        except Exception as exc:
            raise ValueError(f"invalid efficiency ledger row {line_no}: {exc}") from exc
    return records


__all__ = [
    "RequestFootprint",
    "TurnEfficiencyRecord",
    "UsageSnapshot",
    "append_turn_efficiency_record",
    "build_turn_efficiency_record",
    "load_turn_efficiency_records",
    "turn_efficiency_path",
]
