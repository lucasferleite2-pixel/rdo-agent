"""Testes da integracao financial_records no RDO — Sprint 4 Op10.

Cobrem:
  - RDO de data com PIX inclui secao "Pagamentos registrados (comprovantes)"
  - RDO de data sem PIX NAO inclui a secao
  - Multi-PIX: tabela lista todos em ordem cronologica (hora_transacao)
  - Valores formatados em BRL (R$ 3.500,00, R$ 30,00)
  - Total do dia eh soma dos comprovantes
  - Descricao e nomes truncados se muito longos
  - `_format_brl` helper puro
  - `_fetch_financial_records_for_date` respeita obra + data
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from rdo_agent.orchestrator import init_db

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import generate_rdo_piloto as rdo_piloto  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_minimal_classification(conn: sqlite3.Connection, ts_date: str) -> None:
    """Cria 1 classification minima pra a data (pre-requisito do RDO)."""
    now = "2026-04-22T00:00:00Z"
    ts = f"{ts_date}T09:00:00Z"
    conn.execute(
        """INSERT INTO messages (message_id, obra, timestamp_whatsapp,
        sender, content, media_ref, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("msg_1", "OBRA_FIN", ts, "Lucas", "audio anexo",
         "AUDIO-01.opus", now),
    )
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, referenced_by_message, timestamp_resolved,
        timestamp_source, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f_audio", "OBRA_FIN", "10_media/a.opus", "audio", "a"*64, 100,
         "msg_1", ts, "whatsapp_txt", "done", now),
    )
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, derived_from, derivation_method, referenced_by_message,
        timestamp_resolved, timestamp_source, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f_trans", "OBRA_FIN", "20_transcriptions/a.txt", "text", "b"*64,
         50, "f_audio", "whisper-1", "msg_1", ts, "whatsapp_txt",
         "awaiting_classification", now),
    )
    conn.execute(
        """INSERT INTO transcriptions (obra, file_id, text, language,
        confidence, low_confidence, api_call_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("OBRA_FIN", "f_trans", "texto exemplo", "portuguese", 0.6, 0,
         None, now),
    )
    conn.execute(
        """INSERT INTO classifications (
            obra, source_file_id, source_type,
            quality_flag, quality_reasoning, human_review_needed,
            human_reviewed, categories, confidence_model, reasoning,
            classifier_api_call_id, classifier_model,
            quality_api_call_id, quality_model,
            source_sha256, semantic_status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("OBRA_FIN", "f_trans", "transcription",
         "coerente", "ok", 0, 0, json.dumps(["cronograma"]), 0.8, "x",
         None, "gpt-4o-mini-2024-07-18",
         None, "gpt-4o-mini-2024-07-18",
         "c"*64, "classified", now, now),
    )
    conn.commit()


def _seed_image_and_financial_record(
    conn: sqlite3.Connection,
    *,
    file_id: str,
    data_transacao: str,
    hora_transacao: str = "11:13:24",
    valor_centavos: int = 350000,
    doc_type: str = "pix",
    pagador: str = "Vale Nobre LTDA",
    recebedor: str = "Everaldo Caitano Baia",
    descricao: str = "50% sinal serralheria",
) -> None:
    now = "2026-04-22T00:00:00Z"
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type,
        sha256, size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (file_id, "OBRA_FIN", f"10_media/{file_id}.jpg", "image",
         ("s"*63 + file_id[-1]), 1000, "ocr_extracted", now),
    )
    conn.execute(
        """INSERT INTO financial_records (obra, source_file_id, doc_type,
        valor_centavos, moeda, data_transacao, hora_transacao,
        pagador_nome, recebedor_nome, descricao, confidence,
        api_call_id, created_at)
        VALUES (?, ?, ?, ?, 'BRL', ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("OBRA_FIN", file_id, doc_type, valor_centavos,
         data_transacao, hora_transacao, pagador, recebedor,
         descricao, 0.95, None, now),
    )
    conn.commit()


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path)


# ---------------------------------------------------------------------------
# _format_brl helper (puro)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cents,expected", [
    (350000, "R$ 3.500,00"),
    (3000, "R$ 30,00"),
    (1, "R$ 0,01"),
    (0, "R$ 0,00"),
    (100, "R$ 1,00"),
    (1234567890, "R$ 12.345.678,90"),
    (None, "n/a"),
    (-500, "-R$ 5,00"),
])
def test_format_brl(cents, expected):
    assert rdo_piloto._format_brl(cents) == expected


# ---------------------------------------------------------------------------
# _fetch_financial_records_for_date
# ---------------------------------------------------------------------------


def test_fetch_returns_records_for_date(db):
    _seed_image_and_financial_record(
        db, file_id="f_pix_1", data_transacao="2026-04-06",
    )
    _seed_image_and_financial_record(
        db, file_id="f_pix_2", data_transacao="2026-04-10",
    )
    rows = rdo_piloto._fetch_financial_records_for_date(
        db, "OBRA_FIN", "2026-04-06",
    )
    assert len(rows) == 1
    assert rows[0]["data_transacao"] == "2026-04-06"


def test_fetch_orders_by_hora_transacao(db):
    _seed_image_and_financial_record(
        db, file_id="f_pix_b", data_transacao="2026-04-06",
        hora_transacao="14:00:00", descricao="segundo",
    )
    _seed_image_and_financial_record(
        db, file_id="f_pix_a", data_transacao="2026-04-06",
        hora_transacao="09:00:00", descricao="primeiro",
    )
    rows = rdo_piloto._fetch_financial_records_for_date(
        db, "OBRA_FIN", "2026-04-06",
    )
    assert len(rows) == 2
    assert rows[0]["descricao"] == "primeiro"
    assert rows[1]["descricao"] == "segundo"


def test_fetch_empty_for_date_without_records(db):
    rows = rdo_piloto._fetch_financial_records_for_date(
        db, "OBRA_FIN", "2026-04-06",
    )
    assert rows == []


def test_fetch_isolates_by_obra(db):
    _seed_image_and_financial_record(
        db, file_id="f_pix_x", data_transacao="2026-04-06",
    )
    rows = rdo_piloto._fetch_financial_records_for_date(
        db, "OUTRA_OBRA", "2026-04-06",
    )
    assert rows == []


# ---------------------------------------------------------------------------
# _render_financial_section (pure, no DB)
# ---------------------------------------------------------------------------


def test_render_empty_section_returns_empty_list():
    assert rdo_piloto._render_financial_section([]) == []


def test_render_section_contains_header_and_table(db):
    _seed_image_and_financial_record(
        db, file_id="f_pix_1", data_transacao="2026-04-06",
    )
    records = rdo_piloto._fetch_financial_records_for_date(
        db, "OBRA_FIN", "2026-04-06",
    )
    lines = rdo_piloto._render_financial_section(records)
    text = "\n".join(lines)
    assert "## 💰 Pagamentos registrados (comprovantes)" in text
    assert "| Hora | Valor | Tipo | De → Para | Descrição |" in text
    assert "R$ 3.500,00" in text
    assert "PIX" in text
    assert "Everaldo" in text


def test_render_section_includes_total_do_dia(db):
    _seed_image_and_financial_record(
        db, file_id="f_pix_1", data_transacao="2026-04-06",
        valor_centavos=350000,
    )
    _seed_image_and_financial_record(
        db, file_id="f_pix_2", data_transacao="2026-04-06",
        hora_transacao="14:00:00", valor_centavos=150000,
    )
    records = rdo_piloto._fetch_financial_records_for_date(
        db, "OBRA_FIN", "2026-04-06",
    )
    lines = rdo_piloto._render_financial_section(records)
    text = "\n".join(lines)
    assert "**Total do dia:** R$ 5.000,00" in text


def test_render_truncates_long_pagador(db):
    _seed_image_and_financial_record(
        db, file_id="f_pix_t", data_transacao="2026-04-06",
        pagador="A" * 50,
    )
    records = rdo_piloto._fetch_financial_records_for_date(
        db, "OBRA_FIN", "2026-04-06",
    )
    lines = rdo_piloto._render_financial_section(records)
    text = "\n".join(lines)
    # 28 chars + ellipsis
    assert "A" * 50 not in text
    assert "…" in text


# ---------------------------------------------------------------------------
# Integração com render_markdown/generate_rdo
# ---------------------------------------------------------------------------


def test_rdo_with_financial_records_includes_section(db, tmp_path):
    """RDO de data com PIX inclui secao visivel."""
    _seed_minimal_classification(db, "2026-04-06")
    _seed_image_and_financial_record(
        db, file_id="f_pix_a", data_transacao="2026-04-06",
        descricao="50% de sinal do serviço de serralheria",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="OBRA_FIN", date="2026-04-06",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    assert "## 💰 Pagamentos registrados (comprovantes)" in md
    assert "R$ 3.500,00" in md
    assert "serralheria" in md


def test_rdo_without_financial_records_omits_section(db, tmp_path):
    """Data sem comprovantes: secao nao aparece."""
    _seed_minimal_classification(db, "2026-04-06")
    # sem financial_records
    result = rdo_piloto.generate_rdo(
        db, obra="OBRA_FIN", date="2026-04-06",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    assert "💰 Pagamentos registrados" not in md
    assert "Pagamentos registrados (comprovantes)" not in md


def test_rdo_multi_pix_lists_all_in_chronological_order(db, tmp_path):
    _seed_minimal_classification(db, "2026-04-06")
    _seed_image_and_financial_record(
        db, file_id="f_pix_manha", data_transacao="2026-04-06",
        hora_transacao="09:15:00", descricao="primeiro",
    )
    _seed_image_and_financial_record(
        db, file_id="f_pix_tarde", data_transacao="2026-04-06",
        hora_transacao="16:30:00", descricao="terceiro",
    )
    _seed_image_and_financial_record(
        db, file_id="f_pix_meio", data_transacao="2026-04-06",
        hora_transacao="12:45:00", descricao="segundo",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="OBRA_FIN", date="2026-04-06",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    p1 = md.index("primeiro")
    p2 = md.index("segundo")
    p3 = md.index("terceiro")
    assert p1 < p2 < p3


def test_rdo_financial_section_appears_after_resumo_before_categorias(
    db, tmp_path,
):
    """Posicionamento: apos 'Resumo do dia' e antes de 'Negociações comerciais'."""
    _seed_minimal_classification(db, "2026-04-06")
    _seed_image_and_financial_record(
        db, file_id="f_pix_p", data_transacao="2026-04-06",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="OBRA_FIN", date="2026-04-06",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    pos_resumo = md.index("## Resumo do dia")
    pos_financial = md.index("## 💰 Pagamentos registrados")
    pos_negociacoes = md.index("## Negociações comerciais")
    assert pos_resumo < pos_financial < pos_negociacoes
