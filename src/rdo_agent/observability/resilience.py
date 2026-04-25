"""
Resilience primitives — Sessão 6 (dívida #54).

Centraliza **circuit breaker, rate limiter e cost quota** sem
duplicar o retry per-module que já existe (narrator/transcriber/
visual_analyzer mantêm seu retry com classificação de erro).

Configuração via env vars (todas opcionais; defaults conservadores):

- ``RDO_AGENT_CIRCUIT_FAILURE_THRESHOLD`` (default: 5)
- ``RDO_AGENT_CIRCUIT_RECOVERY_SEC``     (default: 300)
- ``RDO_AGENT_RATE_LIMIT_OPENAI_PER_MIN`` (default: 60)
- ``RDO_AGENT_RATE_LIMIT_ANTHROPIC_PER_MIN`` (default: 20)
- ``RDO_AGENT_DAILY_QUOTA_USD`` (default: 100.0)
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        log.warning("env %s invalido (%r); usando default %d", name, val, default)
        return default


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        log.warning("env %s invalido (%r); usando default %.2f", name, val, default)
        return default


# ----------------------------------------------------------------------
# Circuit Breaker
# ----------------------------------------------------------------------


class CircuitOpenError(RuntimeError):
    """Levantado quando o circuit está OPEN e a chamada é rejeitada."""


class CircuitBreaker:
    """
    Circuit breaker em 3 estados (CLOSED → OPEN → HALF_OPEN → CLOSED).

    - **CLOSED**: chamadas passam normalmente. Conta falhas
      consecutivas. Ao atingir ``failure_threshold``, vai para OPEN.
    - **OPEN**: chamadas são bloqueadas com ``CircuitOpenError`` por
      ``recovery_timeout_sec`` segundos.
    - **HALF_OPEN**: após o timeout, próxima chamada é tentativa de
      teste. Se passar, volta para CLOSED. Se falhar, volta para OPEN
      (timer reinicia).
    """

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int | None = None,
        recovery_timeout_sec: float | None = None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold or _env_int(
            "RDO_AGENT_CIRCUIT_FAILURE_THRESHOLD", 5,
        )
        self.recovery_timeout_sec = float(
            recovery_timeout_sec
            if recovery_timeout_sec is not None
            else _env_int("RDO_AGENT_CIRCUIT_RECOVERY_SEC", 300)
        )
        self.state = "CLOSED"
        self.consecutive_failures = 0
        self.last_failure_time: float | None = None

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if self.state == "OPEN":
            assert self.last_failure_time is not None
            if (time.time() - self.last_failure_time) > self.recovery_timeout_sec:
                self.state = "HALF_OPEN"
                log.info(
                    "circuit %s: OPEN -> HALF_OPEN (timeout expirou)",
                    self.name,
                )
            else:
                raise CircuitOpenError(
                    f"circuit '{self.name}' OPEN; "
                    f"retry em {self._seconds_until_recovery():.0f}s"
                )

        try:
            result = func(*args, **kwargs)
        except Exception:
            self._on_failure()
            raise
        else:
            self._on_success()
            return result

    def _on_success(self) -> None:
        was = self.state
        if self.state == "HALF_OPEN":
            self.state = "CLOSED"
            log.info(
                "circuit %s: %s -> CLOSED (recovery confirmado)",
                self.name, was,
            )
        self.consecutive_failures = 0

    def _on_failure(self) -> None:
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        if (
            self.state == "HALF_OPEN"
            or self.consecutive_failures >= self.failure_threshold
        ):
            was = self.state
            self.state = "OPEN"
            if was != "OPEN":
                log.warning(
                    "circuit %s: %s -> OPEN (consecutive_failures=%d)",
                    self.name, was, self.consecutive_failures,
                )

    def _seconds_until_recovery(self) -> float:
        if self.last_failure_time is None:
            return 0.0
        elapsed = time.time() - self.last_failure_time
        return max(0.0, self.recovery_timeout_sec - elapsed)

    def reset(self) -> None:
        """Forçar volta para CLOSED. Útil em testes ou intervenção manual."""
        self.state = "CLOSED"
        self.consecutive_failures = 0
        self.last_failure_time = None


# ----------------------------------------------------------------------
# Rate Limiter (token bucket)
# ----------------------------------------------------------------------


class RateLimiter:
    """
    Token bucket simples por minuto. ``acquire()`` bloqueia
    (``time.sleep``) até um token estar disponível.

    Não pretende ser super-preciso (sem jitter, sem distributed
    coordination) — apenas evita flood acidental contra a API. Se
    rate cap for muito apertado e o caller for chamado em paralelo,
    a coordenação fica como exercício futuro.
    """

    def __init__(self, name: str, rate_per_min: int | None = None):
        self.name = name
        # Default 60/min se param for None. Não usar truthiness aqui
        # porque 0 é valor explícito inválido.
        if rate_per_min is None:
            rate_per_min = 60
        if rate_per_min <= 0:
            raise ValueError("rate_per_min precisa ser > 0")
        self.rate_per_min = rate_per_min
        self.tokens: float = float(self.rate_per_min)
        self.last_refill: float = time.time()

    def acquire(self) -> None:
        self._refill()
        if self.tokens < 1:
            sleep_sec = (1 - self.tokens) * 60.0 / self.rate_per_min
            log.debug(
                "rate_limiter %s: throttle %.2fs (tokens=%.2f)",
                self.name, sleep_sec, self.tokens,
            )
            time.sleep(sleep_sec)
            self._refill()
        self.tokens -= 1

    def _refill(self) -> None:
        now = time.time()
        elapsed = now - self.last_refill
        added = elapsed * (self.rate_per_min / 60.0)
        self.tokens = min(float(self.rate_per_min), self.tokens + added)
        self.last_refill = now


# ----------------------------------------------------------------------
# Cost Quota
# ----------------------------------------------------------------------


class QuotaExceededError(RuntimeError):
    """Levantado quando ``CostQuota.check_or_raise`` detecta excesso."""


@dataclass
class CostQuota:
    """
    Pausa pipeline se ``current_spend_usd`` ultrapassar
    ``daily_max_usd``. O caller é responsável por tracking do
    spending (geralmente via ``api_calls`` table ou via
    ``StructuredLogger`` agregado).
    """

    corpus_id: str
    daily_max_usd: float | None = None

    def __post_init__(self) -> None:
        if self.daily_max_usd is None:
            self.daily_max_usd = _env_float("RDO_AGENT_DAILY_QUOTA_USD", 100.0)

    def check_or_raise(self, current_spend_usd: float) -> None:
        if current_spend_usd > (self.daily_max_usd or 0):
            raise QuotaExceededError(
                f"corpus={self.corpus_id}: spend "
                f"${current_spend_usd:.2f} > daily quota "
                f"${self.daily_max_usd:.2f}"
            )


# ----------------------------------------------------------------------
# Singletons cross-module (per-API)
# ----------------------------------------------------------------------

_OPENAI_CIRCUIT: CircuitBreaker | None = None
_OPENAI_WHISPER_CIRCUIT: CircuitBreaker | None = None
_OPENAI_VISION_CIRCUIT: CircuitBreaker | None = None
_ANTHROPIC_CIRCUIT: CircuitBreaker | None = None
_OPENAI_RATE: RateLimiter | None = None
_ANTHROPIC_RATE: RateLimiter | None = None


def get_openai_circuit() -> CircuitBreaker:
    """Singleton CircuitBreaker para chamadas a OpenAI (chat/embeddings)."""
    global _OPENAI_CIRCUIT
    if _OPENAI_CIRCUIT is None:
        _OPENAI_CIRCUIT = CircuitBreaker(name="openai")
    return _OPENAI_CIRCUIT


def get_openai_whisper_circuit() -> CircuitBreaker:
    """Singleton CircuitBreaker para chamadas a OpenAI Whisper API.

    Separado do circuit ``openai`` (chat) porque Whisper tem perfil
    de falha e rate limit independentes — degradação no chat não
    deve abortar transcrição em andamento e vice-versa.
    """
    global _OPENAI_WHISPER_CIRCUIT
    if _OPENAI_WHISPER_CIRCUIT is None:
        _OPENAI_WHISPER_CIRCUIT = CircuitBreaker(name="openai_whisper")
    return _OPENAI_WHISPER_CIRCUIT


def get_openai_vision_circuit() -> CircuitBreaker:
    """Singleton CircuitBreaker para OpenAI Vision API.

    Separado dos circuits ``openai`` (chat) e ``openai_whisper``
    pelo mesmo motivo — perfis de falha e rate limit independentes.
    """
    global _OPENAI_VISION_CIRCUIT
    if _OPENAI_VISION_CIRCUIT is None:
        _OPENAI_VISION_CIRCUIT = CircuitBreaker(name="openai_vision")
    return _OPENAI_VISION_CIRCUIT


def get_anthropic_circuit() -> CircuitBreaker:
    """Singleton CircuitBreaker para chamadas a Anthropic."""
    global _ANTHROPIC_CIRCUIT
    if _ANTHROPIC_CIRCUIT is None:
        _ANTHROPIC_CIRCUIT = CircuitBreaker(name="anthropic")
    return _ANTHROPIC_CIRCUIT


def get_openai_rate_limiter() -> RateLimiter:
    """Singleton RateLimiter para chamadas a OpenAI."""
    global _OPENAI_RATE
    if _OPENAI_RATE is None:
        _OPENAI_RATE = RateLimiter(
            name="openai",
            rate_per_min=_env_int("RDO_AGENT_RATE_LIMIT_OPENAI_PER_MIN", 60),
        )
    return _OPENAI_RATE


def get_anthropic_rate_limiter() -> RateLimiter:
    """Singleton RateLimiter para chamadas a Anthropic."""
    global _ANTHROPIC_RATE
    if _ANTHROPIC_RATE is None:
        _ANTHROPIC_RATE = RateLimiter(
            name="anthropic",
            rate_per_min=_env_int("RDO_AGENT_RATE_LIMIT_ANTHROPIC_PER_MIN", 20),
        )
    return _ANTHROPIC_RATE


def reset_singletons_for_test() -> None:
    """Reseta singletons (apenas para uso em testes)."""
    global _OPENAI_CIRCUIT, _OPENAI_WHISPER_CIRCUIT, _OPENAI_VISION_CIRCUIT
    global _ANTHROPIC_CIRCUIT, _OPENAI_RATE, _ANTHROPIC_RATE
    _OPENAI_CIRCUIT = None
    _OPENAI_WHISPER_CIRCUIT = None
    _OPENAI_VISION_CIRCUIT = None
    _ANTHROPIC_CIRCUIT = None
    _OPENAI_RATE = None
    _ANTHROPIC_RATE = None
