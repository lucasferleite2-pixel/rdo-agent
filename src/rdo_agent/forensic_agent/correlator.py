"""
CORRELATOR — Esqueleto Fase B (Sprint 5).

NESTA SESSAO (Fase A): contrato apenas. Implementacao real na proxima sessao.

TODO Fase B:
  - Implementar find_payment_intent_before_execution (rule-based):
    texto 'manda a chave', 'pix', 'transferir' seguido por
    financial_record em <30min
  - Implementar find_audio_mentions_matching_photos:
    audio menciona material/atividade + foto do mesmo material/
    atividade no mesmo dia
  - Implementar find_material_discussions_before_delivery:
    discussoes sobre material X + evento de entrega/recebimento
    posterior
  - Integrar LLM-based correlation pra casos complexos (Sonnet 4.6)

Este modulo exporta:
  - Correlation dataclass (schema alinhado com tabela correlations)
  - find_correlations_for_day / find_correlations_obra_wide
    (stubs que levantam NotImplementedError — sinalizam contrato
    publico sem bloquear imports)
  - save_correlation helper pra Fase B plumar resultados em DB
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

EventSource = Literal["classification", "financial_record", "document"]


@dataclass
class Correlation:
    """
    Relacao temporal/semantica entre dois eventos.

    Alinhada com schema `correlations` table (Sprint 5 Fase B).
    `time_gap_seconds`: distancia temporal entre primary e related
    (positivo se related vem depois).
    `confidence`: [0.0, 1.0].
    `detected_by`: ex 'rule:payment_intent_30min' ou 'agent:sonnet-4-6'.
    """

    obra: str
    correlation_type: str
    primary_event_ref: str
    primary_event_source: EventSource
    related_event_ref: str
    related_event_source: EventSource
    time_gap_seconds: int | None
    confidence: float
    rationale: str
    detected_by: str


def find_correlations_for_day(
    conn: sqlite3.Connection, obra: str, date: str,
) -> list[Correlation]:
    """
    TODO Fase B — Retorna correlacoes detectadas para um dia especifico.

    Regras planejadas:
      1. payment_intent_before_execution: texto 'manda a chave', 'pix',
         'transferir' seguido de financial_record em <30min
      2. audio_mentions_matching_photos: audio menciona material/atividade
         + foto do mesmo material/atividade no mesmo dia
      3. cronograma_vs_execution: promessa de execucao em data X
         + reporte de execucao em data Y

    Args:
        conn: conexao SQLite
        obra: CODESC
        date: YYYY-MM-DD

    Returns:
        Lista de Correlation detectadas para o dia.
    """
    raise NotImplementedError(
        "Fase B — find_correlations_for_day: implementacao planejada para "
        "proxima sessao. Ver TODO no header do modulo."
    )


def find_correlations_obra_wide(
    conn: sqlite3.Connection, obra: str,
) -> list[Correlation]:
    """
    TODO Fase B — Correlacoes que cruzam dias da obra inteira.

    Regras planejadas:
      1. recurring_payment_pattern: pagamentos recorrentes em intervalos
         similares
      2. contract_then_execution: fechamento de contrato + execucao
         subsequente
      3. escalation_pattern: volume de mensagens crescente antes de
         pagamento
    """
    raise NotImplementedError(
        "Fase B — find_correlations_obra_wide: implementacao planejada para "
        "proxima sessao."
    )


def save_correlation(
    conn: sqlite3.Connection, correlation: Correlation,
) -> int:
    """
    Helper de persistencia para Fase B (ja implementado pra evitar
    reescrita): insere Correlation na tabela correlations.

    Returns id da row criada.
    """
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    data = asdict(correlation)
    data["created_at"] = now
    cur = conn.execute(
        """INSERT INTO correlations (
            obra, correlation_type,
            primary_event_ref, primary_event_source,
            related_event_ref, related_event_source,
            time_gap_seconds, confidence, rationale, detected_by,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data["obra"], data["correlation_type"],
            data["primary_event_ref"], data["primary_event_source"],
            data["related_event_ref"], data["related_event_source"],
            data["time_gap_seconds"], data["confidence"],
            data["rationale"], data["detected_by"], data["created_at"],
        ),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


__all__ = [
    "Correlation",
    "EventSource",
    "find_correlations_for_day",
    "find_correlations_obra_wide",
    "save_correlation",
]
