"""
types.py — Sprint 5 Fase B.

Tipos compartilhados do subsistema forensic_agent/correlator.

`Correlation` + `EventSource` continuam sendo definidos em
`correlator.py` (autoridade historica, alinhada com `save_correlation`).
Este modulo re-exporta pra ergonomia e adiciona:

  - `CorrelationType`: StrEnum com os tipos canonicos emitidos pelos
    tres detectores rule-based da Fase B.
  - `CONFIDENCE_HIGH`, `CONFIDENCE_MEDIUM`, `CONFIDENCE_LOW`:
    thresholds de exibicao (nao de persistencia — persistencia sempre
    salva tudo).
"""

from __future__ import annotations

from enum import StrEnum

from rdo_agent.forensic_agent.correlator import Correlation, EventSource


class CorrelationType(StrEnum):
    """Valores canonicos do campo `correlation_type` na tabela correlations."""

    TEMPORAL_PAYMENT_CONTEXT = "TEMPORAL_PAYMENT_CONTEXT"
    SEMANTIC_PAYMENT_SCOPE = "SEMANTIC_PAYMENT_SCOPE"
    MATH_VALUE_MATCH = "MATH_VALUE_MATCH"
    MATH_INSTALLMENT_MATCH = "MATH_INSTALLMENT_MATCH"
    MATH_VALUE_DIVERGENCE = "MATH_VALUE_DIVERGENCE"


# Thresholds de exibicao — persistencia salva todos strengths.
CONFIDENCE_HIGH = 0.70
CONFIDENCE_MEDIUM = 0.40
CONFIDENCE_LOW = 0.0


__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "Correlation",
    "CorrelationType",
    "EventSource",
]
