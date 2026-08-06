"""Runtime orchestrator for the Core Autonomy Kernel."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Optional

from .models import ActionReceipt, ActionRequest, Decision
from .policy import evaluate_request
from .store import AutonomyStore


class CoreAutonomyRuntime:
    """Execute action requests with policy checks, callbacks, and durable accounting."""

    def __init__(self, store: Optional[AutonomyStore] = None) -> None:
        self.store = store or AutonomyStore()

    @staticmethod
    def _normalize_readback(result: Any) -> str:
        if result is None:
            return "ambiguous"
        if isinstance(result, bool):
            return "confirmed" if result else "failed"

        if isinstance(result, str):
            normalized = result.strip().lower()
            if normalized in {"confirmed", "ok", "success", "completed", "acknowledged"}:
                return "confirmed"
            if normalized in {"ambiguous", "unknown", "unverified", "pending"}:
                return "ambiguous"
            if normalized in {"failed", "error", "denied"}:
                return "failed"

        if isinstance(result, dict):
            if "status" in result and isinstance(result["status"], str):
                return CoreAutonomyRuntime._normalize_readback(result["status"])
            if "ok" in result:
                return "confirmed" if bool(result["ok"]) else "failed"

        return "ambiguous"

    def execute(
        self,
        request: ActionRequest,
        execute_callback: Callable[[ActionRequest], Any],
        readback_callback: Optional[Callable[[ActionRequest, Any], Any]] = None,
        *,
        kill_switch: bool = False,
    ) -> ActionReceipt:
        # idempotency: if we saw this id before, return the same decision/result
        existing = self.store.get_receipt(request.request_id)
        if existing is not None:
            return replace(
                existing,
                idempotent=True,
                readback="ambiguous" if existing.readback == "ambiguous" else existing.readback,
            )

        decision = evaluate_request(
            request,
            kill_switch=kill_switch or self.store.is_killed(request),
            grant_usage=self.store.get_grant_usage(),
        )

        base_receipt = ActionReceipt(request_id=request.request_id, decision=decision)

        if decision.outcome in {"block", "ask"}:
            self.store.record_receipt(base_receipt)
            return base_receipt

        # allow path
        try:
            result = execute_callback(request)
        except Exception as err:  # pragma: no cover - defensive against user-supplied callback failures
            failed_receipt = base_receipt.with_defaults(
                executed=False,
                readback="failed",
                error=f"{type(err).__name__}: {err}",
            )
            self.store.record_receipt(failed_receipt)
            return failed_receipt

        # mark usage only when execution callback is reached
        if decision.grant_id:
            self.store.consume_limit(decision.grant_id, request.action, request.scope)

        if readback_callback is None:
            ambiguous_receipt = base_receipt.with_defaults(
                executed=True,
                readback="ambiguous",
                result=result,
                error="provider_readback_required",
            )
            self.store.record_execution(ambiguous_receipt, request)
            return ambiguous_receipt

        try:
            readback = readback_callback(request, result)
        except Exception as err:  # pragma: no cover
            confirmed = base_receipt.with_defaults(
                executed=True,
                readback="ambiguous",
                result=result,
                error=f"{type(err).__name__}: {err}",
            )
            self.store.record_execution(confirmed, request)
            return confirmed

        normalized = self._normalize_readback(readback)
        final_receipt = ActionReceipt(
            request_id=base_receipt.request_id,
            decision=base_receipt.decision,
            executed=True,
            readback=normalized,
            result=result,
            error=base_receipt.error,
            created_at=base_receipt.created_at,
        )

        self.store.record_execution(final_receipt, request)
        return final_receipt
