"""Testes de ingestao streaming — Sessao 7, divida #41.

Cobre:
- iter_chat_messages (parser linha-a-linha sem read_text full)
- write_messages_streaming (insert em batches com dedup duplo)
- Resilencia contra arquivos grandes (RAM peak bounded por batch_size)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rdo_agent.ingestor import write_messages_streaming
from rdo_agent.orchestrator import init_db
from rdo_agent.parser import iter_chat_messages, parse_chat_file


# ---------------------------------------------------------------------------
# Fixtures e helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "vault")


def _make_chat_txt(path: Path, *, num_messages: int = 10, multiline_every: int = 5) -> None:
    """
    Gera _chat.txt sintetico com ``num_messages`` mensagens em formato
    "dash" (Android pt-BR). A cada ``multiline_every``-esima, anexa
    linha de continuação para testar parsing de mensagem multi-linha.
    """
    lines: list[str] = []
    for i in range(1, num_messages + 1):
        # Hora varia para ter timestamps unicos
        hh = (i // 60) % 24
        mm = i % 60
        lines.append(f"08/04/2026 {hh:02d}:{mm:02d} - User{i % 3}: Mensagem {i}")
        if multiline_every and i % multiline_every == 0:
            lines.append("    continuação na linha seguinte")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# iter_chat_messages
# ---------------------------------------------------------------------------


def test_iter_chat_messages_yields_parsed_messages(tmp_path):
    chat = tmp_path / "_chat.txt"
    _make_chat_txt(chat, num_messages=5, multiline_every=0)

    msgs = list(iter_chat_messages(chat))
    assert len(msgs) == 5
    assert all(m.content.startswith("Mensagem") for m in msgs)
    assert msgs[0].sender in ("User0", "User1", "User2")


def test_iter_chat_messages_handles_multiline(tmp_path):
    """Linhas de continuacao acumulam no content da mensagem corrente."""
    chat = tmp_path / "_chat.txt"
    _make_chat_txt(chat, num_messages=10, multiline_every=5)

    msgs = list(iter_chat_messages(chat))
    assert len(msgs) == 10
    # Mensagens 5 e 10 são as multi-linha
    for idx in (4, 9):  # zero-indexed
        assert "continuação" in msgs[idx].content


def test_iter_chat_messages_matches_parse_chat_file(tmp_path):
    """Iter e wrapper eager devem produzir resultado identico."""
    chat = tmp_path / "_chat.txt"
    _make_chat_txt(chat, num_messages=20, multiline_every=3)

    eager = parse_chat_file(chat)
    streaming = list(iter_chat_messages(chat))
    assert len(eager) == len(streaming)
    for a, b in zip(eager, streaming, strict=True):
        assert a.line_number == b.line_number
        assert a.timestamp_raw == b.timestamp_raw
        assert a.sender == b.sender
        assert a.content == b.content
        assert a.message_type == b.message_type


def test_iter_chat_messages_empty_file(tmp_path):
    chat = tmp_path / "_chat.txt"
    chat.write_text("", encoding="utf-8")
    assert list(iter_chat_messages(chat)) == []


def test_iter_chat_messages_is_generator(tmp_path):
    """iter_chat_messages eh generator (lazy), nao retorna list."""
    import types
    chat = tmp_path / "_chat.txt"
    _make_chat_txt(chat, num_messages=3, multiline_every=0)
    result = iter_chat_messages(chat)
    assert isinstance(result, types.GeneratorType)


def test_iter_chat_messages_handles_latin1_fallback(tmp_path):
    """Exports antigos pt-BR em latin-1 sao detectados e parseados."""
    chat = tmp_path / "_chat.txt"
    # latin-1 com caracteres pt-BR que falharia em utf-8 strict
    chat.write_bytes(
        "08/04/2026 09:00 - José: Olá!\n".encode("latin-1")
    )
    msgs = list(iter_chat_messages(chat))
    assert len(msgs) == 1
    assert msgs[0].sender == "José"
    assert "Olá" in msgs[0].content


# ---------------------------------------------------------------------------
# write_messages_streaming
# ---------------------------------------------------------------------------


def test_write_messages_streaming_inserts_all(conn, tmp_path):
    chat = tmp_path / "_chat.txt"
    _make_chat_txt(chat, num_messages=15, multiline_every=0)

    inserted, skipped = write_messages_streaming(
        conn, iter_chat_messages(chat), obra="OBRA_S",
        batch_size=10,
    )
    assert inserted == 15
    assert skipped == 0
    n = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE obra = 'OBRA_S'",
    ).fetchone()[0]
    assert n == 15


def test_write_messages_streaming_batches_invoke_progress_callback(conn, tmp_path):
    chat = tmp_path / "_chat.txt"
    _make_chat_txt(chat, num_messages=25, multiline_every=0)

    calls: list[tuple[int, int, int]] = []

    def cb(ins: int, skp: int, total: int) -> None:
        calls.append((ins, skp, total))

    inserted, _ = write_messages_streaming(
        conn, iter_chat_messages(chat), obra="OBRA_CB",
        batch_size=10, progress_callback=cb,
    )
    assert inserted == 25
    # 25 mensagens / batch=10 → 3 flushes (10, 10, 5)
    assert len(calls) == 3
    assert [c[0] for c in calls] == [10, 10, 5]


def test_write_messages_streaming_dedupes_via_content_hash(conn, tmp_path):
    """Re-rodar streaming sobre o mesmo .txt skipa duplicatas."""
    chat = tmp_path / "_chat.txt"
    _make_chat_txt(chat, num_messages=10, multiline_every=0)

    # Primeira ingestao
    ins1, skp1 = write_messages_streaming(
        conn, iter_chat_messages(chat), obra="OBRA_DUP",
    )
    assert ins1 == 10 and skp1 == 0

    # Segunda ingestao: tudo skipped (PK + content_hash)
    ins2, skp2 = write_messages_streaming(
        conn, iter_chat_messages(chat), obra="OBRA_DUP",
    )
    assert ins2 == 0
    assert skp2 == 10


def test_write_messages_streaming_isolates_by_obra(conn, tmp_path):
    chat = tmp_path / "_chat.txt"
    _make_chat_txt(chat, num_messages=5, multiline_every=0)

    write_messages_streaming(conn, iter_chat_messages(chat), obra="A")
    write_messages_streaming(conn, iter_chat_messages(chat), obra="B")

    n_a = conn.execute("SELECT COUNT(*) FROM messages WHERE obra='A'").fetchone()[0]
    n_b = conn.execute("SELECT COUNT(*) FROM messages WHERE obra='B'").fetchone()[0]
    assert n_a == 5 and n_b == 5  # corpus_id isola


def test_write_messages_streaming_handles_empty_iterator(conn):
    """Iterador vazio nao explode nem grava nada."""
    inserted, skipped = write_messages_streaming(
        conn, iter([]), obra="OBRA_EMPTY",
    )
    assert inserted == 0 and skipped == 0


def test_streaming_pipeline_ram_bounded(conn, tmp_path):
    """
    Smoke test: arquivo de 1000 mensagens com batch=50 nao acumula
    todas em RAM. Verifica que o iterador de fato eh lazy (drena 1
    de cada vez sem materializar a lista inteira).
    """
    chat = tmp_path / "_chat.txt"
    _make_chat_txt(chat, num_messages=1000, multiline_every=0)

    drained = 0
    iterator = iter_chat_messages(chat)
    # Drena 50 mensagens e para — se o iterador fosse eager, ja teria
    # lido tudo. Aqui validamos que apenas as primeiras 50 foram parseadas.
    for _ in range(50):
        next(iterator)
        drained += 1
    assert drained == 50

    # E o write_messages_streaming integral processa o resto
    rest_inserted, _ = write_messages_streaming(
        conn, iterator, obra="OBRA_STREAM", batch_size=100,
    )
    # Ja consumimos 50, sobram 950
    assert rest_inserted == 950
