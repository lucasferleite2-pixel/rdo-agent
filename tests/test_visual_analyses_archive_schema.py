"""Testes do schema visual_analyses_archive — Sprint 4 Op9 Fase 3.

Cobrem:
  - table criada via init_db (schema.sql + migration)
  - migration idempotente (2x sem erro)
  - mirror de visual_analyses + archived_at + archive_reason
  - index (obra, archived_at) + (file_id) presentes
  - insert happy path preserva analysis_json original
  - archived_at NOT NULL (pra sempre saber quando foi arquivado)
"""

from __future__ import annotations

import sqlite3

import pytest

from rdo_agent.orchestrator import (
    _migrate_visual_analyses_archive_sprint4_op9,
    init_db,
)


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path)


def test_table_exists_after_init_db(db):
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='visual_analyses_archive'"
    ).fetchone()
    assert row is not None


def test_columns_include_archive_extras(db):
    cols = {r["name"] for r in db.execute(
        "PRAGMA table_info(visual_analyses_archive)"
    ).fetchall()}
    # Original visual_analyses columns
    for c in ("obra", "file_id", "analysis_json", "confidence",
              "api_call_id", "created_at"):
        assert c in cols
    # Extras Op9
    assert "archived_at" in cols
    assert "archive_reason" in cols
    assert "original_id" in cols


def test_indexes_created(db):
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='visual_analyses_archive'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert "idx_visual_analyses_archive_obra" in names
    assert "idx_visual_analyses_archive_fileid" in names


def test_migration_idempotent_when_called_twice(db):
    _migrate_visual_analyses_archive_sprint4_op9(db)
    _migrate_visual_analyses_archive_sprint4_op9(db)
    db.execute(
        """INSERT INTO visual_analyses_archive (
            original_id, obra, file_id, analysis_json, confidence,
            created_at, archived_at, archive_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (1, "OBRA_X", "f_test", '{"a": "b"}', 1.0,
         "2026-04-20T00:00:00Z", "2026-04-23T00:00:00Z",
         "superseded_by_ocr_first_retroactive_sprint4_op9"),
    )
    db.commit()
    assert db.execute(
        "SELECT COUNT(*) FROM visual_analyses_archive"
    ).fetchone()[0] == 1


def test_insert_with_all_fields_round_trip(db):
    db.execute(
        """INSERT INTO visual_analyses_archive (
            original_id, obra, file_id, analysis_json, confidence,
            api_call_id, created_at, archived_at, archive_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (42, "OBRA_Y", "f_img_abc",
         '{"atividade_em_curso": "estrutura montada"}',
         0.95, None,
         "2026-04-22T10:15:00Z",
         "2026-04-23T14:30:00Z",
         "superseded_by_ocr_first_retroactive_sprint4_op9"),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM visual_analyses_archive WHERE original_id=42"
    ).fetchone()
    assert row["obra"] == "OBRA_Y"
    assert row["file_id"] == "f_img_abc"
    assert "estrutura montada" in row["analysis_json"]
    assert row["archive_reason"].startswith("superseded")


def test_archived_at_is_not_null(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO visual_analyses_archive (
                obra, file_id, analysis_json, created_at, archived_at
            ) VALUES (?, ?, ?, ?, ?)""",
            ("OBRA_Z", "f_x", "{}", "2026-04-22T00:00:00Z", None),
        )


def test_migration_on_fresh_connection():
    """Conn sem schema.sql: migration cria tabela isoladamente."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _migrate_visual_analyses_archive_sprint4_op9(conn)
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name='visual_analyses_archive'"
    ).fetchone() is not None
    conn.close()
