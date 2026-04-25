"""
Observability — Sessão 6 (dívidas #53 + #54).

- ``StructuredLogger``: emite eventos como JSONL em
  ``~/.rdo-agent/logs/<corpus_id>/<YYYY-MM-DD>.jsonl``. Não substitui
  o ``logging`` standard (que continua emitindo texto ao terminal);
  é uma camada paralela com schema previsível para queries
  agregadas (custo, throughput, falhas).

- ``CircuitBreaker`` / ``RateLimiter`` / ``CostQuota``: primitivas de
  resiliência para wrappers de chamadas a APIs externas. Não duplicam
  o retry per-module (narrator/transcriber/visual_analyzer já têm) —
  centralizam apenas circuit + rate + quota cross-module.
"""

from __future__ import annotations

from rdo_agent.observability.logger import (
    DEFAULT_LOG_ROOT,
    StructuredLogger,
    aggregate_logs,
    iter_log_records,
)
from rdo_agent.observability.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CostQuota,
    QuotaExceededError,
    RateLimiter,
    get_anthropic_circuit,
    get_anthropic_rate_limiter,
    get_openai_circuit,
    get_openai_rate_limiter,
    get_openai_vision_circuit,
    get_openai_whisper_circuit,
)

__all__ = [
    "DEFAULT_LOG_ROOT",
    "StructuredLogger",
    "aggregate_logs",
    "iter_log_records",
    "CircuitBreaker",
    "CircuitOpenError",
    "CostQuota",
    "QuotaExceededError",
    "RateLimiter",
    "get_anthropic_circuit",
    "get_anthropic_rate_limiter",
    "get_openai_circuit",
    "get_openai_rate_limiter",
    "get_openai_vision_circuit",
    "get_openai_whisper_circuit",
]
