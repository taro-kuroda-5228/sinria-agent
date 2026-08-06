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

BOUNDARY_PROVIDER_NOT_REGISTERED_REASON = (
    "provider is not registered in Sinria provider trust registry"
)


@dataclass(frozen=True)
class EgressDecision:
    destination_type: str
    external: bool
    likely_confidential: bool
    action: Action
    reason: str


class SinriaEgressBlocked(RuntimeError):
    """Raised when an external egress payload is blocked or needs approval."""

    def __init__(
        self,
        decision: EgressDecision,
        *,
        metadata: dict | None = None,
    ):
        self.decision = decision
        self.metadata = dict(metadata or {})
        super().__init__(
            f"Sinria external egress guard: {decision.action} "
            f"for {decision.destination_type} ({decision.reason})"
        )


class SinriaEgressGuardFailure(RuntimeError):
    """Raised when the guard itself fails; provider transmission must stop."""

    def __init__(self, stage: str = "model-provider request"):
        super().__init__(f"Sinria egress guard failed closed during {stage}")


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
_SANITIZED_CONFIDENTIAL_PLACEHOLDERS = (
    "(機密の可能性があるため要約は保存しません)",
    "(臨床・患者情報を含む可能性があるため、具体的内容は保存しません。"
    "ローカルSinriaで再入力・承認してください)",
)
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
    r"(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    re.IGNORECASE | re.DOTALL,
)

# The detector (`_SECRET_VALUE_RE`) and the redactor (`_SECRET_ASSIGNMENT_RE`)
# must share this value class; drift in either direction is a live defect.
# Wider on the redactor swallows the structural delimiters around the value —
# tool results are JSON, and eating a closing `\"` corrupts the payload the
# model receives. Wider on the detector reports material redaction can never
# remove, such as `Token: {raw_token}` in a listed source line, so the payload
# classifies as `credential` for as long as it stays in session history and the
# boundary refuses every provider with no approval able to release it.
_SECRET_KEY_RE_SRC = (
    r"\b(?:api[_-]?key|token|password|passwd|secret|credential|bearer)\b\s*[:=]\s*"
)
_SECRET_VALUE_CLASS_SRC = r"[^\s,;\"'`<>{}\\]+"
_SECRET_ASSIGNMENT_RE = re.compile(
    rf"({_SECRET_KEY_RE_SRC})({_SECRET_VALUE_CLASS_SRC})",
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
_PATIENT_ID_PLACEHOLDER_RE = re.compile(
    r"(?:\bpatient[_\s-]*id\b|患者\s*id)\s*(?:[:=#：]|\s+)\s*"
    r"(?:\[REDACTED\]|<redacted>|redacted)",
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
    # Company OS emits these fixed envelopes only after removing source detail.
    # Strip exact canonical values; any additional patient/secret text remains
    # visible to the normal classifiers and is still blocked.
    for placeholder in _SANITIZED_CONFIDENTIAL_PLACEHOLDERS:
        content = content.replace(placeholder, "")
    return any(pattern.search(content) for pattern in _CONFIDENTIAL_PATTERNS)


_SECRET_VALUE_RE = re.compile(
    rf"{_SECRET_KEY_RE_SRC}({_SECRET_VALUE_CLASS_SRC})",
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
    sample = _PRIVATE_KEY_BLOCK_RE.sub("[REDACTED_PRIVATE_KEY]", sample)
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
    text = _PRIVATE_KEY_BLOCK_RE.sub("[REDACTED_PRIVATE_KEY]", text)
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


class _PreparedModelProviderPayload(dict):
    """Marker for an exact provider payload already redacted and guarded.

    The marker is process-local. Passing it through ``**payload`` preserves the
    transmitted mapping while allowing deeper transport helpers to avoid a
    duplicate approval prompt. Any shape-changing copy/preflight operation
    returns a plain dict and must be checked again before transmission.
    """

    _sinria_prepared_digest: str | None = None


def _prepared_payload_digest(payload: dict) -> str:
    """Return a raw-content-free integrity marker for a prepared payload."""

    return hashlib.sha256(
        _payload_to_text(dict(payload)).encode("utf-8", errors="replace")
    ).hexdigest()


def redact_model_provider_payload(agent: Any, payload: dict) -> dict:
    """Return a transport-only copy with every textual payload leaf redacted."""

    config = _load_sinria_egress_config(agent)
    try:
        copied = deepcopy(dict(payload))
    except Exception:
        copied = dict(payload)
    if not bool(config.get("redact_secrets_before_external_send", True)):
        return copied
    if _is_local_model_endpoint(agent):
        return copied
    return _redact_external_payload(copied)


BOUNDARY_WITHHELD_CONTENT_TEMPLATE = (
    "[Sinria boundary policy withheld this content: data_class={data_class}]"
)

# Classes the boundary refuses to route externally. Withholding a unit of one of
# these classes removes the reason for the refusal; `public`/`internal` units are
# never touched because they were never the cause.
_BOUNDARY_WITHHOLDABLE_CLASSES = frozenset({"classified", "credential", "phi_pii"})

# Outbound text lives under different keys per API: chat completions and
# Anthropic use `messages`, the Responses API uses `input`, and the system prompt
# is a top-level `system`/`instructions` value.
_BOUNDARY_UNIT_LIST_KEYS = ("messages", "input")
_BOUNDARY_UNIT_TEXT_KEYS = ("system", "instructions")


def _withheld_content_value(value: Any, placeholder: str) -> Any:
    """Withhold a body while keeping the container shape the provider expects.

    Structured content (`[{"type": "input_text", "text": ...}]`) must stay an
    array of parts: collapsing it to a bare string changes the item's shape after
    the runtime's preflight has already validated it. Parts without text — images
    — carry material the boundary refused and cannot be replaced in place, so
    they are dropped instead of transmitted.
    """

    if not isinstance(value, list):
        return placeholder
    rewritten = []
    for part in value:
        if not isinstance(part, dict):
            rewritten.append(placeholder)
            continue
        if isinstance(part.get("text"), str):
            part = dict(part)
            part["text"] = placeholder
            rewritten.append(part)
    return rewritten or placeholder


def _withheld_unit(unit: Any, data_class: str) -> Any:
    """Return a copy of one outbound unit with its body withheld.

    The body is replaced rather than stripped of its markings: sending the
    surrounding text with the marking removed would launder a genuinely
    classified document through the boundary. Structural fields the provider API
    requires (`role`, `tool_call_id`, `name`) are preserved so withholding never
    turns a policy stop into a malformed request.
    """

    placeholder = BOUNDARY_WITHHELD_CONTENT_TEMPLATE.format(data_class=data_class)
    if not isinstance(unit, dict):
        return placeholder
    withheld = dict(unit)
    if "content" in withheld:
        withheld["content"] = _withheld_content_value(withheld["content"], placeholder)
    # The Responses API carries tool results under `output`, not `content`; the
    # live Codex runtime uses that shape, so omitting it would leave the main
    # runtime unable to recover.
    if "output" in withheld:
        withheld["output"] = _withheld_content_value(withheld["output"], placeholder)
    # Tool arguments carry the same material as message bodies — a `write_file`
    # call holds the document itself. They must be withheld as valid JSON, since
    # providers parse this field.
    withheld_arguments = json.dumps({"sinria_withheld": placeholder}, ensure_ascii=False)
    if "arguments" in withheld:
        withheld["arguments"] = withheld_arguments
    tool_calls = withheld.get("tool_calls")
    if isinstance(tool_calls, list):
        rewritten_calls = []
        for call in tool_calls:
            if not isinstance(call, dict):
                rewritten_calls.append(call)
                continue
            call = dict(call)
            if "arguments" in call:
                call["arguments"] = withheld_arguments
            function = call.get("function")
            if isinstance(function, dict):
                function = dict(function)
                function["arguments"] = withheld_arguments
                call["function"] = function
            rewritten_calls.append(call)
        withheld["tool_calls"] = rewritten_calls
    return withheld


def _withhold_boundary_violating_units(payload: dict) -> tuple[dict, list[dict]]:
    """Withhold every unit whose own data class the boundary refuses to route.

    Classifying per unit rather than per payload is what keeps the turn usable:
    the payload as a whole is refused, but only the units that caused it lose
    their content.
    """

    withheld_payload = dict(payload)
    records: list[dict] = []

    for key in _BOUNDARY_UNIT_LIST_KEYS:
        units = withheld_payload.get(key)
        if not isinstance(units, list):
            continue
        rewritten = list(units)
        withheld_here = False
        for index, unit in enumerate(units):
            data_class = classify_runtime_model_data_class(
                unit, payload_is_redacted=True
            )
            if data_class not in _BOUNDARY_WITHHOLDABLE_CLASSES:
                continue
            rewritten[index] = _withheld_unit(unit, data_class)
            records.append({"kind": key, "index": index, "data_class": data_class})
            withheld_here = True
        if withheld_here:
            withheld_payload[key] = rewritten

    for key in _BOUNDARY_UNIT_TEXT_KEYS:
        value = withheld_payload.get(key)
        if value is None:
            continue
        data_class = classify_runtime_model_data_class(
            value, payload_is_redacted=True
        )
        if data_class not in _BOUNDARY_WITHHOLDABLE_CLASSES:
            continue
        withheld_payload[key] = _withheld_content_value(
            value, BOUNDARY_WITHHELD_CONTENT_TEMPLATE.format(data_class=data_class)
        )
        records.append({"kind": key, "data_class": data_class})

    return withheld_payload, records


def _withhold_and_reguard(
    agent: Any,
    prepared: dict,
    blocked: SinriaEgressBlocked,
) -> "_PreparedModelProviderPayload":
    """Withhold the refused units, then re-check the result under the same policy.

    The re-check is what keeps this fail-closed: withholding only proposes a
    payload, the unchanged guard decides. A refusal caused by something other
    than the content — an unregistered provider, a disallowed trust level —
    produces no withholdable unit and is re-raised untouched.
    """

    withheld_payload, records = _withhold_boundary_violating_units(prepared)
    if not records:
        raise blocked
    candidate = _PreparedModelProviderPayload(withheld_payload)
    guard_model_provider_egress(agent, candidate)
    _audit_boundary_withheld_send(agent, candidate, records)
    _notify_boundary_withheld(agent, records)
    return candidate


def _notify_boundary_withheld(agent: Any, records: list[dict]) -> None:
    """Tell the operator that part of the request was withheld.

    Without this the model looks like it simply forgot what it just read, and
    the operator has no way to connect that to a policy decision.
    """

    emit = getattr(agent, "_emit_status", None)
    if not callable(emit):
        return
    data_classes = ", ".join(
        sorted({str(record.get("data_class") or "") for record in records})
    )
    try:
        emit(
            f"🛡️ Withheld {len(records)} unit(s) from this model request under "
            f"the Sinria boundary policy ({data_classes}); local history is unchanged."
        )
    except Exception:
        # Status delivery is cosmetic; it must never re-strand a released turn.
        pass


def _audit_boundary_withheld_send(
    agent: Any,
    candidate: dict,
    records: list[dict],
) -> None:
    """Record that a refused payload was released with its offending units withheld.

    The audited content is the withheld payload, so the sampled excerpt cannot
    reintroduce what the boundary just refused. ``action`` stays within the
    existing vocabulary; the withholding detail rides in metadata so existing
    audit consumers keep parsing these records.
    """

    destination_type = (
        "internal_model_provider"
        if _is_local_model_endpoint(agent)
        else "model_provider"
    )
    data_classes = sorted({str(record.get("data_class") or "") for record in records})
    decision = EgressDecision(
        destination_type=destination_type,
        external=destination_type == "model_provider",
        likely_confidential=True,
        action="allow",
        reason=(
            "Sinria Boundary Control Layer withheld "
            f"{len(records)} refused unit(s) and released the remaining payload"
        ),
    )
    try:
        config = _load_sinria_egress_config(agent)
    except Exception:
        config = {}
    write_egress_audit(
        decision,
        _payload_to_text(dict(candidate)),
        audit_path=getattr(agent, "sinria_egress_audit_path", None),
        policy_profile=str(config.get("profile") or ""),
        metadata={
            "provider_key": _infer_boundary_provider_key(agent),
            "model": str(getattr(agent, "model", "") or ""),
            "boundary_withheld": {
                "count": len(records),
                "data_classes": data_classes,
                "units": records,
            },
        },
    )


def prepare_model_provider_payload(agent: Any, payload: dict) -> dict:
    """Redact then guard the exact payload immediately before provider egress.

    Guard/redaction failures are fail-closed and hide raw exception details.
    Reusing the same marked object is idempotent; transformed retry payloads are
    unmarked and therefore pass through the boundary again.

    A boundary refusal is not terminal on its own: the offending units are
    withheld from this outbound copy and the result is re-checked by the same
    policy. Local history keeps the originals, and a refusal that withholding
    cannot satisfy still fails closed.
    """

    if isinstance(payload, _PreparedModelProviderPayload):
        expected = getattr(payload, "_sinria_prepared_digest", None)
        if expected and expected == _prepared_payload_digest(payload):
            return payload
        # A caller mutated either the mapping or a nested value after the
        # boundary check. Drop the marker and run redaction + policy again.
        payload = dict(payload)

    try:
        prepared = _PreparedModelProviderPayload(
            redact_model_provider_payload(agent, payload)
        )
        try:
            guard_model_provider_egress(agent, prepared)
        except SinriaEgressBlocked as blocked:
            prepared = _withhold_and_reguard(agent, prepared, blocked)
        prepared._sinria_prepared_digest = _prepared_payload_digest(prepared)
        return prepared
    except SinriaEgressBlocked:
        raise
    except SinriaEgressGuardFailure:
        raise
    except Exception as exc:
        raise SinriaEgressGuardFailure() from exc


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
        # Moonshot (Kimi) direct API — untrusted external cloud. No "untrusted"
        # trust level exists, so model it as approved_cloud limited to the
        # ``public`` data class: PHI/PII (and internal/credential/classified)
        # are never approved for egress. Advisory under the default profile;
        # kept identical to the DEFAULT_CONFIG copy in hermes_cli/config.py.
        "kimi-coding": {
            "trust_level": "approved_cloud",
            "external_egress": True,
            "approved_data_classes": ["public"],
            "training_use": False,
            "retention": "provider_defined",
            "requires_sanitization": True,
        },
    },
}

_CLASSIFIED_RE = re.compile(r"(?:classified|top secret|secret//|機密区分|防衛秘密|特定秘密)", re.IGNORECASE)
_INTERNAL_RE = re.compile(r"(?:confidential|internal|社外秘|機密|契約書|取締役会|board memo)", re.IGNORECASE)
_RUNTIME_EXPLICIT_CLASSIFIED_RE = re.compile(
    r"(?:top secret|secret//|classification\s*[:：=]\s*classified\b|"
    r"機密区分\s*[:：=]|防衛秘密|特定秘密)",
    re.IGNORECASE,
)
_RUNTIME_CLASSIFIED_ARTIFACT_RE = re.compile(
    r"(?:_CLASSIFIED_RE|re\.compile|classifier\s+positives?|"
    r"classification\s+(?:regex|pattern)|data[_\s-]*class(?:es)?|"
    r"classified_signal|source_indicators|raw_content_included|"
    r"pytest|def\s+test_|tests?/test_)",
    re.IGNORECASE,
)
_RUNTIME_STANDALONE_DOCUMENT_MARKING_RE = re.compile(
    r"(?:^|[\r\n\"']|\\+n)\s*"
    r"(?:classified|top secret|secret//|機密区分|防衛秘密|特定秘密)"
    r"\s*(?=$|[\r\n]|\\+n)",
    re.IGNORECASE,
)
# `read_file` renders a file as `LINE_NUM|CONTENT`; a run of such lines frames
# source under review rather than a document Sinria is about to disclose.
_LISTING_LINE_RE = re.compile(r"^\s*(\d{1,7})\|")
_IDENTIFIER_CHAR_RE = re.compile(r"[A-Za-z0-9_]")
_REDACTED_PROVIDER_MARKER_RE = re.compile(
    r"(?:\[REDACTED(?:_[A-Z0-9_]+)?\]|<redacted>)",
    re.IGNORECASE,
)


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


def _is_metadata_only_classifier_diagnostic(content: str) -> tuple[bool, list[str]]:
    """Validate known classifier-diagnostic JSON shapes with no free-form body."""
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return False, []

    if isinstance(parsed, dict):
        allowed_wrapper = {"status", "output", "tool_calls_made", "duration_seconds", "error"}
        if not set(parsed).issubset(allowed_wrapper) or parsed.get("status") != "success":
            return False, []
        if parsed.get("error") not in {None, ""}:
            return False, []
        if "tool_calls_made" in parsed and not isinstance(parsed["tool_calls_made"], int):
            return False, []
        if "duration_seconds" in parsed and not isinstance(
            parsed["duration_seconds"], (int, float)
        ):
            return False, []
        parsed = parsed.get("output")
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except (TypeError, ValueError):
                return False, []

    if not isinstance(parsed, list) or not parsed:
        return False, []

    class_names = {"public", "internal", "phi_pii", "credential", "classified"}
    classified_terms = {
        "classified",
        "top secret",
        "secret//",
        "機密区分",
        "防衛秘密",
        "特定秘密",
    }
    file_labels: list[str] = []
    for row in parsed:
        if not isinstance(row, dict):
            return False, []
        keys = set(row)
        simple_keys = {
            "index",
            "role",
            "tool_name",
            "term",
            "artifact_context",
            "artifact_by_radius",
            "content_chars",
            "raw_content_included",
        }
        if keys.issubset(simple_keys) and "term" in row:
            if str(row.get("term") or "").lower() not in classified_terms:
                return False, []
            if row.get("raw_content_included") is not False:
                return False, []
            if "index" in row and not isinstance(row["index"], int):
                return False, []
            if "content_chars" in row and not isinstance(row["content_chars"], int):
                return False, []
            if "artifact_context" in row and not isinstance(row["artifact_context"], bool):
                return False, []
            if "role" in row and row["role"] not in {
                "system",
                "user",
                "assistant",
                "tool",
            }:
                return False, []
            if "tool_name" in row and (
                not isinstance(row["tool_name"], str)
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", row["tool_name"])
            ):
                return False, []
            radii = row.get("artifact_by_radius", {})
            if not isinstance(radii, dict) or not all(
                isinstance(key, str)
                and key.isdigit()
                and len(key) <= 6
                and isinstance(value, bool)
                for key, value in radii.items()
            ):
                return False, []
            continue

        aggregate_keys = {
            "all_class",
            "file",
            "message_count",
            "non_system_class",
            "role_signals",
        }
        if keys != aggregate_keys:
            return False, []
        if row.get("all_class") not in class_names or row.get("non_system_class") not in class_names:
            return False, []
        if not isinstance(row.get("message_count"), int) or row["message_count"] < 0:
            return False, []
        file_label = row.get("file")
        if not isinstance(file_label, str) or len(file_label) > 512 or "\n" in file_label:
            return False, []
        role_signals = row.get("role_signals")
        if not isinstance(role_signals, dict) or not set(role_signals).issubset(
            {"system", "user", "assistant", "tool"}
        ):
            return False, []
        expected_signal_keys = {
            "messages",
            "classified_terms",
            "concrete_secret_messages",
            "real_patient_id_messages",
            "internal_signal_messages",
        }
        for signals in role_signals.values():
            if not isinstance(signals, dict) or set(signals) != expected_signal_keys:
                return False, []
            for key in expected_signal_keys - {"classified_terms"}:
                if not isinstance(signals[key], int) or signals[key] < 0:
                    return False, []
            terms = signals["classified_terms"]
            if not isinstance(terms, dict) or not all(
                str(term).lower() in classified_terms
                and isinstance(count, int)
                and count >= 0
                for term, count in terms.items()
            ):
                return False, []
        file_labels.append(file_label)

    return True, file_labels


def _looks_like_unvalidated_classifier_diagnostic(content: str) -> bool:
    """Detect classifier-shaped JSON that failed the strict metadata schema."""
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return False

    if isinstance(parsed, dict) and "output" in parsed:
        parsed = parsed.get("output")
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except (TypeError, ValueError):
                return False
    if not isinstance(parsed, list):
        return False

    diagnostic_keys = {
        "term",
        "artifact_context",
        "artifact_by_radius",
        "raw_content_included",
        "all_class",
        "non_system_class",
        "role_signals",
    }
    return any(
        isinstance(row, dict) and bool(set(row) & diagnostic_keys)
        for row in parsed
    ) and bool(_CLASSIFIED_RE.search(content))


def _line_numbered_listing_lines(text: str) -> list[str] | None:
    """Lines of a ``read_file`` listing (``LINE_NUM|CONTENT``), else None.

    Every populated line must carry the prefix and the numbers must walk
    forward, so a document body cannot reach the source-listing exemption by
    prefixing a single line.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    numbers: list[int] = []
    bodies: list[str] = []
    for line in lines:
        match = _LISTING_LINE_RE.match(line)
        if not match:
            return None
        numbers.append(int(match.group(1)))
        bodies.append(line[match.end():])
    if any(later < earlier for earlier, later in zip(numbers, numbers[1:])):
        return None
    return bodies


def _source_listing_lines(text: str) -> list[str] | None:
    """Listed source lines carried by a file-reading tool result, else None.

    Recognizes the two envelopes Sinria's file tools emit: ``read_file``'s
    ``{"content": "LINE_NUM|..."}`` and ``search_files``'s
    ``{"matches": [{"path", "line", "content"}]}``. A bare listing is accepted
    too. Anything else — a ``write_file`` body, ``execute_code`` source, a
    pasted document — is not a listing and keeps full strictness.
    """
    if not text:
        return None
    try:
        parsed, _end = json.JSONDecoder().raw_decode(text.lstrip())
    except (TypeError, ValueError):
        return _line_numbered_listing_lines(text)
    if not isinstance(parsed, dict):
        return None

    matches = parsed.get("matches")
    if isinstance(matches, list) and matches:
        lines: list[str] = []
        for row in matches:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("path"), str)
                or not isinstance(row.get("line"), int)
                or isinstance(row.get("line"), bool)
                or not isinstance(row.get("content"), str)
            ):
                return None
            lines.append(row["content"])
        return lines

    content = parsed.get("content")
    if isinstance(content, str):
        return _line_numbered_listing_lines(content)
    return None


def _marking_is_source_token(line: str, match: re.Match[str]) -> bool:
    """Whether a marking is code on its listed line rather than document text.

    Two forms qualify: a quoted literal (``"top secret",`` in a term table) and
    a fragment of a larger identifier (``classified_terms``, ``_CLASSIFIED_RE``).
    A marking standing on its own in prose or on its own line qualifies as
    neither, so it keeps its original classification power.
    """
    before, after = line[: match.start()], line[match.end():]
    if any(quote in before and quote in after for quote in "\"'`"):
        return True
    return bool(
        (before[-1:] and _IDENTIFIER_CHAR_RE.match(before[-1:]))
        or (after[:1] and _IDENTIFIER_CHAR_RE.match(after[:1]))
    )


def _source_listing_classification_view(text: str) -> str:
    """Reduce a file listing to its source lines, neutralizing code vocabulary.

    Sinria's guard spells the classification vocabulary out in its own source,
    so reading or grepping that source classified the whole outbound payload as
    ``classified`` — permanently stranding the session, because the poisoned
    history is replayed on every turn.

    The exemption is deliberately narrow and per-occurrence: only markings that
    are demonstrably code on their own listed line are neutralized, so a bare
    document marking inside a listing still classifies as ``classified``.
    Envelope metadata (paths, hints, counts) is tool-generated and drops out of
    the view. Credentials and patient identifiers are never neutralized here — a
    listing containing them still classifies as ``credential``/``phi_pii``.
    """
    if not text or not _CLASSIFIED_RE.search(text):
        # Nothing to neutralize, so leave the text — and everything else that
        # classifies from it — exactly as it was.
        return text
    lines = _source_listing_lines(text)
    if lines is None:
        return text
    return "\n".join(_neutralize_source_listing_line(line) for line in lines)


def _neutralize_source_listing_line(line: str) -> str:
    return _CLASSIFIED_RE.sub(
        lambda match: (
            "classification-term" if _marking_is_source_token(line, match) else match.group(0)
        ),
        line,
    )


def _classification_view_tool_arguments(name: Any, arguments: Any) -> Any:
    """Neutralize classifier vocabulary only in known tool plumbing fields."""
    if not isinstance(name, str) or not isinstance(arguments, str):
        return arguments
    try:
        parsed = json.loads(arguments)
    except (TypeError, ValueError):
        return arguments
    if not isinstance(parsed, dict):
        return arguments

    if name == "search_files" and isinstance(parsed.get("pattern"), str):
        parsed = dict(parsed)
        parsed["pattern"] = _CLASSIFIED_RE.sub(
            "classification-term", parsed["pattern"]
        )
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True)

    return arguments


def _runtime_classification_view(value: Any) -> Any:
    """Replace strictly validated tool diagnostics with a body-free summary."""
    if isinstance(value, str):
        # Payload shapes differ per API (`messages` vs Responses `input`), so
        # listings are recognized by their own envelope rather than by the key
        # that happens to carry them.
        return _source_listing_classification_view(value)
    if isinstance(value, list):
        return [_runtime_classification_view(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_runtime_classification_view(item) for item in value)
    if not isinstance(value, dict):
        return value

    function = value.get("function")
    if isinstance(function, dict):
        function_name = function.get("name")
        function = dict(function)
        function["arguments"] = _classification_view_tool_arguments(
            function_name,
            function.get("arguments"),
        )
        value = {**value, "function": function}

    if (
        value.get("role") == "tool"
        and value.get("name") == "execute_code"
        and isinstance(value.get("content"), str)
    ):
        valid, file_labels = _is_metadata_only_classifier_diagnostic(value["content"])
        if valid:
            summary = "metadata-only classifier diagnostic"
            if file_labels:
                summary += " files=" + ",".join(file_labels)
            return {
                key: summary if key == "content" else _runtime_classification_view(item)
                for key, item in value.items()
            }
        if _looks_like_unvalidated_classifier_diagnostic(value["content"]):
            # This sentinel exists only in the private classification view; the
            # local tool result remains intact. Malformed/free-form fields must
            # fail closed rather than inherit the source-code exemption below.
            return {
                key: (
                    "unvalidated classifier diagnostic\nTOP SECRET\n"
                    if key == "content"
                    else _runtime_classification_view(item)
                )
                for key, item in value.items()
            }

    return {key: _runtime_classification_view(item) for key, item in value.items()}


def _has_explicit_runtime_classification_marking(text: str) -> bool:
    """Return True for document markings, not classifier/source vocabulary."""
    # Standalone markings remain authoritative even beside pytest/classifier
    # vocabulary; otherwise appending one artifact word bypasses the boundary.
    if _RUNTIME_STANDALONE_DOCUMENT_MARKING_RE.search(text or ""):
        return True
    for match in _RUNTIME_EXPLICIT_CLASSIFIED_RE.finditer(text or ""):
        window = text[max(0, match.start() - 180): min(len(text), match.end() + 180)]
        if _RUNTIME_CLASSIFIED_ARTIFACT_RE.search(window):
            continue
        return True
    return False


def classify_runtime_model_data_class(
    content: Any,
    *,
    payload_is_redacted: bool = False,
) -> str:
    """Classify the exact runtime payload without poisoning on control text.

    Product/report previews intentionally classify broad clinical and security
    terms conservatively. Runtime prompts always contain policy instructions,
    loaded skills, and source/test output, so vocabulary alone is not sensitive
    data. Concrete identifiers, credentials, and explicit classification
    markings remain strict. A process-local prepared payload may additionally
    treat Sinria-generated redaction markers as sanitized placeholders.
    """
    runtime_view = _runtime_classification_view(content)
    text = (
        _payload_to_text(runtime_view)
        if not isinstance(runtime_view, str)
        else runtime_view
    )
    classification_text = (
        _REDACTED_PROVIDER_MARKER_RE.sub("sanitized", text)
        if payload_is_redacted
        else text
    )

    if _has_explicit_runtime_classification_marking(classification_text):
        return "classified"
    if looks_like_concrete_secret(classification_text):
        if _has_real_patient_identifier(classification_text):
            return "phi_pii"
        return "credential"

    data_class = classify_sinria_data_class(classification_text)
    if data_class == "classified":
        # Bare policy/source vocabulary is internal, not classified material.
        return "internal"
    if data_class != "phi_pii":
        return data_class
    if _PATIENT_ID_RE.search(classification_text) or _INLINE_PATIENT_ID_RE.search(
        classification_text
    ):
        return "phi_pii"
    if not payload_is_redacted and _PATIENT_ID_PLACEHOLDER_RE.search(
        classification_text
    ):
        return "phi_pii"
    return "internal"


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
    data_class_override: str | None = None,
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
    data_class = (
        data_class_override
        if data_class_override
        in {"public", "internal", "phi_pii", "credential", "classified"}
        else classify_sinria_data_class(payload)
    )
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
            "reason": BOUNDARY_PROVIDER_NOT_REGISTERED_REASON,
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
    if explicit is False:
        return None
    if isinstance(explicit, dict):
        return explicit
    try:
        from hermes_cli.config import load_config

        loaded = load_config() or {}
    except Exception as exc:
        if _is_local_model_endpoint(agent):
            return None
        raise SinriaEgressGuardFailure("boundary policy load") from exc
    if not isinstance(loaded, dict):
        return None
    sinria = loaded.get("sinria")
    boundary = sinria.get("boundary_control") if isinstance(sinria, dict) else None
    return loaded if isinstance(boundary, dict) else None


def _infer_boundary_provider_key(agent: Any) -> str:
    explicit = str(getattr(agent, "sinria_provider_key", "") or "").strip()
    if explicit:
        return explicit
    provider = str(getattr(agent, "provider", "") or "").strip().lower()
    if _is_local_model_endpoint(agent):
        return "local_vllm"
    # A remote custom endpoint has no boundary identity unless the caller
    # supplied one after matching the concrete endpoint against operator-owned
    # configuration. Never let a generic ``custom`` registry entry confer
    # trust on every arbitrary OpenAI-compatible URL.
    if provider == "custom" or provider.startswith("custom:"):
        return ""
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
    provider_name = str(getattr(agent, "provider", "") or "").strip().lower()
    unresolved_custom_provider = (
        not provider_key
        and (provider_name == "custom" or provider_name.startswith("custom:"))
        and destination_type == "model_provider"
    )
    boundary_metadata: dict | None = None
    if isinstance(boundary_config, dict) and (provider_key or unresolved_custom_provider):
        runtime_data_class = classify_runtime_model_data_class(
            messages,
            payload_is_redacted=isinstance(messages, _PreparedModelProviderPayload),
        )
        route = route_model_provider_for_payload(
            messages,
            provider_key=provider_key,
            config=boundary_config,
            data_class_override=runtime_data_class,
        )
        resolved_boundary = resolve_sinria_boundary_control(boundary_config)
        provider_registry = resolved_boundary.get("provider_trust_registry")
        provider_policy = (
            provider_registry.get(provider_key)
            if isinstance(provider_registry, dict)
            else None
        )
        if (
            isinstance(provider_policy, dict)
            and provider_policy.get("trust_level") == "local_only"
            and destination_type == "model_provider"
        ):
            route = dict(route)
            route.update(
                {
                    "allowed": False,
                    "required_model_route": "local_only",
                    "reason": "local-only provider is bound to a non-local endpoint",
                }
            )
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
            raise SinriaEgressBlocked(decision, metadata=boundary_metadata)
        if destination_type != "model_provider":
            return EgressDecision(
                destination_type=destination_type,
                external=False,
                likely_confidential=route.get("data_class") != "public",
                action="allow",
                reason=f"Sinria Boundary Control Layer allowed local model-provider route: {route.get('reason')}",
            )
    decision = classify_external_egress(destination_type, content, config)
    return _enforce_external_decision(
        decision,
        content,
        audit_path=audit_path,
        config=config,
        audit_metadata=boundary_metadata,
    )


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
