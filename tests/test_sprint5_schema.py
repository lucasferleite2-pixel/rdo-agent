"""Testes schema Sprint 5 Fase A+B — forensic_narratives + correlations."""

from __future__ import annotations

import sqlite3

import pytest

from rdo_agent.orchestrator import (
    _migrate_sprint5_fase_a_b,
    init_db,
)


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path)


# ---------------------------------------------------------------------------
# forensic_narratives
# ---------------------------------------------------------------------------


def test_forensic_narratives_table_exists(db):
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='forensic_narratives'"
    ).fetchone()
    assert row is not None


def test_forensic_narratives_columns(db):
    cols = {r["name"] for r in db.execute(
        "PRAGMA table_info(forensic_narratives)"
    ).fetchall()}
    expected = {
        "id", "obra", "scope", "scope_ref", "narrative_text",
        "dossier_hash", "model_used", "prompt_version", "api_call_id",
        "events_count", "confidence", "validation_checklist_json",
        "created_at",
    }
    assert expected.issubset(cols)


def test_forensic_narratives_scope_check(db):
    """CHECK (scope IN ('day', 'obra_overview')) deve bloquear valor invalido."""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO forensic_narratives (obra, scope, narrative_text,
            dossier_hash, model_used, prompt_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("OBRA_X", "invalid_scope", "text", "h", "m", "v",
             "2026-04-22T00:00:00Z"),
        )


def test_forensic_narratives_unique_key_enforced(db):
    """UNIQUE (obra, scope, scope_ref, dossier_hash) previne duplicata."""
    db.execute(
        """INSERT INTO forensic_narratives (obra, scope, scope_ref,
        narrative_text, dossier_hash, model_used, prompt_version, created_at)
        VALUES (?, 'day', '2026-04-06', ?, ?, ?, ?, ?)""",
        ("OBRA_X", "narrative 1", "hash_A", "claude-sonnet-4-6",
         "narrator_v1", "2026-04-22T00:00:00Z"),
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO forensic_narratives (obra, scope, scope_ref,
            narrative_text, dossier_hash, model_used, prompt_version,
            created_at)
            VALUES (?, 'day', '2026-04-06', ?, ?, ?, ?, ?)""",
            ("OBRA_X", "narrative 2 diff", "hash_A", "claude-sonnet-4-6",
             "narrator_v1", "2026-04-22T01:00:00Z"),
        )


def test_forensic_narratives_index_present(db):
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='forensic_narratives'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert "idx_narratives_obra_scope" in names


def test_forensic_narratives_happy_insert(db):
    db.execute(
        """INSERT INTO forensic_narratives (obra, scope, scope_ref,
        narrative_text, dossier_hash, model_used, prompt_version,
        events_count, confidence, validation_checklist_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("EVERALDO", "day", "2026-04-06", "# Narrativa: ...\n---",
         "abc123", "claude-sonnet-4-6", "narrator_v1",
         12, 0.85, '{"passed": true}', "2026-04-22T00:00:00Z"),
    )
    db.commit()
    row = db.execute(
        "SELECT events_count, confidence FROM forensic_narratives "
        "WHERE obra='EVERALDO'"
    ).fetchone()
    assert row["events_count"] == 12
    assert row["confidence"] == 0.85


# ---------------------------------------------------------------------------
# correlations (esqueleto Fase B)
# ---------------------------------------------------------------------------


def test_correlations_table_exists(db):
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='correlations'"
    ).fetchone()
    assert row is not None


def test_correlations_columns(db):
    cols = {r["name"] for r in db.execute(
        "PRAGMA table_info(correlations)"
    ).fetchall()}
    expected = {
        "id", "obra", "correlation_type",
        "primary_event_ref", "primary_event_source",
        "related_event_ref", "related_event_source",
        "time_gap_seconds", "confidence", "rationale",
        "detected_by", "created_at",
    }
    assert expected.issubset(cols)


def test_correlations_index_present(db):
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='correlations'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert "idx_correlations_obra" in names


def test_correlations_happy_insert(db):
    db.execute(
        """INSERT INTO correlations (obra, correlation_type,
        primary_event_ref, primary_event_source,
        related_event_ref, related_event_source,
        time_gap_seconds, confidence, rationale, detected_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("EVERALDO", "payment_intent_before_execution",
         "c_123", "classification", "fr_45", "financial_record",
         1800, 0.9, "pedido pix + transferencia 30min depois",
         "rule:payment_intent", "2026-04-22T00:00:00Z"),
    )
    db.commit()
    row = db.execute(
        "SELECT correlation_type, time_gap_seconds FROM correlations "
        "WHERE obra='EVERALDO'"
    ).fetchone()
    assert row["correlation_type"] == "payment_intent_before_execution"
    assert row["time_gap_seconds"] == 1800


# ---------------------------------------------------------------------------
# Migration idempotente
# ---------------------------------------------------------------------------


def test_migration_idempotent_rerun(db):
    _migrate_sprint5_fase_a_b(db)
    _migrate_sprint5_fase_a_b(db)
    # Insert ainda funciona apos 2x
    db.execute(
        """INSERT INTO forensic_narratives (obra, scope, narrative_text,
        dossier_hash, model_used, prompt_version, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("OBRA_MIG", "day", "x", "h", "m", "v", "2026-04-22T00:00:00Z"),
    )
    db.commit()
    assert db.execute(
        "SELECT COUNT(*) FROM forensic_narratives WHERE obra='OBRA_MIG'"
    ).fetchone()[0] == 1


def test_migration_on_fresh_connection():
    """Conn sem schema.sql rodado: migration cria tabelas isoladas."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _migrate_sprint5_fase_a_b(conn)
    for t in ("forensic_narratives", "correlations"):
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name=?", (t,)
        ).fetchone() is not None
    conn.close()
