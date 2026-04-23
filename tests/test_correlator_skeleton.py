"""Testes correlator esqueleto — Sprint 5 Fase B preparacao."""

from __future__ import annotations

import sqlite3

import pytest

from rdo_agent.forensic_agent import (
    Correlation,
    find_correlations_for_day,
    find_correlations_obra_wide,
    save_correlation,
)
from rdo_agent.orchestrator import init_db


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path)


# ---------------------------------------------------------------------------
# Importacao e dataclass
# ---------------------------------------------------------------------------


def test_correlator_module_imports():
    """Importar modulo + funcoes nao levanta."""
    from rdo_agent.forensic_agent import correlator
    assert hasattr(correlator, "Correlation")
    assert hasattr(correlator, "find_correlations_for_day")
    assert hasattr(correlator, "find_correlations_obra_wide")
    assert hasattr(correlator, "save_correlation")


def test_correlation_dataclass_instantiation():
    c = Correlation(
        obra="OBRA_X",
        correlation_type="payment_intent_before_execution",
        primary_event_ref="c_123",
        primary_event_source="classification",
        related_event_ref="fr_45",
        related_event_source="financial_record",
        time_gap_seconds=1800,
        confidence=0.9,
        rationale="pedido pix seguido de transferencia em 30min",
        detected_by="rule:payment_intent",
    )
    assert c.obra == "OBRA_X"
    assert c.correlation_type == "payment_intent_before_execution"
    assert c.time_gap_seconds == 1800


def test_correlation_allows_null_time_gap():
    """time_gap_seconds pode ser None (correlacao sem dimensao temporal)."""
    c = Correlation(
        obra="OBRA_Y",
        correlation_type="audio_mentions_photo",
        primary_event_ref="c_1",
        primary_event_source="classification",
        related_event_ref="c_2",
        related_event_source="classification",
        time_gap_seconds=None,
        confidence=0.7,
        rationale="same day, same material",
        detected_by="rule:co_occurrence",
    )
    assert c.time_gap_seconds is None


# ---------------------------------------------------------------------------
# Fase B implementada — sem NotImplementedError; com DB vazio retornam []
# ---------------------------------------------------------------------------


def test_find_correlations_for_day_empty_db_returns_empty(db):
    """Sem financial_records/classifications, retorna lista vazia."""
    assert find_correlations_for_day(db, "OBRA_X", "2026-04-06") == []


def test_find_correlations_obra_wide_empty_db_returns_empty(db):
    assert find_correlations_obra_wide(db, "OBRA_X") == []


# ---------------------------------------------------------------------------
# save_correlation ja funcional (pra Fase B plumar)
# ---------------------------------------------------------------------------


def test_save_correlation_inserts_row(db):
    c = Correlation(
        obra="OBRA_S",
        correlation_type="payment_intent_before_execution",
        primary_event_ref="c_1",
        primary_event_source="classification",
        related_event_ref="fr_2",
        related_event_source="financial_record",
        time_gap_seconds=600,
        confidence=0.85,
        rationale="pediu pix, recebeu em 10min",
        detected_by="rule:test",
    )
    cid = save_correlation(db, c)
    assert cid > 0

    row = db.execute(
        "SELECT correlation_type, confidence, detected_by "
        "FROM correlations WHERE id=?", (cid,),
    ).fetchone()
    assert row["correlation_type"] == "payment_intent_before_execution"
    assert row["confidence"] == 0.85
    assert row["detected_by"] == "rule:test"


def test_save_correlation_with_null_time_gap(db):
    c = Correlation(
        obra="OBRA_N",
        correlation_type="co_occurrence",
        primary_event_ref="c_a",
        primary_event_source="classification",
        related_event_ref="c_b",
        related_event_source="classification",
        time_gap_seconds=None,
        confidence=0.5,
        rationale="same day",
        detected_by="rule:x",
    )
    cid = save_correlation(db, c)
    row = db.execute(
        "SELECT time_gap_seconds FROM correlations WHERE id=?", (cid,),
    ).fetchone()
    assert row["time_gap_seconds"] is None


def test_correlations_schema_supports_manual_insert(db):
    """Sanity: schema correlations aceita insert direto sem dataclass."""
    db.execute(
        """INSERT INTO correlations (obra, correlation_type,
        primary_event_ref, primary_event_source,
        related_event_ref, related_event_source,
        confidence, rationale, detected_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("OBRA_M", "manual", "a", "classification", "b", "document",
         0.5, "manual insert test", "rule:manual",
         "2026-04-22T00:00:00Z"),
    )
    db.commit()
    row = db.execute(
        "SELECT correlation_type FROM correlations WHERE obra='OBRA_M'"
    ).fetchone()
    assert row["correlation_type"] == "manual"
