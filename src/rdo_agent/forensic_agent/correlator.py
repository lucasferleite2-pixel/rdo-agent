"""
CORRELATOR — Sprint 5 Fase B.

Orquestrador dos detectores rule-based. Modelo pairwise 1:1 (uma
Correlation = uma aresta entre dois eventos).

Exporta:
  - `Correlation` (dataclass alinhado com tabela correlations)
  - `EventSource` (Literal)
  - `save_correlation(conn, c)`: persiste 1 Correlation. Retorna id.
  - `detect_correlations(conn, obra, *, persist=True)`: roda os 3
    detectores (temporal, semantic, math), persiste (opcional) e
    retorna list[Correlation].
  - `get_correlations(conn, obra, *, filter_type=None, min_confidence=0.0)`:
    consulta a tabela correlations (nao roda detectores).
  - `delete_correlations_for_obra(conn, obra)`: remove todas as linhas
    da obra. Usado pelo --rebuild da CLI.
  - `find_correlations_for_day(conn, obra, date)`,
    `find_correlations_obra_wide(conn, obra)`: wrappers retrocompativeis
    que NAO persistem (usam os detectores in-memory); o find_for_day
    filtra por data do primary_event (financial_record.data_transacao).
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


def detect_correlations(
    conn: sqlite3.Connection, obra: str, *, persist: bool = True,
) -> list[Correlation]:
    """
    Roda os 3 detectores rule-based (temporal, semantic, math) sobre a
    obra e opcionalmente persiste. Retorna a lista concatenada.

    Import lazy dos detectores pra nao criar ciclo de importacao (o
    pacote detectors re-exporta estes nomes).
    """
    # Import lazy: detectors -> _common -> correlator (Correlation).
    from rdo_agent.forensic_agent.detectors.contract_renegotiation import (
        detect_contract_renegotiation,
    )
    from rdo_agent.forensic_agent.detectors.math import (
        detect_math_relations,
    )
    from rdo_agent.forensic_agent.detectors.semantic import (
        detect_semantic_payment_scope,
    )
    from rdo_agent.forensic_agent.detectors.temporal import (
        detect_temporal_payment_context,
    )

    out: list[Correlation] = []
    out.extend(detect_temporal_payment_context(conn, obra))
    out.extend(detect_semantic_payment_scope(conn, obra))
    out.extend(detect_math_relations(conn, obra))
    # Sessão 5 / #27: roda DEPOIS — detector mensagem↔mensagem que
    # complementa o triplet base (que correlaciona com financial_records).
    out.extend(detect_contract_renegotiation(conn, obra))

    if persist:
        for c in out:
            save_correlation(conn, c)
    return out


def get_correlations(
    conn: sqlite3.Connection, obra: str, *,
    filter_type: str | None = None,
    min_confidence: float = 0.0,
) -> list[Correlation]:
    """
    Consulta a tabela correlations (NAO roda detectores).

    `filter_type`: se fornecido, filtra por correlation_type exato.
    `min_confidence`: threshold inclusivo (>=).
    """
    sql = "SELECT * FROM correlations WHERE obra = ? AND confidence >= ?"
    params: list[object] = [obra, min_confidence]
    if filter_type is not None:
        sql += " AND correlation_type = ?"
        params.append(filter_type)
    sql += " ORDER BY primary_event_ref, related_event_ref"
    rows = conn.execute(sql, params).fetchall()
    return [
        Correlation(
            obra=r["obra"],
            correlation_type=r["correlation_type"],
            primary_event_ref=r["primary_event_ref"],
            primary_event_source=r["primary_event_source"],
            related_event_ref=r["related_event_ref"],
            related_event_source=r["related_event_source"],
            time_gap_seconds=r["time_gap_seconds"],
            confidence=r["confidence"],
            rationale=r["rationale"],
            detected_by=r["detected_by"],
        )
        for r in rows
    ]


def delete_correlations_for_obra(
    conn: sqlite3.Connection, obra: str,
) -> int:
    """Remove todas as correlations da obra. Retorna count removido."""
    cur = conn.execute("DELETE FROM correlations WHERE obra = ?", (obra,))
    conn.commit()
    return cur.rowcount


def find_correlations_for_day(
    conn: sqlite3.Connection, obra: str, date: str,
) -> list[Correlation]:
    """
    Retorna correlacoes cujo primary_event (financial_record) foi em
    `date` (YYYY-MM-DD). Roda os detectores in-memory (nao persiste).
    Se nao houver correlations persistidas, chamador pode usar
    `detect_correlations(conn, obra, persist=True)` primeiro.
    """
    all_corr = detect_correlations(conn, obra, persist=False)
    # Filtra: primary_event_ref = 'fr_<id>' cuja data bate com date
    fr_ids_on_date = {
        f"fr_{r['id']}"
        for r in conn.execute(
            "SELECT id FROM financial_records "
            "WHERE obra = ? AND data_transacao = ?",
            (obra, date),
        )
    }
    return [c for c in all_corr if c.primary_event_ref in fr_ids_on_date]


def find_correlations_obra_wide(
    conn: sqlite3.Connection, obra: str,
) -> list[Correlation]:
    """Alias de `detect_correlations(conn, obra, persist=False)`."""
    return detect_correlations(conn, obra, persist=False)


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
    "delete_correlations_for_obra",
    "detect_correlations",
    "find_correlations_for_day",
    "find_correlations_obra_wide",
    "get_correlations",
    "save_correlation",
]
