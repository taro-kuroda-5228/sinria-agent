"""Conservative per-turn tool-schema selection for Sinria.

The selector is deterministic and ephemeral: raw user text is classified in
memory and never returned or persisted. Shadow mode is the default; active
filtering requires an explicit approval bit and falls back to the full schema
set whenever the task class is uncertain.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Sequence


_VALID_MODES = {"off", "shadow", "active"}
_CONTROL_TOOLS = {
    "clarify",
    "todo",
    "memory",
    "recall_context",
    "session_search",
    "skill_view",
    "skills_list",
    "process",
}

_TASK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "development": (
        "code", "python", "javascript", "typescript", "repo", "repository",
        "file", "test", "build", "debug", "bug", "git", "implement", "edit",
        "コード", "実装", "修正", "テスト", "ビルド", "デバッグ", "バグ",
        "ファイル", "リポジトリ",
    ),
    "web": (
        "latest", "news", "weather", "web", "search", "research",
        "website", "current", "最新", "ニュース", "天気", "検索", "調査",
        "ウェブ",
    ),
    "browser": (
        "browser", "login", "log in", "page", "click", "form", "captcha",
        "ブラウザ", "ログイン", "ページ", "クリック", "フォーム",
    ),
    "messaging": (
        "send message", "send email", "email", "discord", "telegram", "slack",
        "メール", "メッセージを送", "送信して", "discord", "telegram",
    ),
    "scheduling": (
        "schedule", "cron", "reminder", "every day", "every week",
        "スケジュール", "定期", "毎日", "毎週", "リマインド",
    ),
    "media": (
        "image", "video", "audio", "voice", "photo", "diagram",
        "画像", "動画", "音声", "写真", "図解",
    ),
    "healthcare": (
        "patient", "clinical", "ehr", "emr", "medevidence", "medical",
        "患者", "臨床", "医療", "カルテ", "メドエビデンス",
    ),
    "ml": (
        "train model", "fine-tun", "lora", "dataset", "inference", "gpu",
        "学習", "ファインチューニング", "データセット", "推論",
    ),
    "smart_home": (
        "home assistant", "light", "thermostat", "hue", "スマートホーム", "照明",
    ),
    "content": (
        "rewrite", "proofread", "draft", "article", "copyedit", "edit this text",
        "推敲", "文章", "下書き", "記事", "校正",
    ),
}

_CATEGORY_TOOL_NAMES: dict[str, set[str]] = {
    "development": {
        "terminal", "process", "read_file", "search_files", "patch", "write_file",
        "execute_code", "delegate_task", "ml_training",
    },
    "web": {
        "web_search", "web_extract", "x_search", "xurl", "maps",
    },
    "browser": set(),
    "messaging": {
        "send_message", "himalaya", "imessage", "discord", "discord_admin",
    },
    "scheduling": {"cronjob", "todo", "apple_reminders"},
    "media": {
        "image_generate", "vision_analyze", "text_to_speech", "video_generate",
        "video_edit", "spotify",
    },
    "healthcare": {
        "sinria_integrations", "sinria_hybrid_bridge", "content_os_material",
        "content_os_package", "content_os_approval", "content_os_schedule",
        "content_os_publish", "content_os_metrics", "content_os_ops",
        "content_os_voice", "content_os_media",
    },
    "ml": {"ml_training"},
    "smart_home": {"homeassistant", "openhue"},
    "content": {"write_file", "read_file"},
}

_CATEGORY_PREFIXES: dict[str, tuple[str, ...]] = {
    "development": ("git", "github_", "code_"),
    "web": ("web_", "x_search"),
    "browser": ("browser_",),
    "messaging": ("send_", "discord", "slack", "telegram", "email"),
    "scheduling": ("cron", "reminder"),
    "media": ("image_", "video_", "vision_", "audio_", "tts"),
    "healthcare": ("sinria_integr", "content_os_"),
    "ml": ("ml_",),
    "smart_home": ("homeassistant", "hue", "openhue"),
    "content": (),
}


def _tool_name(schema: Any) -> str:
    if not isinstance(schema, Mapping):
        return ""
    function = schema.get("function")
    if isinstance(function, Mapping) and isinstance(function.get("name"), str):
        return function["name"]
    name = schema.get("name")
    return name if isinstance(name, str) else ""


def _structure_chars(value: Any) -> int:
    if value is None:
        return 4
    if isinstance(value, bool):
        return 4 if value else 5
    if isinstance(value, (int, float)):
        return len(str(value))
    if isinstance(value, str):
        return len(value) + 2
    if isinstance(value, Mapping):
        total = 2
        for index, (key, item) in enumerate(value.items()):
            if index:
                total += 1
            total += _structure_chars(str(key)) + 1 + _structure_chars(item)
        return total
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        total = 2
        for index, item in enumerate(value):
            if index:
                total += 1
            total += _structure_chars(item)
        return total
    return len(str(value)) + 2


def _keyword_matches(text: str, keyword: str) -> bool:
    folded = keyword.casefold()
    if not folded:
        return False
    if folded.isascii():
        pattern = rf"(?<![a-z0-9_]){re.escape(folded)}(?![a-z0-9_])"
        return re.search(pattern, text) is not None
    return folded in text


def _task_classes(user_message: str) -> tuple[str, ...]:
    text = (user_message or "").casefold()
    return tuple(
        category
        for category, keywords in _TASK_KEYWORDS.items()
        if any(_keyword_matches(text, keyword) for keyword in keywords)
    )


def _historical_tool_names(messages: Iterable[Any]) -> set[str]:
    names: set[str] = set()
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        direct_name = message.get("name")
        if isinstance(direct_name, str):
            names.add(direct_name)
        calls = message.get("tool_calls")
        if isinstance(calls, Sequence) and not isinstance(calls, (str, bytes, bytearray)):
            for call in calls:
                if not isinstance(call, Mapping):
                    continue
                function = call.get("function")
                if isinstance(function, Mapping) and isinstance(function.get("name"), str):
                    names.add(function["name"])
    return names


def _matches_category(name: str, category: str) -> bool:
    if name in _CATEGORY_TOOL_NAMES.get(category, set()):
        return True
    return any(name.startswith(prefix) for prefix in _CATEGORY_PREFIXES.get(category, ()))


@dataclass(frozen=True)
class DynamicToolSelectionConfig:
    mode: str = "shadow"
    active_approved: bool = False
    quality_gate_passed: bool = False

    @classmethod
    def from_mapping(cls, value: Any) -> "DynamicToolSelectionConfig":
        if not isinstance(value, Mapping):
            return cls()
        mode = str(value.get("mode", "shadow")).strip().lower()
        if mode not in _VALID_MODES:
            mode = "off"
        return cls(
            mode=mode,
            active_approved=value.get("active_approved") is True,
            quality_gate_passed=value.get("quality_gate_passed") is True,
        )


@dataclass(frozen=True)
class ToolSelectionDecision:
    request_tools: tuple[Any, ...]
    recommended_tools: tuple[Any, ...]
    requested_mode: str
    effective_mode: str
    applied: bool
    task_classes: tuple[str, ...]
    reason_codes: tuple[str, ...]
    fallback_reason: str
    original_count: int
    selected_count: int
    schema_chars_before: int
    estimated_schema_chars_after: int

    def to_metadata(self) -> dict[str, Any]:
        return {
            "requested_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
            "applied": self.applied,
            "task_classes": list(self.task_classes),
            "reason_codes": list(self.reason_codes),
            "fallback_reason": self.fallback_reason,
            "original_count": self.original_count,
            "selected_count": self.selected_count,
            "schema_chars_before": self.schema_chars_before,
            "schema_chars_after": self.estimated_schema_chars_after,
        }


def validate_active_selection(
    original_tools: Sequence[Any],
    selected_tools: Sequence[Any],
) -> tuple[bool, tuple[str, ...]]:
    """Verify every available safety/control tool remains in an active request."""
    original_names = {_tool_name(tool) for tool in original_tools}
    selected_names = {_tool_name(tool) for tool in selected_tools}
    required = original_names & _CONTROL_TOOLS
    missing = tuple(sorted(required - selected_names))
    return not missing, missing


def select_dynamic_tools(
    tools: Sequence[Any] | None,
    *,
    user_message: str,
    messages: Iterable[Any],
    config: DynamicToolSelectionConfig,
) -> ToolSelectionDecision:
    original = tuple(tools or ())
    before_chars = _structure_chars(original)
    requested_mode = config.mode if config.mode in _VALID_MODES else "off"
    if requested_mode == "off" or not original:
        return ToolSelectionDecision(
            request_tools=original,
            recommended_tools=original,
            requested_mode=requested_mode,
            effective_mode="off",
            applied=False,
            task_classes=(),
            reason_codes=("selection_off",),
            fallback_reason="selection_off",
            original_count=len(original),
            selected_count=len(original),
            schema_chars_before=before_chars,
            estimated_schema_chars_after=before_chars,
        )

    classes = _task_classes(user_message)
    schemas_by_name = {_tool_name(schema): schema for schema in original if _tool_name(schema)}
    selected_names = set(schemas_by_name) & _CONTROL_TOOLS
    reasons = [f"task:{category}" for category in classes]
    for category in classes:
        selected_names.update(
            name for name in schemas_by_name if _matches_category(name, category)
        )

    continuity = _historical_tool_names(messages) & set(schemas_by_name)
    if continuity:
        selected_names.update(continuity)
        reasons.append("continuity")

    recommended = tuple(
        schema for schema in original if _tool_name(schema) in selected_names
    )
    after_chars = _structure_chars(recommended)
    fallback_reason = ""
    if not classes:
        fallback_reason = "no_confident_task_class"
        recommended = original
        after_chars = before_chars
    elif not recommended:
        fallback_reason = "no_matching_tools"
    elif len(recommended) >= len(original) or after_chars >= before_chars:
        fallback_reason = "no_schema_reduction"

    safety_ok, _missing_safety_tools = validate_active_selection(original, recommended)
    if not safety_ok:
        fallback_reason = "safety_required_tool_missing"
        recommended = original
        after_chars = before_chars
        reasons.append("safety_gate")

    if requested_mode == "active" and not config.active_approved:
        effective_mode = "shadow"
        fallback_reason = "active_not_approved"
    elif requested_mode == "active" and not config.quality_gate_passed:
        effective_mode = "shadow"
        fallback_reason = "quality_gate_not_passed"
    else:
        effective_mode = requested_mode

    can_apply = (
        effective_mode == "active"
        and not fallback_reason
        and bool(recommended)
        and len(recommended) < len(original)
    )
    request_tools = recommended if can_apply else original
    return ToolSelectionDecision(
        request_tools=request_tools,
        recommended_tools=recommended,
        requested_mode=requested_mode,
        effective_mode=effective_mode,
        applied=can_apply,
        task_classes=classes,
        reason_codes=tuple(reasons),
        fallback_reason=fallback_reason,
        original_count=len(original),
        selected_count=len(recommended),
        schema_chars_before=before_chars,
        estimated_schema_chars_after=after_chars,
    )


def _merge_observation(agent: Any, decision: ToolSelectionDecision) -> None:
    observation = getattr(agent, "_turn_tool_selection_observation", None)
    if not isinstance(observation, dict):
        observation = {}
        setattr(agent, "_turn_tool_selection_observation", observation)
    defaults = {
        "request_count": 0,
        "applied_request_count": 0,
        "original_tool_count_total": 0,
        "selected_tool_count_total": 0,
        "schema_chars_before": 0,
        "schema_chars_after": 0,
        "mode": decision.effective_mode,
        "task_classes": [],
        "reason_codes": [],
        "fallback_reasons": [],
    }
    for key, value in defaults.items():
        observation.setdefault(key, value)
    observation["request_count"] += 1
    observation["applied_request_count"] += int(decision.applied)
    observation["original_tool_count_total"] += decision.original_count
    observation["selected_tool_count_total"] += decision.selected_count
    observation["schema_chars_before"] += decision.schema_chars_before
    observation["schema_chars_after"] += decision.estimated_schema_chars_after
    observation["mode"] = decision.effective_mode
    for key, values in (
        ("task_classes", decision.task_classes),
        ("reason_codes", decision.reason_codes),
        ("fallback_reasons", (decision.fallback_reason,) if decision.fallback_reason else ()),
    ):
        current = observation[key]
        for value in values:
            if value not in current:
                current.append(value)
        current.sort()


def _tool_choice_name(tool_choice: Any) -> str:
    if isinstance(tool_choice, str):
        normalized = tool_choice.strip()
        if normalized.casefold() not in {"", "auto", "none", "required", "any"}:
            return normalized
        return ""
    if not isinstance(tool_choice, Mapping):
        return ""
    function = tool_choice.get("function")
    if isinstance(function, Mapping) and isinstance(function.get("name"), str):
        return function["name"]
    name = tool_choice.get("name")
    return name if isinstance(name, str) else ""


def apply_dynamic_tool_selection(
    agent: Any,
    api_kwargs: dict[str, Any],
    *,
    user_message: str,
    messages: Iterable[Any],
) -> dict[str, Any]:
    config = getattr(agent, "_dynamic_tool_selection_config", None)
    if not isinstance(config, DynamicToolSelectionConfig):
        config = DynamicToolSelectionConfig(mode="off")
    tools = api_kwargs.get("tools")
    selection_messages = list(messages)
    forced_tool = _tool_choice_name(api_kwargs.get("tool_choice"))
    if forced_tool:
        selection_messages.append({"role": "assistant", "name": forced_tool})
    decision = select_dynamic_tools(
        tools if isinstance(tools, Sequence) else (),
        user_message=user_message,
        messages=selection_messages,
        config=config,
    )
    _merge_observation(agent, decision)
    if decision.applied:
        api_kwargs["tools"] = list(decision.request_tools)
    return api_kwargs


__all__ = [
    "DynamicToolSelectionConfig",
    "ToolSelectionDecision",
    "apply_dynamic_tool_selection",
    "select_dynamic_tools",
    "validate_active_selection",
]
