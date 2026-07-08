"""Sinria external-egress boundary helpers.

Product rule:
- Sinria may read, use, and store confidential data internally.
- Sinria must not leak confidential data outside the organization.

This module therefore classifies and guards organization-external egress only.
It must not log raw secrets.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover - startup fallback
    get_hermes_home = None  # type: ignore[assignment]


Action = Literal["allow", "ask", "block"]


DEFAULT_EGRESS_CONFIG = {
    "mode": "ask",
    "confidential_external_send": "ask",
    "redact_secrets_before_external_send": True,
    "classify_lightweight": True,
}


@dataclass(frozen=True)
class EgressDecision:
    destination_type: str
    external: bool
    likely_confidential: bool
    action: Action
    reason: str


class SinriaEgressBlocked(RuntimeError):
    """Raised when an external egress payload is blocked or needs approval."""

    def __init__(self, decision: EgressDecision):
        self.decision = decision
        super().__init__(
            f"Sinria external egress guard: {decision.action} "
            f"for {decision.destination_type} ({decision.reason})"
        )


_EXTERNAL_DESTINATIONS = {
    "model_provider",
    "web_search",
    "browser",
    "messaging",
    "email",
    "webhook",
    "github_public",
    "cloud_storage",
    "mcp_external",
    "cron_delivery",
    "image_generation",
    "subagent_external",
    "terminal_external",
}

_CONFIDENTIAL_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\b(?:api[_-]?key|token|password|passwd|secret|credential|bearer)\b\s*[:=]", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?:社外秘|機密|患者|カルテ|診療録|契約書)", re.IGNORECASE),
    re.compile(r"\b(?:confidential|patient)\b", re.IGNORECASE),
    re.compile(r"\b(?:patient\s*id)\b|患者\s*id", re.IGNORECASE),
]

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(\b(?:api[_-]?key|token|password|passwd|secret|credential|bearer)\b\s*[:=]\s*)([^\s,;]+)",
    re.IGNORECASE,
)
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_BENIGN_SK_PLACEHOLDER_RE = re.compile(
    r"^sk-(?:your|example|sample|placeholder|test|dummy|redacted)(?:-[A-Za-z0-9_-]+)*$",
    re.IGNORECASE,
)
_PATIENT_ID_RE = re.compile(
    r"((?:\bpatient[_\s-]*id\b|患者\s*id)\s*(?:[:=#：]|\s+)\s*)"
    r"(?=[A-Za-z0-9_-]*\d)([A-Za-z0-9][A-Za-z0-9_-]{2,})",
    re.IGNORECASE,
)
_INLINE_PATIENT_ID_RE = re.compile(
    r"((?:\bpatient[_\s-]*id\b|患者\s*id))(?=[A-Za-z0-9_-]*\d)([A-Za-z0-9][A-Za-z0-9_-]{2,})",
    re.IGNORECASE,
)
_SYNTHETIC_PATIENT_ID_CONTEXT_RE = re.compile(
    r"(?:"
    r"example|sample|synthetic|fixture|mock|dummy|test|demo|"
    r"サンプル|例示|テスト|ダミー|モック|デモ|"
    r"keyFindings|outcomeTags|vitest|pytest|describe\(|it\("
    r")",
    re.IGNORECASE,
)
_SYNTHETIC_SECRET_CONTEXT_RE = re.compile(
    r"(?:"
    r"synthetic|fixture|mock|dummy|test|demo|"
    r"pytest|vitest|unittest|assert|raises|monkeypatch|"
    r"describe\(|it\(|def\s+test_|tests?/|source|snippet|"
    r"docs?|documentation|guide|usage|auth\s+add|--api-key|"
    r"例示|テスト|ダミー|モック|デモ"
    r")",
    re.IGNORECASE,
)


def is_external_destination(destination_type: str) -> bool:
    return destination_type in _EXTERNAL_DESTINATIONS


def looks_confidential(content: str) -> bool:
    if not content:
        return False
    return any(pattern.search(content) for pattern in _CONFIDENTIAL_PATTERNS)


_SECRET_VALUE_RE = re.compile(
    r"\b(?:api[_-]?key|token|password|passwd|secret|credential|bearer)\b\s*[:=]\s*([^\s,;\"'`<>]+)",
    re.IGNORECASE,
)
_BENIGN_SECRET_PLACEHOLDER_VALUES = {
    "token",
    "tokens",
    "key",
    "keys",
    "secret",
    "secrets",
    "password",
    "passwords",
    "credential",
    "credentials",
    "bearer",
    "redacted",
    "[redacted]",
    "example",
    "sample",
    "placeholder",
    "none",
    "null",
    "false",
    "true",
    "usage",
    "text",
}


def _has_concrete_secret_assignment(content: str) -> bool:
    """Detect concrete secret assignments while ignoring docs/UI labels.

    Sinria's model-provider guard sees the whole agent system prompt. That
    prompt legitimately contains local usage/help text such as "Token: usage"
    and policy wording about tokens. Those are not egress secrets. Treat
    key-like assignments as concrete only when the assigned value looks like an
    actual credential or an explicit secret fixture.
    """
    for match in _SECRET_VALUE_RE.finditer(content or ""):
        value = match.group(1).strip().strip('"\'')
        lowered = value.lower()
        if lowered in _BENIGN_SECRET_PLACEHOLDER_VALUES:
            continue
        if lowered.startswith("[redacted") or lowered.startswith("<redacted"):
            continue
        if re.match(r"^[A-Za-z_][\w.]*\([^\s,;]*\)", value):
            continue
        window = content[max(0, match.start() - 200): min(len(content), match.end() + 200)]
        if (
            ("example-secret" in lowered or "dummy-secret" in lowered or "test-secret" in lowered)
            and _SYNTHETIC_SECRET_CONTEXT_RE.search(window)
        ):
            continue
        if "example-secret" in lowered or "dummy-secret" in lowered or "test-secret" in lowered:
            return True
        if len(value) >= 12 and re.search(r"[A-Za-z]", value) and re.search(r"\d", value):
            return True
        if len(value) >= 20:
            return True
    return False


def _has_concrete_sk_token(content: str) -> bool:
    """Detect real sk-* tokens while ignoring docs/test placeholders."""
    for match in _SK_RE.finditer(content or ""):
        token = match.group(0)
        window = content[max(0, match.start() - 200): min(len(content), match.end() + 200)]
        if _BENIGN_SK_PLACEHOLDER_RE.match(token) and _SYNTHETIC_SECRET_CONTEXT_RE.search(window):
            continue
        return True
    return False


def looks_like_concrete_secret(content: str) -> bool:
    """Return True for concrete high-risk material, not mere policy concepts.

    Dogfooding should allow discussions about confidentiality/security through
    trusted frontier models, but never send obvious credentials, private keys,
    or direct patient identifiers without a stronger redaction/approval path.
    """
    if not content:
        return False
    if _CONFIDENTIAL_PATTERNS[0].search(content):  # private key blocks
        return True
    if _has_concrete_sk_token(content):
        return True
    if _has_concrete_secret_assignment(content):
        return True
    return _has_real_patient_identifier(content)


def _has_real_patient_identifier(content: str) -> bool:
    """Detect direct patient IDs while avoiding synthetic code/test fixtures.

    Sinria's model-provider guard sees the whole outbound request, including
    prior tool outputs and line-numbered source snippets. A synthetic fixture
    such as `keyFindings: ["患者ID 12345 ..."]` should not permanently poison
    the Discord session, but a direct user-provided patient identifier should
    still block.
    """
    for match in _PATIENT_ID_RE.finditer(content):
        window = content[max(0, match.start() - 160): min(len(content), match.end() + 160)]
        if _SYNTHETIC_PATIENT_ID_CONTEXT_RE.search(window):
            continue
        return True
    return False


def classify_external_egress(destination_type: str, content: str, config: dict | None = None) -> EgressDecision:
    config = config or {}
    external = is_external_destination(destination_type)
    likely_confidential = looks_confidential(content) if config.get("classify_lightweight", True) else False

    if not external:
        return EgressDecision(
            destination_type=destination_type,
            external=False,
            likely_confidential=likely_confidential,
            action="allow",
            reason="internal destination; Sinria may use and store internal confidential data",
        )

    if not likely_confidential:
        return EgressDecision(
            destination_type=destination_type,
            external=True,
            likely_confidential=False,
            action="allow",
            reason="external destination without lightweight confidential signals",
        )

    mode = str(config.get("mode", "ask")).lower()
    profile = str(config.get("profile", "") or "").lower()
    if profile == "dogfood_frontier" and destination_type == "model_provider":
        if looks_like_concrete_secret(content):
            return EgressDecision(
                destination_type=destination_type,
                external=True,
                likely_confidential=True,
                action="block",
                reason="dogfood_frontier concrete secret or direct identifier at model-provider boundary",
            )
        return EgressDecision(
            destination_type=destination_type,
            external=True,
            likely_confidential=True,
            action="allow",
            reason="dogfood_frontier trusted model-provider egress with audit",
        )

    if mode == "allow":
        action: Action = "allow"
    elif mode == "block":
        action = "block"
    else:
        configured_action = str(config.get("confidential_external_send", "ask")).lower()
        action = configured_action if configured_action in {"allow", "ask", "block"} else "ask"  # type: ignore[assignment]

    return EgressDecision(
        destination_type=destination_type,
        external=True,
        likely_confidential=True,
        action=action,
        reason="confidential-looking content at external egress boundary",
    )


def _payload_to_text(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(payload)


def _sanitize_sample(text: str, max_chars: int = 240) -> str:
    sample = (text or "")[:max_chars]
    sample = _SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]", sample)
    sample = _PATIENT_ID_RE.sub(r"\1[REDACTED]", sample)
    sample = _SK_RE.sub("[REDACTED_SK]", sample)
    return sample


def _redact_external_text(text: str) -> str:
    """Redact concrete egress secrets while preserving surrounding context.

    The gateway can legitimately run local discovery commands such as
    `gh auth status`. Their output may include credential metadata lines like
    `Token: ...`. Blocking the next model call strands the Discord session, but
    sending the raw value would violate Sinria's egress boundary. Redact the
    value before the payload reaches a trusted external model provider.
    """
    if not isinstance(text, str) or not text:
        return text
    text = _CONFIDENTIAL_PATTERNS[0].sub("[REDACTED_PRIVATE_KEY]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)
    text = _PATIENT_ID_RE.sub(r"\1[REDACTED]", text)
    text = _INLINE_PATIENT_ID_RE.sub(r"\1[REDACTED]", text)
    text = _SK_RE.sub("[REDACTED_SK]", text)
    return text


def _redact_external_payload(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_external_text(value)
    if isinstance(value, list):
        return [_redact_external_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_external_payload(item) for item in value)
    if isinstance(value, dict):
        return {key: _redact_external_payload(item) for key, item in value.items()}
    return value


def redact_model_provider_messages(agent: Any, messages: Any) -> Any:
    """Return a copy of model-provider messages safe for external LLM egress.

    Raw session history remains local. This only redacts the outbound request
    copy when Sinria is about to call an external model provider and the egress
    config asks for secret redaction before external sends.
    """
    config = _load_sinria_egress_config(agent)
    if not bool(config.get("redact_secrets_before_external_send", True)):
        return messages
    if _is_local_model_endpoint(agent):
        return messages
    try:
        redacted = _redact_external_payload(deepcopy(messages))
    except Exception:
        redacted = _redact_external_payload(messages)
    return redacted


def _default_audit_path() -> Path:
    if get_hermes_home is not None:
        try:
            return Path(get_hermes_home()) / "logs" / "sinria-egress-audit.jsonl"
        except Exception:
            pass
    return Path.home() / ".sinria" / "logs" / "sinria-egress-audit.jsonl"


def write_egress_audit(
    decision: EgressDecision,
    content: str,
    *,
    audit_path: Path | str | None = None,
    policy_profile: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Append a secret-safe JSONL audit record.

    Stores only metadata, a SHA-256 digest, length, and a redacted/truncated sample.
    Raw content must never be written.
    """
    path = Path(audit_path) if audit_path else _default_audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": int(time.time()),
        "decision": asdict(decision),
        "policy_profile": policy_profile or "",
        "content_sha256": hashlib.sha256((content or "").encode("utf-8", errors="replace")).hexdigest(),
        "content_len": len(content or ""),
        "sanitized_sample": _sanitize_sample(content),
        "raw_content_included": False,
    }
    if isinstance(metadata, dict):
        safe_metadata = _redact_external_payload(deepcopy(metadata))
        if isinstance(safe_metadata, dict):
            record.update(safe_metadata)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _is_local_model_endpoint(agent: Any) -> bool:
    provider = str(getattr(agent, "provider", "") or "").strip().lower()
    if provider in {"local", "ollama", "lmstudio", "llama.cpp", "llamacpp"}:
        return True
    base_url = str(getattr(agent, "base_url", "") or "")
    try:
        from agent.model_metadata import is_local_endpoint
        return bool(is_local_endpoint(base_url))
    except Exception:
        lowered = base_url.lower()
        return any(host in lowered for host in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"))


def resolve_sinria_policy_profile(config: dict | None) -> dict:
    """Return the active Sinria policy profile metadata, if configured."""
    if not isinstance(config, dict):
        return {}
    sinria = config.get("sinria")
    if not isinstance(sinria, dict):
        return {}
    policy = sinria.get("policy")
    if not isinstance(policy, dict):
        return {}
    active = str(policy.get("active_profile") or "").strip()
    profiles = policy.get("profiles")
    if not active or not isinstance(profiles, dict):
        return {}
    profile_cfg = profiles.get(active)
    if not isinstance(profile_cfg, dict):
        return {}
    return {"name": active, "config": profile_cfg}



def resolve_sinria_retention_policy(config: dict | None) -> dict:
    """Return retention behavior derived from the active Sinria policy profile."""
    profile = resolve_sinria_policy_profile(config)
    profile_cfg = profile.get("config") if isinstance(profile, dict) else None
    if not isinstance(profile_cfg, dict):
        return {
            "profile": "",
            "retain_raw_history_locally": True,
            "retain_sanitized_training_log": True,
        }
    return {
        "profile": str(profile.get("name") or ""),
        "retain_raw_history_locally": bool(profile_cfg.get("retain_raw_history_locally", True)),
        "retain_sanitized_training_log": bool(profile_cfg.get("retain_sanitized_training_log", True)),
    }


_DEFAULT_BOUNDARY_CONTROL = {
    "deployment_modes": {
        "full_on_prem": {
            "external_model_egress": "block",
            "network_egress": "deny_by_default",
            "allowed_provider_trust": ["local_only", "sovereign_private"],
            "human_approval_required": True,
        },
        "hybrid_confidential": {
            "external_model_egress": "sanitized_only",
            "network_egress": "allowlist",
            "allowed_provider_trust": ["local_only", "approved_cloud"],
            "human_approval_required": True,
        },
        "cloud_enhanced": {
            "external_model_egress": "allow_public_low_risk",
            "network_egress": "allowlist",
            "allowed_provider_trust": ["local_only", "approved_cloud", "trusted_frontier"],
            "human_approval_required": False,
        },
    },
    "data_policy_matrix": {
        "public": {
            "external_egress": "allow",
            "model_route": "approved_cloud_or_local",
            "approval": "not_required",
            "audit": True,
        },
        "internal": {
            "external_egress": "ask",
            "model_route": "approved_cloud_or_local",
            "approval": "required",
            "audit": True,
        },
        "phi_pii": {
            "external_egress": "block",
            "model_route": "local_only",
            "approval": "clinical_or_security_required",
            "audit": True,
        },
        "credential": {
            "external_egress": "block",
            "model_route": "no_model_egress",
            "approval": "not_permitted",
            "audit": True,
        },
        "classified": {
            "external_egress": "block",
            "model_route": "air_gapped_only",
            "approval": "security_required",
            "audit": True,
        },
    },
    "provider_trust_registry": {
        "local_vllm": {
            "trust_level": "local_only",
            "external_egress": False,
            "approved_data_classes": ["public", "internal", "phi_pii", "credential", "classified"],
            "training_use": False,
            "retention": "local_policy",
        },
        "openai_enterprise": {
            "trust_level": "approved_cloud",
            "external_egress": True,
            "approved_data_classes": ["public", "internal"],
            "training_use": False,
            "retention": "contract_defined",
            "requires_sanitization": True,
        },
        "anthropic_enterprise": {
            "trust_level": "approved_cloud",
            "external_egress": True,
            "approved_data_classes": ["public", "internal"],
            "training_use": False,
            "retention": "contract_defined",
            "requires_sanitization": True,
        },
    },
}

_CLASSIFIED_RE = re.compile(r"(?:classified|top secret|secret//|機密区分|防衛秘密|特定秘密)", re.IGNORECASE)
_INTERNAL_RE = re.compile(r"(?:confidential|internal|社外秘|機密|契約書|取締役会|board memo)", re.IGNORECASE)


def _deep_merge_dict(base: dict, overlay: dict | None) -> dict:
    merged = deepcopy(base)
    if not isinstance(overlay, dict):
        return merged
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def classify_sinria_data_class(content: Any) -> str:
    """Classify payload material into Sinria's commercial policy classes.

    This is intentionally conservative and metadata-only. It is not a final DLP
    engine; it gives the Boundary Control Layer a deterministic first gate for
    model routing, preview, audit, and customer-facing policy reports.
    """
    text = _payload_to_text(content) if not isinstance(content, str) else content
    if not text:
        return "public"
    if "[REDACTED_PRIVATE_KEY]" in text:
        return "credential"
    if _CLASSIFIED_RE.search(text):
        return "classified"
    if looks_like_concrete_secret(text):
        if _has_real_patient_identifier(text):
            return "phi_pii"
        return "credential"
    if _has_real_patient_identifier(text) or re.search(r"(?:患者|カルテ|診療録|病歴|検査結果|PHI|PII)", text, re.IGNORECASE):
        return "phi_pii"
    if _INTERNAL_RE.search(text):
        return "internal"
    return "public"


def resolve_sinria_boundary_control(config: dict | None) -> dict:
    """Resolve Sinria Boundary Control Layer metadata for admin/compliance views."""
    config = config if isinstance(config, dict) else {}
    sinria = config.get("sinria") if isinstance(config.get("sinria"), dict) else {}
    boundary_cfg = sinria.get("boundary_control") if isinstance(sinria, dict) else None
    boundary = _deep_merge_dict(_DEFAULT_BOUNDARY_CONTROL, boundary_cfg if isinstance(boundary_cfg, dict) else {})
    profile = resolve_sinria_policy_profile(config)
    profile_name = str(profile.get("name") or "") if isinstance(profile, dict) else ""
    profile_cfg = profile.get("config") if isinstance(profile, dict) else None
    deployment_mode = "hybrid_confidential"
    if isinstance(profile_cfg, dict):
        deployment_mode = str(profile_cfg.get("deployment_mode") or "").strip() or deployment_mode
        provider_trust = profile_cfg.get("provider_trust")
        if provider_trust == "local_only" and not profile_cfg.get("deployment_mode"):
            deployment_mode = "full_on_prem"
        elif provider_trust == "trusted_frontier" and not profile_cfg.get("deployment_mode"):
            deployment_mode = "cloud_enhanced"
    if deployment_mode not in boundary["deployment_modes"]:
        deployment_mode = "hybrid_confidential"
    boundary["active_profile"] = profile_name
    boundary["deployment_mode"] = deployment_mode
    boundary["deployment_mode_policy"] = boundary["deployment_modes"][deployment_mode]
    boundary["raw_content_included"] = False
    return boundary


def _config_with_deployment_mode(config: dict | None, deployment_mode: str) -> dict:
    """Return a config copy whose active policy profile forces a deployment mode.

    This lets callers select a deployment mode directly (e.g. the documented
    routing smoke) without hand-authoring a full policy profile, while still
    preserving any existing `sinria.boundary_control` overrides.
    """
    base = deepcopy(config) if isinstance(config, dict) else {}
    sinria = base.setdefault("sinria", {})
    if not isinstance(sinria, dict):
        sinria = base["sinria"] = {}
    policy = sinria.setdefault("policy", {})
    if not isinstance(policy, dict):
        policy = sinria["policy"] = {}
    profiles = policy.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        profiles = policy["profiles"] = {}
    profiles["_sinria_runtime_mode"] = {"deployment_mode": deployment_mode}
    policy["active_profile"] = "_sinria_runtime_mode"
    return base


def route_model_provider_for_payload(
    payload: Any,
    *,
    provider_key: str | None = None,
    provider: str | None = None,
    deployment_mode: str | None = None,
    config: dict | None = None,
) -> dict:
    """Return a policy decision for routing a payload to a named model provider.

    `provider` is accepted as an alias for `provider_key`, and `deployment_mode`
    may be supplied directly instead of via a full policy profile, so operator
    smoke checks can call this with the documented short signature.
    """
    provider_key = (provider_key or provider or "").strip()
    if deployment_mode:
        config = _config_with_deployment_mode(config, deployment_mode)
    boundary = resolve_sinria_boundary_control(config)
    data_class = classify_sinria_data_class(payload)
    matrix = boundary["data_policy_matrix"].get(data_class, boundary["data_policy_matrix"]["internal"])
    registry = boundary["provider_trust_registry"]
    provider = registry.get(provider_key)
    if not isinstance(provider, dict):
        return {
            "provider_key": provider_key,
            "data_class": data_class,
            "allowed": False,
            "external_egress": matrix.get("external_egress", "ask"),
            "approval": matrix.get("approval", "required"),
            "required_model_route": matrix.get("model_route", "approved_cloud_or_local"),
            "requires_sanitization": True,
            "reason": "provider is not registered in Sinria provider trust registry",
        }
    trust_level = str(provider.get("trust_level") or "")
    mode_policy = boundary["deployment_mode_policy"]
    allowed_trust = set(mode_policy.get("allowed_provider_trust") or [])
    approved_classes = set(provider.get("approved_data_classes") or [])
    allowed = trust_level in allowed_trust and data_class in approved_classes and matrix.get("external_egress") != "block"
    if data_class in approved_classes and trust_level == "local_only":
        allowed = True
    if matrix.get("model_route") in {"local_only", "air_gapped_only", "no_model_egress"} and trust_level != "local_only":
        allowed = False
    if trust_level not in allowed_trust:
        reason = f"provider trust level {trust_level or 'unknown'} is not allowed in deployment mode {boundary['deployment_mode']}"
    elif data_class not in approved_classes:
        reason = f"provider {provider_key} is not approved for data class {data_class}"
    elif matrix.get("external_egress") == "block" and trust_level != "local_only":
        reason = f"data class {data_class} requires {matrix.get('model_route')} and blocks external model egress"
    else:
        reason = f"provider {provider_key} is approved for {data_class} under {boundary['deployment_mode']}"
    return {
        "provider_key": provider_key,
        "provider_trust": trust_level,
        "data_class": data_class,
        "allowed": bool(allowed),
        "external_egress": matrix.get("external_egress", "ask"),
        "approval": matrix.get("approval", "required"),
        "required_model_route": matrix.get("model_route", "approved_cloud_or_local"),
        "requires_sanitization": bool(provider.get("requires_sanitization") or mode_policy.get("external_model_egress") == "sanitized_only"),
        "deployment_mode": boundary["deployment_mode"],
        "reason": reason,
    }


def preview_external_egress(
    destination_type: str,
    payload: Any,
    *,
    provider_key: str | None = None,
    config: dict | None = None,
    max_chars: int = 800,
) -> dict:
    """Build a human-reviewable, sanitized preview of what would leave Sinria."""
    content = _payload_to_text(payload)
    route = route_model_provider_for_payload(payload, provider_key=provider_key or "", config=config) if provider_key else {}
    data_class = route.get("data_class") or classify_sinria_data_class(payload)
    action = route.get("external_egress") if route else None
    if action not in {"allow", "ask", "block"}:
        action = "ask" if action else classify_external_egress(destination_type, content, DEFAULT_EGRESS_CONFIG).action
    decision = EgressDecision(
        destination_type=destination_type,
        external=is_external_destination(destination_type),
        likely_confidential=data_class != "public",
        action=action,  # type: ignore[arg-type]
        reason=route.get("reason") or "Sinria Boundary Control Layer sanitized egress preview",
    )
    approval = str(route.get("approval") or "") if route else ""
    approval_required = bool(approval) and approval != "not_required"
    allowed = bool(route.get("allowed")) if route else (action == "allow")
    return {
        "destination_type": destination_type,
        "action": action,
        "allowed": allowed,
        "provider": provider_key or "",
        "provider_key": provider_key or "",
        "data_class": data_class,
        "required_route": route.get("required_model_route") if route else None,
        "approval": approval,
        "approval_required": approval_required,
        "raw_content_included": False,
        "external_action_performed": False,
        "sanitized_preview": _sanitize_sample(_redact_external_text(content), max_chars=max_chars),
        "decision": asdict(decision),
        "route": route,
    }


def export_sinria_boundary_compliance_report(config: dict | None = None) -> dict:
    """Export a raw-content-free Boundary Control report for buyers/auditors."""
    boundary = resolve_sinria_boundary_control(config)
    return {
        "product": "Sinria Boundary Control Layer",
        "active_profile": boundary.get("active_profile", ""),
        "deployment_mode": boundary.get("deployment_mode", "hybrid_confidential"),
        "raw_content_included": False,
        "controls": [
            "deployment_mode_profiles",
            "data_policy_matrix",
            "provider_trust_registry",
            "egress_preview",
            "audit_metadata_only",
        ],
        "deployment_modes": boundary["deployment_modes"],
        "data_policy_matrix": boundary["data_policy_matrix"],
        "provider_trust_registry": boundary["provider_trust_registry"],
    }


_REQUIRED_DATA_CLASSES = ("public", "internal", "phi_pii", "credential", "classified")
_REQUIRED_DEPLOYMENT_MODES = ("full_on_prem", "hybrid_confidential", "cloud_enhanced")
_SENSITIVE_DATA_CLASSES = ("phi_pii", "credential", "classified")
_KNOWN_TRUST_LEVELS = (
    "local_only",
    "sovereign_private",
    "approved_cloud",
    "trusted_frontier",
)
_CLOUD_TRUST_LEVELS = ("approved_cloud", "trusted_frontier")


def validate_sinria_boundary_policy(config: dict | None) -> dict:
    """Validate a Boundary Control policy and fail closed with sanitized errors.

    Errors only reference policy identifiers (class/mode/provider/field names),
    never raw confidential content. A missing or malformed required policy is a
    hard error so the resolved config cannot silently drop a data class,
    deployment mode, or provider boundary.
    """
    errors: list[str] = []
    boundary = resolve_sinria_boundary_control(config)

    # If the operator ships an explicit override, it must be complete: defaults
    # silently backfill the resolved envelope, but an explicit policy that drops
    # a required class/mode is a policy authoring error and must fail closed.
    raw_cfg = config if isinstance(config, dict) else {}
    raw_sinria = raw_cfg.get("sinria") if isinstance(raw_cfg.get("sinria"), dict) else {}
    raw_override = raw_sinria.get("boundary_control") if isinstance(raw_sinria, dict) else None
    if isinstance(raw_override, dict):
        raw_modes = raw_override.get("deployment_modes")
        if isinstance(raw_modes, dict):
            for mode in _REQUIRED_DEPLOYMENT_MODES:
                if mode not in raw_modes:
                    errors.append(f"missing deployment mode: {mode}")
        raw_matrix = raw_override.get("data_policy_matrix")
        if isinstance(raw_matrix, dict):
            for cls in _REQUIRED_DATA_CLASSES:
                if cls not in raw_matrix:
                    errors.append(f"missing data class policy: {cls}")

    modes = boundary.get("deployment_modes") if isinstance(boundary.get("deployment_modes"), dict) else {}
    for mode in _REQUIRED_DEPLOYMENT_MODES:
        if mode not in modes:
            errors.append(f"missing deployment mode: {mode}")

    matrix = boundary.get("data_policy_matrix") if isinstance(boundary.get("data_policy_matrix"), dict) else {}
    for cls in _REQUIRED_DATA_CLASSES:
        entry = matrix.get(cls)
        if not isinstance(entry, dict):
            errors.append(f"missing data class policy: {cls}")
            continue
        for field in ("external_egress", "model_route", "approval"):
            if field not in entry:
                errors.append(f"data class {cls} missing field: {field}")
    for cls in _SENSITIVE_DATA_CLASSES:
        entry = matrix.get(cls)
        if isinstance(entry, dict) and entry.get("external_egress") != "block":
            errors.append(f"data class {cls} must block external egress by default")

    registry = boundary.get("provider_trust_registry") if isinstance(boundary.get("provider_trust_registry"), dict) else {}
    if not registry:
        errors.append("provider trust registry is empty")
    for name, prov in registry.items():
        if not isinstance(prov, dict):
            errors.append(f"provider {name} is not a policy object")
            continue
        trust = str(prov.get("trust_level") or "").strip()
        if trust not in _KNOWN_TRUST_LEVELS:
            errors.append(f"provider {name} has unknown trust level: {trust or 'unset'}")
        approved = prov.get("approved_data_classes")
        if not isinstance(approved, list) or not approved:
            errors.append(f"provider {name} has no approved data classes")
        else:
            for cls in approved:
                if cls not in _REQUIRED_DATA_CLASSES:
                    errors.append(f"provider {name} references unknown data class: {cls}")
            if trust in _CLOUD_TRUST_LEVELS:
                for cls in _SENSITIVE_DATA_CLASSES:
                    if cls in approved:
                        errors.append(f"cloud provider {name} must not accept {cls} by default")

    return {
        "valid": not errors,
        "errors": errors,
        "deployment_mode": boundary.get("deployment_mode", ""),
        "active_profile": boundary.get("active_profile", ""),
        "raw_content_included": False,
    }



def _policy_profile_overlay(config: dict | None) -> dict:
    profile = resolve_sinria_policy_profile(config)
    profile_name = str(profile.get("name") or "") if isinstance(profile, dict) else ""
    profile_cfg = profile.get("config") if isinstance(profile, dict) else None
    if not profile_name or not isinstance(profile_cfg, dict):
        return {}
    overlay = {"profile": profile_name}
    if "external_send" in profile_cfg:
        overlay["mode"] = profile_cfg.get("external_send")
    if "confidential_external_send" in profile_cfg:
        overlay["confidential_external_send"] = profile_cfg.get("confidential_external_send")
    if "provider_trust" in profile_cfg:
        overlay["provider_trust"] = profile_cfg.get("provider_trust")
    return overlay



def _load_sinria_egress_config(agent: Any) -> dict:
    explicit = getattr(agent, "sinria_egress_config", None)
    loaded_cfg = None
    try:
        from hermes_cli.config import load_config
        loaded_cfg = load_config() or {}
    except Exception:
        loaded_cfg = None

    effective = dict(DEFAULT_EGRESS_CONFIG)
    sinria = loaded_cfg.get("sinria") if isinstance(loaded_cfg, dict) else None
    egress = sinria.get("egress") if isinstance(sinria, dict) else None
    if isinstance(egress, dict):
        effective.update(egress)
    effective.update(_policy_profile_overlay(loaded_cfg))
    if isinstance(explicit, dict):
        effective.update(explicit)
    return effective


def _load_sinria_boundary_config(agent: Any) -> dict | None:
    explicit = getattr(agent, "sinria_boundary_config", None)
    if isinstance(explicit, dict):
        return explicit
    return None


def _infer_boundary_provider_key(agent: Any) -> str:
    explicit = str(getattr(agent, "sinria_provider_key", "") or "").strip()
    if explicit:
        return explicit
    provider = str(getattr(agent, "provider", "") or "").strip().lower()
    if _is_local_model_endpoint(agent):
        return "local_vllm"
    if provider in {"openai", "openai-codex", "gpt", "chatgpt"}:
        return "openai_enterprise"
    if provider in {"anthropic", "claude"}:
        return "anthropic_enterprise"
    return provider


def _enforce_external_decision(
    decision: EgressDecision,
    content: str,
    *,
    audit_path: Path | str | None = None,
    config: dict | None = None,
    audit_metadata: dict | None = None,
) -> EgressDecision:
    if decision.external and decision.likely_confidential:
        policy_profile = ""
        if isinstance(config, dict):
            policy_profile = str(config.get("profile") or "")
        write_egress_audit(
            decision,
            content,
            audit_path=audit_path,
            policy_profile=policy_profile,
            metadata=audit_metadata,
        )

    if decision.external and decision.likely_confidential and decision.action == "ask":
        if _request_interactive_egress_approval(decision, content):
            return decision
        raise SinriaEgressBlocked(decision)

    if decision.external and decision.likely_confidential and decision.action == "block":
        raise SinriaEgressBlocked(decision)

    return decision


def _request_interactive_egress_approval(decision: EgressDecision, content: str) -> bool:
    """Ask the active gateway user before allowing an external egress payload.

    The approval prompt must stay secret-safe: it shows only a sanitized,
    truncated sample and never raw content.
    """
    try:
        from tools.approval import request_gateway_approval
    except Exception:
        return False

    preview = (
        f"Sinria external egress request\n"
        f"Destination: {decision.destination_type}\n"
        f"Reason: {decision.reason}\n"
        f"Sanitized sample: {_sanitize_sample(content, max_chars=800)}"
    )
    result = request_gateway_approval(
        preview,
        f"Sinria external egress requires approval: {decision.reason}",
        pattern_key=f"sinria_egress:{decision.destination_type}",
        pattern_keys=[f"sinria_egress:{decision.destination_type}"],
        allow_session=True,
        allow_permanent=False,
        metadata={
            "approval_kind": "sinria_egress",
            "title": "🛡️ Sinria External Egress Approval Required",
            "preview_label": "Sanitized egress preview",
        },
    )
    return bool(result.get("approved"))


def guard_model_provider_egress(agent: Any, messages: Any) -> EgressDecision:
    """Guard the model-provider request payload before external transmission.

    Local model endpoints are internal. External model providers are classified
    and blocked/asked according to `sinria.egress` when the payload looks
    confidential.
    """
    config = _load_sinria_egress_config(agent)
    destination_type = "internal_model_provider" if _is_local_model_endpoint(agent) else "model_provider"
    content = _payload_to_text(messages)
    audit_path = getattr(agent, "sinria_egress_audit_path", None)
    boundary_config = _load_sinria_boundary_config(agent)
    provider_key = _infer_boundary_provider_key(agent)
    if provider_key and isinstance(boundary_config, dict):
        route = route_model_provider_for_payload(messages, provider_key=provider_key, config=boundary_config)
        boundary_metadata = {
            "provider_key": provider_key,
            "model": str(getattr(agent, "model", "") or ""),
            "boundary_decision": route,
        }
        if not bool(route.get("allowed")):
            decision = EgressDecision(
                destination_type=destination_type,
                external=destination_type == "model_provider",
                likely_confidential=route.get("data_class") != "public",
                action="block",
                reason=f"Sinria Boundary Control Layer blocked model-provider route: {route.get('reason')}",
            )
            write_egress_audit(
                decision,
                content,
                audit_path=audit_path,
                policy_profile=str(config.get("profile") or ""),
                metadata=boundary_metadata,
            )
            raise SinriaEgressBlocked(decision)
        if destination_type != "model_provider":
            return EgressDecision(
                destination_type=destination_type,
                external=False,
                likely_confidential=route.get("data_class") != "public",
                action="allow",
                reason=f"Sinria Boundary Control Layer allowed local model-provider route: {route.get('reason')}",
            )
    decision = classify_external_egress(destination_type, content, config)
    return _enforce_external_decision(decision, content, audit_path=audit_path, config=config)


def guard_messaging_egress(
    target: str,
    message: str,
    *,
    config: dict | None = None,
    audit_path: Path | str | None = None,
) -> EgressDecision:
    """Guard cross-platform messaging payloads before external send.

    Messaging platforms are organization-external by default. This does not
    restrict internal storage/mirroring; it only gates outbound delivery.
    """
    effective_config = config if isinstance(config, dict) else _load_sinria_egress_config(None)
    content = _payload_to_text({"target": target, "message": message})
    decision = classify_external_egress("messaging", content, effective_config)
    return _enforce_external_decision(decision, content, audit_path=audit_path, config=effective_config)


def guard_web_query_egress(
    destination_type: str,
    query_or_url: str,
    *,
    config: dict | None = None,
    audit_path: Path | str | None = None,
) -> EgressDecision:
    """Guard external web/search/browser query material before network send.

    Web search queries, crawl prompts, browser navigations, and remote URLs can
    become organization-external egress. Non-sensitive web use remains allowed;
    confidential-looking query material is stopped for approval/sanitization.
    """
    effective_config = config if isinstance(config, dict) else _load_sinria_egress_config(None)
    normalized_destination = destination_type if destination_type in _EXTERNAL_DESTINATIONS else destination_type
    content = _payload_to_text({"destination_type": normalized_destination, "query_or_url": query_or_url})
    decision = classify_external_egress(normalized_destination, content, effective_config)
    return _enforce_external_decision(decision, content, audit_path=audit_path, config=effective_config)


def guard_cron_delivery_egress(
    target: str,
    content: str,
    *,
    config: dict | None = None,
    audit_path: Path | str | None = None,
) -> EgressDecision:
    """Guard cron result delivery before any external platform send.

    Cron storage/output remains internal and allowed; only configured outbound
    delivery targets such as Discord, Telegram, email, or webhooks are gated.
    """
    effective_config = config if isinstance(config, dict) else _load_sinria_egress_config(None)
    payload = _payload_to_text({"target": target, "content": content})
    decision = classify_external_egress("cron_delivery", payload, effective_config)
    return _enforce_external_decision(decision, payload, audit_path=audit_path, config=effective_config)


def guard_image_generation_egress(
    prompt: str,
    *,
    config: dict | None = None,
    audit_path: Path | str | None = None,
) -> EgressDecision:
    """Guard image-generation prompts before external provider submission."""
    effective_config = config if isinstance(config, dict) else _load_sinria_egress_config(None)
    payload = _payload_to_text({"prompt": prompt})
    decision = classify_external_egress("image_generation", payload, effective_config)
    return _enforce_external_decision(decision, payload, audit_path=audit_path, config=effective_config)


_EXTERNAL_TERMINAL_COMMAND_RE = re.compile(
    r"(?:^|[;&|\n]\s*)(?:"
    r"curl\b|wget\b|http\b|https\b|"
    r"gh\s+(?:issue|pr|gist|release)\s+(?:create|comment|edit|upload)|"
    r"git\s+push\b|"
    r"aws\s+s3\s+(?:cp|sync)\b|"
    r"gsutil\s+(?:cp|rsync)\b"
    r")",
    re.IGNORECASE,
)


def command_has_external_egress(command: str) -> bool:
    """Return True for terminal commands that can send payloads externally."""
    return bool(_EXTERNAL_TERMINAL_COMMAND_RE.search(command or ""))


def guard_terminal_command_egress(
    command: str,
    *,
    config: dict | None = None,
    audit_path: Path | str | None = None,
) -> EgressDecision:
    """Guard obvious terminal-based organization-external egress commands.

    Local terminal processing remains internal and allowed. This only gates
    command lines whose executable/verb is clearly capable of sending data to
    external services.
    """
    destination = "terminal_external" if command_has_external_egress(command) else "terminal_internal"
    effective_config = config if isinstance(config, dict) else _load_sinria_egress_config(None)
    payload = _payload_to_text({"command": command})
    decision = classify_external_egress(destination, payload, effective_config)
    return _enforce_external_decision(decision, payload, audit_path=audit_path, config=effective_config)
