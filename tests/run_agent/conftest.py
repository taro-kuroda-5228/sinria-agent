"""Fast-path fixtures shared across tests/run_agent/.

Many tests in this directory exercise the retry/backoff paths in the
agent loop. Production code uses ``jittered_backoff(base_delay=5.0)``
with a ``while time.time() < sleep_end`` loop — a single retry test
spends 5+ seconds of real wall-clock time on backoff waits.

Mocking ``jittered_backoff`` to return 0.0 collapses the while-loop
to a no-op (``time.time() < time.time() + 0`` is false immediately),
which handles the most common case without touching ``time.sleep``.

We deliberately DO NOT mock ``time.sleep`` here — some tests
(test_interrupt_propagation, test_primary_runtime_restore, etc.) use
the real ``time.sleep`` for threading coordination or assert that it
was called with specific values. Tests that want to additionally
fast-path direct ``time.sleep(N)`` calls in production code should
monkeypatch ``run_agent.time.sleep`` locally (see
``test_anthropic_error_handling.py`` for the pattern).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fast_retry_backoff(monkeypatch):
    """Short-circuit retry backoff for all tests in this directory."""
    try:
        import run_agent
    except ImportError:
        return

    monkeypatch.setattr(run_agent, "jittered_backoff", lambda *a, **k: 0.0)


@pytest.fixture(autouse=True)
def _clear_hard_usage_limit_state():
    """Each run_agent test should start from a clean hard-quota gate state."""
    from agent.provider_quota_guard import clear_hard_usage_limits

    clear_hard_usage_limits()
    yield
    clear_hard_usage_limits()


@pytest.fixture(autouse=True)
def _isolate_model_provider_boundary(monkeypatch):
    """Keep provider unit tests independent of the production trust registry.

    Tests in this directory mock transport and never perform real network I/O.
    Boundary Control behavior is covered by dedicated integration tests outside
    this directory. A test that needs the real resolver can monkeypatch it again
    locally after this fixture is applied.
    """
    from agent import sinria_egress

    monkeypatch.setattr(
        sinria_egress,
        "_load_sinria_boundary_config",
        lambda _agent: None,
    )
