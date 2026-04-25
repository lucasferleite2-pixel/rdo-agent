"""Testes de dedup defensivo via content_hash — Sessao 6, divida #43.

Camadas de dedup em messages:
1. PRIMARY KEY message_id determinístico (msg_{obra}_L{line_number}) —
   protege contra re-ingest de ZIP idêntico.
2. UNIQUE(obra, content_hash) — protege contra ZIP editado em que
   line_numbers deslocaram mas content é o mesmo.

Camada de dedup em files (legacy, ainda funciona):
3. PRIMARY KEY file_id = f_{sha256[:12]} via INSERT OR IGNORE.
"""

from __future__ import annotations

import sqlite3

import pytest

from rdo_agent.orchestrator import (
    compute_message_content_hash,
    init_db,
)


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "vault")


# ---------------------------------------------------------------------------
# compute_message_content_hash (puro)
# ---------------------------------------------------------------------------


def test_content_hash_is_deterministic():
    h1 = compute_message_content_hash("2026-04-08T09:00:00Z", "Lucas", "Bom dia")
    h2 = compute_message_content_hash("2026-04-08T09:00:00Z", "Lucas", "Bom dia")
    assert h1 == h2


def test_content_hash_is_16_hex_chars():
    h = compute_message_content_hash("2026-04-08T09:00:00Z", "Lucas", "Bom dia")
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


def test_content_hash_differs_for_different_content():
    h1 = compute_message_content_hash("2026-04-08T09:00:00Z", "Lucas", "Bom dia")
    h2 = compute_message_content_hash("2026-04-08T09:00:00Z", "Lucas", "Boa tarde")
    h3 = compute_message_content_hash("2026-04-08T09:00:00Z", "Everaldo", "Bom dia")
    h4 = compute_message_content_hash("2026-04-08T10:00:00Z", "Lucas", "Bom dia")
    assert len({h1, h2, h3, h4}) == 4


def test_content_hash_handles_none_sender_and_content():
    """sender ou content NULL nao quebra o hash (concatena vazio)."""
    h = compute_message_content_hash("2026-04-08T09:00:00Z", None, None)
    assert len(h) == 16


# ---------------------------------------------------------------------------
# Migration: backfill em rows existentes
# ---------------------------------------------------------------------------


def _insert_legacy_message(
    conn: sqlite3.Connection, *,
    message_id: str, obra: str, ts: str, sender: str, content: str,
) -> None:
    """Insere mensagem 'legada' SEM content_hash (simula DB pre-Sessao 6)."""
    conn.execute(
        """
        INSERT OR REPLACE INTO messages
            (message_id, obra, timestamp_whatsapp, sender, content,
             media_ref, is_deleted, is_edited, is_sticker, raw_line,
             created_at, content_hash)
        VALUES (?, ?, ?, ?, ?, NULL, 0, 0, 0, NULL, ?, NULL)
        """,
        (message_id, obra, ts, sender, content, "2026-04-08T00:00:00Z"),
    )


def test_migration_backfills_existing_rows(tmp_path):
    """Vault pre-Sessao 6: ALTER TABLE inicia coluna NULL; migration preenche."""
    conn = init_db(tmp_path / "vault_legacy")

    # Force NULL hash em uma row para simular legacy
    conn.execute(
        """
        INSERT INTO messages
            (message_id, obra, timestamp_whatsapp, sender, content,
             media_ref, is_deleted, is_edited, is_sticker, raw_line,
             created_at, content_hash)
        VALUES (?, ?, ?, ?, ?, NULL, 0, 0, 0, NULL, ?, NULL)
        """,
        ("msg_X_L0001", "X", "2026-04-08T09:00:00Z", "Lucas", "Bom dia",
         "2026-04-08T00:00:00Z"),
    )
    conn.commit()
    conn.close()

    # Re-init aplica migrations (idempotente; backfill cobre row legacy)
    conn = init_db(tmp_path / "vault_legacy")
    row = conn.execute(
        "SELECT content_hash FROM messages WHERE message_id = ?",
        ("msg_X_L0001",),
    ).fetchone()
    assert row["content_hash"] is not None
    expected = compute_message_content_hash(
        "2026-04-08T09:00:00Z", "Lucas", "Bom dia",
    )
    assert row["content_hash"] == expected
    conn.close()


def test_migration_idempotent_when_column_already_exists(conn):
    """Re-rodar init_db em DB ja migrado nao quebra (CREATE INDEX IF NOT)."""
    # Migration ja rodou no fixture; chamar de novo nao deve falhar
    from rdo_agent.orchestrator import _migrate_sessao6_message_content_hash

    _migrate_sessao6_message_content_hash(conn)
    _migrate_sessao6_message_content_hash(conn)  # 2x ok
    _migrate_sessao6_message_content_hash(conn)  # 3x ok

    # Coluna existe
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(messages)")
    }
    assert "content_hash" in cols


# ---------------------------------------------------------------------------
# Dedup em INSERT (camada 2 — content_hash via UNIQUE)
# ---------------------------------------------------------------------------


def test_dedup_messages_same_content_via_content_hash(conn):
    """
    Duas mensagens com content identico mas message_id diferente
    (ex: ZIP editado, line_number deslocou) — dedup via UNIQUE(obra,
    content_hash) bloqueia a segunda.
    """
    h = compute_message_content_hash("2026-04-08T09:00:00Z", "Lucas", "Bom dia")

    conn.execute(
        """
        INSERT INTO messages
            (message_id, obra, timestamp_whatsapp, sender, content,
             media_ref, is_deleted, is_edited, is_sticker, raw_line,
             created_at, content_hash)
        VALUES (?, ?, ?, ?, ?, NULL, 0, 0, 0, NULL, ?, ?)
        """,
        ("msg_X_L0001", "X", "2026-04-08T09:00:00Z", "Lucas", "Bom dia",
         "2026-04-08T00:00:00Z", h),
    )

    # Tentar inserir mesmo content em outro line_number (msg_id diferente)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO messages
                (message_id, obra, timestamp_whatsapp, sender, content,
                 media_ref, is_deleted, is_edited, is_sticker, raw_line,
                 created_at, content_hash)
            VALUES (?, ?, ?, ?, ?, NULL, 0, 0, 0, NULL, ?, ?)
            """,
            ("msg_X_L0099", "X", "2026-04-08T09:00:00Z", "Lucas", "Bom dia",
             "2026-04-08T00:00:00Z", h),  # mesmo hash → conflito
        )


def test_dedup_messages_via_insert_or_ignore_silently_skips(conn):
    """
    INSERT OR IGNORE com hash duplicado nao falha — apenas skip
    silencioso (cur.rowcount = 0). Caminho usado pelo ingestor.
    """
    h = compute_message_content_hash("2026-04-08T09:00:00Z", "Lucas", "Bom dia")
    conn.execute(
        """
        INSERT OR IGNORE INTO messages
            (message_id, obra, timestamp_whatsapp, sender, content,
             media_ref, is_deleted, is_edited, is_sticker, raw_line,
             created_at, content_hash)
        VALUES (?, ?, ?, ?, ?, NULL, 0, 0, 0, NULL, ?, ?)
        """,
        ("msg_X_L0001", "X", "2026-04-08T09:00:00Z", "Lucas", "Bom dia",
         "2026-04-08T00:00:00Z", h),
    )
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO messages
            (message_id, obra, timestamp_whatsapp, sender, content,
             media_ref, is_deleted, is_edited, is_sticker, raw_line,
             created_at, content_hash)
        VALUES (?, ?, ?, ?, ?, NULL, 0, 0, 0, NULL, ?, ?)
        """,
        ("msg_X_L0099", "X", "2026-04-08T09:00:00Z", "Lucas", "Bom dia",
         "2026-04-08T00:00:00Z", h),
    )
    assert cur.rowcount == 0  # skip silencioso
    # So 1 row no DB
    n = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE obra = 'X'",
    ).fetchone()[0]
    assert n == 1


def test_dedup_does_not_collide_across_obras(conn):
    """Mesma content + obras diferentes = registros independentes."""
    h = compute_message_content_hash("2026-04-08T09:00:00Z", "Lucas", "Bom dia")
    for obra in ("CASE_A", "CASE_B"):
        conn.execute(
            """
            INSERT INTO messages
                (message_id, obra, timestamp_whatsapp, sender, content,
                 media_ref, is_deleted, is_edited, is_sticker, raw_line,
                 created_at, content_hash)
            VALUES (?, ?, ?, ?, ?, NULL, 0, 0, 0, NULL, ?, ?)
            """,
            (f"msg_{obra}_L0001", obra, "2026-04-08T09:00:00Z",
             "Lucas", "Bom dia", "2026-04-08T00:00:00Z", h),
        )
    n_a = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE obra = 'CASE_A'",
    ).fetchone()[0]
    n_b = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE obra = 'CASE_B'",
    ).fetchone()[0]
    assert n_a == 1 and n_b == 1


# ---------------------------------------------------------------------------
# Camada 3 — files dedup via PK (sha256-based file_id)
# ---------------------------------------------------------------------------


def test_files_dedup_via_pk_already_works(conn):
    """
    files.file_id = f_{sha256[:12]} (deterministico por content).
    INSERT OR IGNORE silenciosamente skipa duplicatas — comportamento
    legado preservado, validado aqui pra protecao contra regressao.
    """
    file_id = "f_abcdef012345"
    sha = "abcdef012345" + ("0" * 52)  # 64 hex chars
    conn.execute(
        """
        INSERT OR IGNORE INTO files
            (file_id, obra, file_path, file_type, sha256,
             size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (file_id, "X", "p/a.opus", "audio", sha, 100, "done",
         "2026-04-08T00:00:00Z"),
    )
    # Re-inserir mesmo file_id não falha (INSERT OR IGNORE)
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO files
            (file_id, obra, file_path, file_type, sha256,
             size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (file_id, "X", "p/a.opus", "audio", sha, 100, "done",
         "2026-04-08T00:00:00Z"),
    )
    assert cur.rowcount == 0
    n = conn.execute(
        "SELECT COUNT(*) FROM files WHERE file_id = ?", (file_id,),
    ).fetchone()[0]
    assert n == 1
