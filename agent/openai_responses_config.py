"""Config helpers for OpenAI Responses API request defaults.

These helpers intentionally return request overrides only; they do not perform
provider routing or credential work.  The caller decides whether the active
runtime is a Responses-capable provider.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "none", ""}:
        return False
    return default


def build_openai_responses_request_overrides(config: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Build safe Responses API request overrides from config.yaml.

    Supported config shape::

        openai_responses:
          text_verbosity: high          # low|medium|high
          reasoning_context: auto       # passed as reasoning.context
          reasoning_mode: pro           # passed as reasoning.mode
          programmatic_tool_calling:
            default_enabled: true       # adds the hosted JS coordinator tool
            mark_all_tools_eligible: true  # adds allowed_callers to Sinria tools

    Unknown keys are ignored.  Validation is conservative so a typo does not
    silently add arbitrary API fields.
    """
    if not isinstance(config, Mapping):
        return {}
    cfg = config.get("openai_responses") or {}
    if not isinstance(cfg, Mapping):
        return {}

    overrides: Dict[str, Any] = {}

    verbosity = cfg.get("text_verbosity")
    if isinstance(verbosity, str) and verbosity.strip().lower() in {"low", "medium", "high"}:
        overrides["text"] = {"verbosity": verbosity.strip().lower()}

    reasoning: Dict[str, Any] = {}
    reasoning_context = cfg.get("reasoning_context")
    if isinstance(reasoning_context, str) and reasoning_context.strip():
        reasoning["context"] = reasoning_context.strip().lower()
    reasoning_mode = cfg.get("reasoning_mode")
    if isinstance(reasoning_mode, str) and reasoning_mode.strip().lower() == "pro":
        reasoning["mode"] = "pro"
    if reasoning:
        overrides["reasoning"] = reasoning

    ptc = cfg.get("programmatic_tool_calling") or {}
    if isinstance(ptc, Mapping):
        if _truthy(ptc.get("default_enabled"), default=False):
            overrides["programmatic_tool_calling"] = True
        if _truthy(ptc.get("mark_all_tools_eligible"), default=False):
            overrides["programmatic_tool_calling_mark_all_tools_eligible"] = True

    return overrides
