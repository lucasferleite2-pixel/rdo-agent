"""
Detectors — Sprint 5 Fase B.

Rule-based correlation detectors. Zero API calls.

Cada detector recebe (conn, obra) e retorna list[Correlation]. O
orquestrador em `correlator.detect_correlations` compoe os tres e
persiste via `save_correlation`.
"""

from __future__ import annotations

from rdo_agent.forensic_agent.detectors.temporal import (
    detect_temporal_payment_context,
)

__all__ = [
    "detect_temporal_payment_context",
]
