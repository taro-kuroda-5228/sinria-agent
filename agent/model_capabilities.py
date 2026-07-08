"""Model capability contract — architecture-centric model-tier resolution.

Sinria's architecture-centric direction (see
``docs/plans/2026-07-06-architecture-centric-agent-os-p0.md``) requires the
runtime to adapt its scaffolding to the model actually being served: an
8K-context laptop GGUF and a 1M-context frontier model should both get a
working agent, with iteration caps and prompt-injection budgets scaled to
fit instead of assuming frontier capabilities everywhere.

This module is the single place that maps a resolved context length to the
knobs the rest of the runtime reads:

* ``tier`` — ``"small"`` / ``"medium"`` / ``"large"``
* ``max_iterations_cap`` — small models cannot sustain 90 tool-calling
  iterations inside their window
* per-block character budgets for volatile system-prompt injection
  (memory snapshot, user profile, Context Share resolver block)

Budgets of ``0`` mean *unlimited* — the ``large`` tier keeps today's
behavior byte-identical, which also keeps the prompt-caching invariant
intact for frontier models.

Pure functions only: no config reads, no I/O, no runtime imports.  Callers
(``agent/agent_init.py``, ``agent/system_prompt.py``) pass resolved values
in and attach the profile to the agent at construction time so every
derived prompt stays deterministic for the session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Absolute floor: below this even scaled scaffolding cannot sustain the
# tool-calling loop — identity + tool schemas + one exchange already
# approach this size.
SMALL_CONTEXT_FLOOR = 8_000

TIER_SMALL_MAX = 32_000  # ctx < 32K  -> "small"
TIER_MEDIUM_MAX = 100_000  # ctx < 100K -> "medium"; else "large"

_TIER_MAX_ITERATIONS = {"small": 30, "medium": 60, "large": 90}

# Character budgets (~4 chars/token) for volatile system-prompt blocks.
# 0 = unlimited.
_TIER_CHAR_BUDGETS = {
    "small": {"memory": 4_000, "user_profile": 2_000, "resolver": 3_000},
    "medium": {"memory": 12_000, "user_profile": 4_000, "resolver": 6_000},
    "large": {"memory": 0, "user_profile": 0, "resolver": 0},
}

# Injected into the stable prompt tier for small-tier models (fixed at
# agent init, so the cached system prompt stays stable across turns).
SMALL_CONTEXT_OPERATIONS_GUIDANCE = (
    "Small-context mode: this model's context window is limited. Work in "
    "small, verifiable steps and keep outputs terse. Prefer calling the "
    "recall_context tool to retrieve prior corrections and memory on "
    "demand instead of relying on long injected context, and re-read "
    "files instead of assuming earlier content is still in context."
)


@dataclass(frozen=True)
class ModelCapabilityProfile:
    """Resolved capability contract for the model serving this agent."""

    context_length: int  # 0 = unknown
    tier: str  # "small" | "medium" | "large"
    max_iterations_cap: int
    memory_char_budget: int  # 0 = unlimited
    user_profile_char_budget: int  # 0 = unlimited
    resolver_char_budget: int  # 0 = unlimited


def tier_for_context_length(context_length: Optional[int]) -> str:
    """Map a context length to a capability tier.

    Unknown (``None``/``0``) resolves to ``"large"`` — detection failures
    keep today's frontier-model behavior rather than degrading a capable
    model.
    """
    if not context_length or context_length <= 0:
        return "large"
    if context_length < TIER_SMALL_MAX:
        return "small"
    if context_length < TIER_MEDIUM_MAX:
        return "medium"
    return "large"


def resolve_capability_profile(context_length: Optional[int]) -> ModelCapabilityProfile:
    """Resolve the capability profile for a context length."""
    tier = tier_for_context_length(context_length)
    budgets = _TIER_CHAR_BUDGETS[tier]
    return ModelCapabilityProfile(
        context_length=int(context_length or 0),
        tier=tier,
        max_iterations_cap=_TIER_MAX_ITERATIONS[tier],
        memory_char_budget=budgets["memory"],
        user_profile_char_budget=budgets["user_profile"],
        resolver_char_budget=budgets["resolver"],
    )


def validate_context_length(
    context_length: Optional[int],
    *,
    small_context_mode: str,
    model: str,
    minimum_context_length: int,
) -> Optional[str]:
    """Gate a resolved context length.

    Returns a warning string (small-context mode engaged), ``None`` when
    nothing needs saying, or raises ``ValueError`` when the model cannot
    run at all (below the absolute floor, or below the recommended
    minimum with ``model.small_context_mode: strict``).
    """
    if not context_length:
        return None
    if context_length < SMALL_CONTEXT_FLOOR:
        raise ValueError(
            f"Model {model} has a context window of {context_length:,} tokens, "
            f"below the absolute floor of {SMALL_CONTEXT_FLOOR:,} Sinria needs "
            f"for its tool-calling loop. Choose a larger-context model, or set "
            f"model.context_length in config.yaml if detection is wrong."
        )
    if context_length >= minimum_context_length:
        return None
    if (small_context_mode or "auto").strip().lower() == "strict":
        raise ValueError(
            f"Model {model} has a context window of {context_length:,} tokens, "
            f"which is below the minimum {minimum_context_length:,} required "
            f"by Sinria in strict mode. Choose a model with at least "
            f"{minimum_context_length // 1000}K context, set "
            f"model.context_length in config.yaml to override detection, or "
            f"set model.small_context_mode: auto to run with scaled budgets."
        )
    tier = tier_for_context_length(context_length)
    return (
        f"Model {model} context window ({context_length:,} tokens) is below "
        f"the recommended {minimum_context_length:,}; running in "
        f"small-context mode (tier={tier}) with a scaled iteration cap and "
        f"prompt budgets. Use the recall_context tool for older context."
    )


def apply_char_budget(text: str, budget: int, label: str) -> str:
    """Truncate ``text`` to ``budget`` characters (0 = unlimited).

    Appends a marker pointing at the ``recall_context`` tool so the model
    knows the omitted content is retrievable on demand.
    """
    if not text or budget <= 0 or len(text) <= budget:
        return text
    return text[:budget].rstrip() + (
        f"\n… [{label} truncated to fit the model's context budget — use "
        f"the recall_context tool to retrieve older items on demand]"
    )


__all__ = [
    "SMALL_CONTEXT_FLOOR",
    "SMALL_CONTEXT_OPERATIONS_GUIDANCE",
    "ModelCapabilityProfile",
    "apply_char_budget",
    "resolve_capability_profile",
    "tier_for_context_length",
    "validate_context_length",
]
