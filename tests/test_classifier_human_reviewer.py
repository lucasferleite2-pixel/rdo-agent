"""Testes do revisor humano — Sprint 3 Camada 2.

Injeta prompt_fn / edit_fn / print_fn para simular operador sem TTY,
valida transicoes de estado no DB e contadores retornados.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

import pytest

from rdo_agent.classifier.human_reviewer import review_pending
from rdo_agent.orchestrator import init_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _insert_audio_files(conn: sqlite3.Connection, obra: str, n: int) -> None:
    """Cria n pares (audio, transcription .txt derivado, transcription row)
    + n linhas em classifications com status='pending_review'."""
    now = "2026-04-20T00:00:00Z"
    for i in range(n):
        audio_fid = f"file_audio_{i:02d}"
        trans_fid = f"file_trans_{i:02d}"
        conn.execute(
            """INSERT INTO files (
                file_id, obra, file_path, file_type, sha256, size_bytes,
                semantic_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                audio_fid, obra, f"10_media/audio{i:02d}.opus", "audio",
                ("a" + str(i)) * 32, 1000, "done", now,
            ),
        )
        conn.execute(
            """INSERT INTO files (
                file_id, obra, file_path, file_type, sha256, size_bytes,
                derived_from, derivation_method, semantic_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trans_fid, obra, f"20_transcriptions/audio{i:02d}.txt",
                "text", ("b" + str(i)) * 32, 500, audio_fid,
                "whisper-1", "awaiting_classification", now,
            ),
        )
        conn.execute(
            """INSERT INTO transcriptions (
                obra, file_id, text, language, confidence, low_confidence,
                api_call_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (obra, trans_fid, f"texto original {i}", "portuguese", 0.5, 0, None, now),
        )
        conn.execute(
            """INSERT INTO classifications (
                obra, source_file_id, source_type,
                quality_flag, quality_reasoning, human_review_needed,
                quality_api_call_id, quality_model,
                source_sha256, semantic_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                obra, trans_fid, "transcription",
                "suspeita", f"motivo {i}", 1,
                None, "gpt-4o-mini-2024-07-18",
                "c" * 64, "pending_review", now,
            ),
        )
    conn.commit()


@pytest.fixture
def prepared_db(tmp_path) -> sqlite3.Connection:
    conn = init_db(tmp_path)
    return conn


def _queue_prompt(actions: list[str]) -> Callable[[str], str]:
    it = iter(actions)

    def fn(_msg: str) -> str:
        return next(it)

    return fn


def _sink_print() -> tuple[Callable[[str], None], list[str]]:
    buf: list[str] = []

    def fn(s: str) -> None:
        buf.append(s)

    return fn, buf


def _status_of(conn: sqlite3.Connection, cid: int) -> dict[str, Any]:
    row = conn.execute(
        """SELECT semantic_status, human_reviewed, human_corrected_text,
                  human_reviewed_at, updated_at
           FROM classifications WHERE id = ?""",
        (cid,),
    ).fetchone()
    return dict(zip(
        ("semantic_status", "human_reviewed", "human_corrected_text",
         "human_reviewed_at", "updated_at"),
        row, strict=True,
    ))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_pending_review_shows_friendly_message(prepared_db):
    """Sem linhas pending_review -> mensagem amigavel, stats zerado."""
    print_fn, buf = _sink_print()
    stats = review_pending(
        prepared_db, "EVERALDO",
        prompt_fn=_queue_prompt([]),
        edit_fn=lambda s: s,
        print_fn=print_fn,
    )
    assert stats["total"] == 0
    assert stats["accepted"] == 0
    assert stats["quit_early"] is False
    assert any("Nenhuma classification" in line for line in buf)


def test_accept_transitions_to_pending_classify(prepared_db):
    """A -> human_reviewed=1, semantic_status=pending_classify,
    human_reviewed_at preenchido, human_corrected_text permanece NULL."""
    _insert_audio_files(prepared_db, "EVERALDO", 1)
    print_fn, _ = _sink_print()

    stats = review_pending(
        prepared_db, "EVERALDO",
        prompt_fn=_queue_prompt(["A"]),
        edit_fn=lambda s: s,
        print_fn=print_fn,
    )
    assert stats["accepted"] == 1
    row = _status_of(prepared_db, 1)
    assert row["semantic_status"] == "pending_classify"
    assert row["human_reviewed"] == 1
    assert row["human_reviewed_at"] is not None
    assert row["human_corrected_text"] is None


def test_edit_saves_corrected_text(prepared_db):
    """E -> human_corrected_text setado com output do edit_fn,
    semantic_status=pending_classify."""
    _insert_audio_files(prepared_db, "EVERALDO", 1)
    print_fn, _ = _sink_print()

    stats = review_pending(
        prepared_db, "EVERALDO",
        prompt_fn=_queue_prompt(["E"]),
        edit_fn=lambda initial: initial + " -- EDITADO",
        print_fn=print_fn,
    )
    assert stats["edited"] == 1
    row = _status_of(prepared_db, 1)
    assert row["semantic_status"] == "pending_classify"
    assert row["human_reviewed"] == 1
    assert row["human_corrected_text"] == "texto original 0 -- EDITADO"


def test_reject_transitions_to_rejected(prepared_db):
    """R -> semantic_status=rejected, human_reviewed=1."""
    _insert_audio_files(prepared_db, "EVERALDO", 1)
    print_fn, _ = _sink_print()

    stats = review_pending(
        prepared_db, "EVERALDO",
        prompt_fn=_queue_prompt(["R"]),
        edit_fn=lambda s: s,
        print_fn=print_fn,
    )
    assert stats["rejected"] == 1
    row = _status_of(prepared_db, 1)
    assert row["semantic_status"] == "rejected"
    assert row["human_reviewed"] == 1


def test_skip_preserves_pending_review(prepared_db):
    """S -> nenhum UPDATE; row continua pending_review, human_reviewed=0."""
    _insert_audio_files(prepared_db, "EVERALDO", 1)
    print_fn, _ = _sink_print()

    stats = review_pending(
        prepared_db, "EVERALDO",
        prompt_fn=_queue_prompt(["S"]),
        edit_fn=lambda s: s,
        print_fn=print_fn,
    )
    assert stats["skipped"] == 1
    row = _status_of(prepared_db, 1)
    assert row["semantic_status"] == "pending_review"
    assert row["human_reviewed"] == 0
    assert row["updated_at"] is None


def test_quit_breaks_loop_early_and_preserves_remaining(prepared_db):
    """Q na primeira linha -> nao processa as demais."""
    _insert_audio_files(prepared_db, "EVERALDO", 3)
    print_fn, _ = _sink_print()

    stats = review_pending(
        prepared_db, "EVERALDO",
        prompt_fn=_queue_prompt(["Q"]),
        edit_fn=lambda s: s,
        print_fn=print_fn,
    )
    assert stats["quit_early"] is True
    # nenhuma das 3 foi modificada
    remaining = prepared_db.execute(
        "SELECT COUNT(*) FROM classifications WHERE semantic_status='pending_review'",
    ).fetchone()[0]
    assert remaining == 3


def test_invalid_action_reprompts_until_valid(prepared_db):
    """Entradas ruins nao contam; loop insiste ate receber A/E/R/S/Q."""
    _insert_audio_files(prepared_db, "EVERALDO", 1)
    print_fn, buf = _sink_print()

    stats = review_pending(
        prepared_db, "EVERALDO",
        prompt_fn=_queue_prompt(["x", "", "zzz", "A"]),
        edit_fn=lambda s: s,
        print_fn=print_fn,
    )
    assert stats["accepted"] == 1
    # mensagem de erro apareceu para pelo menos uma entrada invalida
    assert any("Acao invalida" in line for line in buf)


def test_edit_with_empty_original_still_allows_edit(prepared_db):
    """Mesmo com transcription.text vazia, E funciona (edit_fn recebe '')."""
    _insert_audio_files(prepared_db, "EVERALDO", 1)
    prepared_db.execute(
        "UPDATE transcriptions SET text='' WHERE file_id=?", ("file_trans_00",),
    )
    prepared_db.commit()

    captured: dict[str, str] = {}

    def edit_fn(initial: str) -> str:
        captured["initial"] = initial
        return "conteudo manual"

    print_fn, _ = _sink_print()
    stats = review_pending(
        prepared_db, "EVERALDO",
        prompt_fn=_queue_prompt(["E"]),
        edit_fn=edit_fn,
        print_fn=print_fn,
    )
    assert stats["edited"] == 1
    assert captured["initial"] == ""
    row = _status_of(prepared_db, 1)
    assert row["human_corrected_text"] == "conteudo manual"


def test_mixed_sequence_with_multiple_rows(prepared_db):
    """Sequencia E, R, A em 3 rows -> contadores e estados corretos."""
    _insert_audio_files(prepared_db, "EVERALDO", 3)
    print_fn, _ = _sink_print()

    stats = review_pending(
        prepared_db, "EVERALDO",
        prompt_fn=_queue_prompt(["E", "R", "A"]),
        edit_fn=lambda initial: initial + " fix",
        print_fn=print_fn,
    )
    assert stats["edited"] == 1
    assert stats["rejected"] == 1
    assert stats["accepted"] == 1
    rows = prepared_db.execute(
        "SELECT id, semantic_status, human_reviewed FROM classifications ORDER BY id",
    ).fetchall()
    assert rows[0]["semantic_status"] == "pending_classify"
    assert rows[1]["semantic_status"] == "rejected"
    assert rows[2]["semantic_status"] == "pending_classify"
    assert all(r["human_reviewed"] == 1 for r in rows)


def test_edit_on_row_with_previous_corrected_text_uses_it_as_initial(prepared_db):
    """Se human_corrected_text ja existe, edit_fn recebe esse valor (nao a
    transcription original)."""
    _insert_audio_files(prepared_db, "EVERALDO", 1)
    prepared_db.execute(
        "UPDATE classifications SET human_corrected_text=? WHERE id=?",
        ("correcao anterior", 1),
    )
    prepared_db.commit()
    captured: dict[str, str] = {}

    def edit_fn(initial: str) -> str:
        captured["initial"] = initial
        return initial + " +2"

    print_fn, _ = _sink_print()
    stats = review_pending(
        prepared_db, "EVERALDO",
        prompt_fn=_queue_prompt(["E"]),
        edit_fn=edit_fn,
        print_fn=print_fn,
    )
    assert stats["edited"] == 1
    assert captured["initial"] == "correcao anterior"
    row = _status_of(prepared_db, 1)
    assert row["human_corrected_text"] == "correcao anterior +2"
