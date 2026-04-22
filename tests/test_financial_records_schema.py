"""Testes do schema financial_records — Sprint 4 Op8.

Cobrem:
  - migration idempotente (init_db 2x sem erro)
  - NOT NULL em obra, source_file_id, created_at
  - FK para files(file_id) enforcement
  - UNIQUE (obra, source_file_id) previne duplicata
  - INSERT + SELECT round-trip com valor_centavos
  - Index por (obra, data_transacao) presente
"""

from __future__ import annotations

import sqlite3

import pytest

from rdo_agent.orchestrator import (
    _migrate_financial_records_sprint4_op8,
    init_db,
)


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path)


def _seed_image_file(conn: sqlite3.Connection, file_id: str = "f_test_image") -> str:
    conn.execute(
        """INSERT INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (file_id, "OBRA_TEST", "10_media/comprov.jpg", "image",
         "a" * 64, 100_000, "awaiting_classification",
         "2026-04-22T00:00:00Z"),
    )
    conn.commit()
    return file_id


def test_table_created_on_init_db(db):
    """init_db cria financial_records via schema.sql + migration."""
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='financial_records'"
    ).fetchone()
    assert row is not None
    assert row["name"] == "financial_records"


def test_columns_match_spec(db):
    """Todas as 20 colunas do schema estao presentes."""
    cols = {r["name"] for r in db.execute(
        "PRAGMA table_info(financial_records)"
    ).fetchall()}
    expected = {
        "id", "obra", "source_file_id", "doc_type",
        "valor_centavos", "moeda", "data_transacao", "hora_transacao",
        "pagador_nome", "pagador_doc", "recebedor_nome", "recebedor_doc",
        "chave_pix", "descricao", "instituicao_origem",
        "instituicao_destino", "raw_ocr_text", "confidence",
        "api_call_id", "created_at",
    }
    assert expected.issubset(cols)


def test_index_on_obra_and_data_transacao(db):
    """Index composite para queries de ledger por dia existe."""
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='financial_records'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert "idx_financial_records_obra_data" in names


def test_migration_idempotent_when_called_twice(db):
    """Rodar a migration 2x sobre DB ja inicializado nao quebra."""
    _migrate_financial_records_sprint4_op8(db)
    _migrate_financial_records_sprint4_op8(db)
    # Tabela ainda existe e inserts funcionam
    _seed_image_file(db)
    db.execute(
        """INSERT INTO financial_records (
            obra, source_file_id, doc_type, valor_centavos,
            created_at
        ) VALUES (?, ?, ?, ?, ?)""",
        ("OBRA_TEST", "f_test_image", "pix", 350000,
         "2026-04-22T00:00:00Z"),
    )
    db.commit()
    assert db.execute(
        "SELECT COUNT(*) FROM financial_records"
    ).fetchone()[0] == 1


def test_migration_on_empty_connection_creates_table():
    """Conexao sem schema.sql rodado (simula vault antiga) recebe
    a tabela via migration isolada."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Nao rodamos schema.sql — forcamos migration direto
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            file_id TEXT PRIMARY KEY,
            obra TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER,
            semantic_status TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obra TEXT, provider TEXT, endpoint TEXT,
            request_hash TEXT, response_hash TEXT,
            request_json TEXT, response_json TEXT,
            tokens_input INTEGER, tokens_output INTEGER,
            cost_usd REAL,
            started_at TEXT, finished_at TEXT,
            error_message TEXT, created_at TEXT
        );
        """
    )
    _migrate_financial_records_sprint4_op8(conn)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='financial_records'"
    )
    assert cur.fetchone() is not None
    conn.close()


def test_insert_happy_path_with_all_fields(db):
    """Insert completo de comprovante PIX round-trip correto."""
    _seed_image_file(db)
    db.execute(
        """INSERT INTO financial_records (
            obra, source_file_id, doc_type, valor_centavos, moeda,
            data_transacao, hora_transacao,
            pagador_nome, pagador_doc, recebedor_nome, recebedor_doc,
            chave_pix, descricao,
            instituicao_origem, instituicao_destino,
            raw_ocr_text, confidence, api_call_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "OBRA_TEST", "f_test_image", "pix", 350000, "BRL",
            "2026-04-06", "11:13:24",
            "Lucas Ferreira", "***.393.776-**", "Everaldo Santos",
            "***.456.789-**", "everaldo@example.com",
            "50% de sinal serralheria",
            "Banco do Brasil", "Itau",
            "COMPROVANTE DE PIX\nValor: R$ 3.500,00\n...",
            0.92, None, "2026-04-22T00:00:00Z",
        ),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM financial_records WHERE source_file_id=?",
        ("f_test_image",),
    ).fetchone()
    assert row["doc_type"] == "pix"
    assert row["valor_centavos"] == 350000
    assert row["moeda"] == "BRL"
    assert row["descricao"] == "50% de sinal serralheria"
    assert row["confidence"] == pytest.approx(0.92)


def test_unique_obra_source_file_id_prevents_duplicate(db):
    """UNIQUE(obra, source_file_id) bloqueia 2a insercao."""
    _seed_image_file(db)
    db.execute(
        """INSERT INTO financial_records (
            obra, source_file_id, doc_type, created_at
        ) VALUES (?, ?, ?, ?)""",
        ("OBRA_TEST", "f_test_image", "pix", "2026-04-22T00:00:00Z"),
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO financial_records (
                obra, source_file_id, doc_type, created_at
            ) VALUES (?, ?, ?, ?)""",
            ("OBRA_TEST", "f_test_image", "ted",
             "2026-04-22T01:00:00Z"),
        )


def test_foreign_key_to_files_enforced(db):
    """FK para files.file_id bloqueia insert com file_id invalido
    (PRAGMA foreign_keys ja esta ON apos init_db)."""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO financial_records (
                obra, source_file_id, doc_type, created_at
            ) VALUES (?, ?, ?, ?)""",
            ("OBRA_TEST", "f_inexistente", "pix",
             "2026-04-22T00:00:00Z"),
        )


def test_not_null_constraints(db):
    """obra, source_file_id, created_at sao NOT NULL."""
    _seed_image_file(db)
    # obra NULL
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO financial_records (
                obra, source_file_id, created_at
            ) VALUES (?, ?, ?)""",
            (None, "f_test_image", "2026-04-22T00:00:00Z"),
        )
    # source_file_id NULL
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO financial_records (
                obra, source_file_id, created_at
            ) VALUES (?, ?, ?)""",
            ("OBRA_TEST", None, "2026-04-22T00:00:00Z"),
        )
    # created_at NULL
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO financial_records (
                obra, source_file_id, created_at
            ) VALUES (?, ?, ?)""",
            ("OBRA_TEST", "f_test_image", None),
        )
