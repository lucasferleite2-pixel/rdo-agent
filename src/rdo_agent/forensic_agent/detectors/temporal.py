"""
Detector TEMPORAL — Sprint 5 Fase B.

Regra: para cada financial_record com timestamp resolvido, busca
classifications na janela +-30 min cujo texto contem keywords de
contexto de pagamento (pix, transferencia, chave, valor, sinal,
comprovante, reais). Emite 1 Correlation por par (financial_record,
classification) com keyword_match > 0.

Modelo pairwise 1:1 (schema Fase A):
  - primary_event_source='financial_record', primary_event_ref='fr_<id>'
  - related_event_source='classification', related_event_ref='c_<id>'
  - correlation_type='TEMPORAL_PAYMENT_CONTEXT'
  - time_gap_seconds=cls_ts - fr_ts (positivo se classification vem depois)
  - confidence=min(unique_keyword_matches / 3, 1.0)
  - detected_by='temporal_v1'
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from rdo_agent.forensic_agent.correlator import Correlation
from rdo_agent.forensic_agent.detectors._common import (
    fetch_event_texts,
    fetch_financial_timestamps,
)
from rdo_agent.forensic_agent.types import CorrelationType

WINDOW = timedelta(minutes=30)

PAYMENT_KEYWORDS: tuple[str, ...] = (
    "pix",
    "transferencia",
    "transferência",
    "manda",
    "chave",
    "valor",
    "reais",
    "sinal",
    "comprovante",
)

# >=3 unique matches = confidence saturada em 1.0 (um pedido explicito
# costuma citar 2-3 desses termos; mais que isso e verbosidade)
CONFIDENCE_SATURATION = 3
DETECTOR_ID = "temporal_v1"


def _count_unique_matches(text: str) -> int:
    """Conta quantas keywords distintas aparecem no texto (case-insensitive)."""
    if not text:
        return 0
    lower = text.lower()
    return sum(1 for kw in PAYMENT_KEYWORDS if kw in lower)


def detect_temporal_payment_context(
    conn: sqlite3.Connection, obra: str,
    *, window: timedelta | None = None,
) -> list[Correlation]:
    """
    Emite correlacoes TEMPORAL_PAYMENT_CONTEXT para a obra inteira.

    Uma correlacao por par (financial_record, classification) onde:
      - classification.timestamp esta em [fr.ts - WINDOW, fr.ts + WINDOW]
        (default WINDOW=30min; configuravel via param ``window``)
      - texto da classification contem >=1 PAYMENT_KEYWORDS

    Financial_records sem timestamp sao ignorados (data_transacao OU
    hora_transacao null). Classifications sem timestamp sao ignoradas.

    Args:
        window: override do WINDOW default (30min). Permite calibrar
            por corpus/contexto. Sessao 10 (#50): expoe parametro
            sem mudar default, integra com parallel_detect_correlations.
    """
    effective_window = window if window is not None else WINDOW

    frs = [fe for fe in fetch_financial_timestamps(conn, obra)
           if fe.timestamp is not None]
    events = [e for e in fetch_event_texts(conn, obra)
              if e.timestamp is not None]
    if not frs or not events:
        return []

    window_label = _format_window_label(effective_window)
    out: list[Correlation] = []
    for fr in frs:
        lo = fr.timestamp - effective_window
        hi = fr.timestamp + effective_window
        for ev in events:
            if ev.timestamp < lo or ev.timestamp > hi:
                continue
            matches = _count_unique_matches(ev.text)
            if matches == 0:
                continue
            confidence = min(matches / CONFIDENCE_SATURATION, 1.0)
            delta = int((ev.timestamp - fr.timestamp).total_seconds())
            out.append(Correlation(
                obra=obra,
                correlation_type=CorrelationType.TEMPORAL_PAYMENT_CONTEXT.value,
                primary_event_ref=f"fr_{fr.financial_id}",
                primary_event_source="financial_record",
                related_event_ref=f"c_{ev.classification_id}",
                related_event_source="classification",
                time_gap_seconds=delta,
                confidence=confidence,
                rationale=(
                    f"{matches} keyword(s) de pagamento na janela "
                    f"+-{window_label} (delta={delta:+d}s)"
                ),
                detected_by=DETECTOR_ID,
            ))
    return out


def _format_window_label(window: timedelta) -> str:
    """Formato compacto para rationale (30min, 3d, 48h, etc)."""
    total_sec = int(window.total_seconds())
    days = total_sec // 86400
    if days >= 1 and total_sec % 86400 == 0:
        return f"{days}d"
    hours = total_sec // 3600
    if hours >= 1 and total_sec % 3600 == 0:
        return f"{hours}h"
    minutes = total_sec // 60
    return f"{minutes}min"


__all__ = [
    "DETECTOR_ID",
    "PAYMENT_KEYWORDS",
    "WINDOW",
    "detect_temporal_payment_context",
]
