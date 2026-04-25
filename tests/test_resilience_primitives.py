"""Testes das primitivas de resiliencia — Sessao 6 / #54.

Cobre CircuitBreaker, RateLimiter, CostQuota — primitivas centrais que
NAO substituem o retry per-module (narrator/transcriber/visual_analyzer
mantem o seu) mas adicionam camada cross-module.
"""

from __future__ import annotations

import time

import pytest

from rdo_agent.observability import (
    CircuitBreaker,
    CircuitOpenError,
    CostQuota,
    QuotaExceededError,
    RateLimiter,
)
from rdo_agent.observability.resilience import (
    get_anthropic_circuit,
    get_anthropic_rate_limiter,
    get_openai_circuit,
    get_openai_rate_limiter,
    reset_singletons_for_test,
)


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class _BoomError(RuntimeError):
    pass


def _ok() -> str:
    return "ok"


def _boom() -> str:
    raise _BoomError("simulated failure")


def test_circuit_breaker_starts_closed():
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout_sec=10)
    assert cb.state == "CLOSED"
    assert cb.consecutive_failures == 0


def test_circuit_breaker_passes_through_when_closed():
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout_sec=10)
    assert cb.call(_ok) == "ok"
    assert cb.state == "CLOSED"


def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout_sec=10)
    for _ in range(3):
        with pytest.raises(_BoomError):
            cb.call(_boom)
    assert cb.state == "OPEN"
    assert cb.consecutive_failures == 3


def test_circuit_breaker_blocks_calls_when_open():
    cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout_sec=60)
    for _ in range(2):
        with pytest.raises(_BoomError):
            cb.call(_boom)
    # Agora qualquer call eh bloqueado sem chegar a executar
    with pytest.raises(CircuitOpenError, match="test"):
        cb.call(_ok)


def test_circuit_breaker_half_open_after_timeout():
    cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout_sec=0.05)
    for _ in range(2):
        with pytest.raises(_BoomError):
            cb.call(_boom)
    assert cb.state == "OPEN"
    time.sleep(0.06)  # passa do timeout
    # proxima chamada eh teste — se passar, fecha
    assert cb.call(_ok) == "ok"
    assert cb.state == "CLOSED"


def test_circuit_breaker_half_open_failure_reopens():
    cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout_sec=0.05)
    for _ in range(2):
        with pytest.raises(_BoomError):
            cb.call(_boom)
    time.sleep(0.06)
    # tentativa de recovery falha → volta para OPEN
    with pytest.raises(_BoomError):
        cb.call(_boom)
    assert cb.state == "OPEN"


def test_circuit_breaker_closes_after_success_when_half_open():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout_sec=0.02)
    with pytest.raises(_BoomError):
        cb.call(_boom)
    time.sleep(0.03)
    assert cb.call(_ok) == "ok"
    assert cb.state == "CLOSED"
    # consecutive_failures volta a 0
    assert cb.consecutive_failures == 0


def test_circuit_breaker_reset():
    cb = CircuitBreaker("test", failure_threshold=2)
    for _ in range(2):
        with pytest.raises(_BoomError):
            cb.call(_boom)
    cb.reset()
    assert cb.state == "CLOSED"
    assert cb.consecutive_failures == 0


def test_circuit_breaker_env_override(monkeypatch):
    monkeypatch.setenv("RDO_AGENT_CIRCUIT_FAILURE_THRESHOLD", "10")
    monkeypatch.setenv("RDO_AGENT_CIRCUIT_RECOVERY_SEC", "120")
    cb = CircuitBreaker("test_env")
    assert cb.failure_threshold == 10
    assert cb.recovery_timeout_sec == 120


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


def test_rate_limiter_passes_first_calls_within_budget():
    rl = RateLimiter("test", rate_per_min=600)  # 10/sec — gentil
    start = time.time()
    for _ in range(5):
        rl.acquire()
    elapsed = time.time() - start
    # Tokens iniciais permitem 5 chamadas instantaneas
    assert elapsed < 0.1


def test_rate_limiter_throttles_when_burst_exceeds_capacity():
    rl = RateLimiter("test", rate_per_min=120)  # 2/sec
    rl.tokens = 0  # forca esvaziar bucket
    rl.last_refill = time.time()
    start = time.time()
    rl.acquire()
    elapsed = time.time() - start
    # Para 1 token a 2/sec, espera ~0.5s
    assert elapsed >= 0.4


def test_rate_limiter_refills_over_time():
    rl = RateLimiter("test", rate_per_min=600)
    rl.tokens = 0
    rl.last_refill = time.time() - 0.2  # 0.2s atras
    rl._refill()
    # 600/min = 10/sec; 0.2s = 2 tokens
    assert rl.tokens >= 2


def test_rate_limiter_cap_at_max():
    """Tokens não acumulam acima de rate_per_min mesmo com idle longo."""
    rl = RateLimiter("test", rate_per_min=10)
    rl.tokens = 0
    rl.last_refill = time.time() - 1000  # MUITO tempo
    rl._refill()
    assert rl.tokens == 10  # cap respeitado


def test_rate_limiter_invalid_rate():
    with pytest.raises(ValueError, match=">"):
        RateLimiter("test", rate_per_min=0)
    with pytest.raises(ValueError, match=">"):
        RateLimiter("test", rate_per_min=-5)


# ---------------------------------------------------------------------------
# CostQuota
# ---------------------------------------------------------------------------


def test_cost_quota_passes_when_under_limit():
    q = CostQuota("CASE_X", daily_max_usd=10.0)
    q.check_or_raise(5.0)  # nao raise
    q.check_or_raise(9.99)  # nao raise


def test_cost_quota_raises_at_limit():
    q = CostQuota("CASE_X", daily_max_usd=10.0)
    with pytest.raises(QuotaExceededError, match="CASE_X"):
        q.check_or_raise(10.01)


def test_cost_quota_env_default(monkeypatch):
    monkeypatch.setenv("RDO_AGENT_DAILY_QUOTA_USD", "50.0")
    q = CostQuota("CASE_X")
    assert q.daily_max_usd == 50.0


def test_cost_quota_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("RDO_AGENT_DAILY_QUOTA_USD", "50.0")
    q = CostQuota("CASE_X", daily_max_usd=200.0)
    assert q.daily_max_usd == 200.0


# ---------------------------------------------------------------------------
# Singletons cross-module
# ---------------------------------------------------------------------------


def test_singletons_return_same_instance():
    reset_singletons_for_test()
    a1 = get_openai_circuit()
    a2 = get_openai_circuit()
    assert a1 is a2

    b1 = get_anthropic_circuit()
    b2 = get_anthropic_circuit()
    assert b1 is b2

    # Mas openai != anthropic
    assert a1 is not b1


def test_singletons_can_be_reset_for_test():
    reset_singletons_for_test()
    cb1 = get_openai_circuit()
    reset_singletons_for_test()
    cb2 = get_openai_circuit()
    assert cb1 is not cb2  # nova instancia apos reset


def test_singleton_rate_limiters_independent():
    reset_singletons_for_test()
    a = get_openai_rate_limiter()
    b = get_anthropic_rate_limiter()
    assert a is not b
    assert a.name == "openai"
    assert b.name == "anthropic"
