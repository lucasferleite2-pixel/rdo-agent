"""Testes do gerador de RDO piloto — Sprint 3 Fase 4 (Camada 4).

Usa DB sintetico com dados fabricados (sem chamada API, sem PDF real).
Valida estrutura do markdown produzido por `generate_rdo()`.
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


def _insert_full_record(
    conn: sqlite3.Connection,
    *,
    idx: int,
    categories: list[str],
    ts_audio: str,
    human_reviewed: int = 0,
    human_corrected: str | None = None,
    transcription_text: str = None,
    semantic_status: str = "classified",
) -> int:
    """Cria audio+transcription+classification com timestamp controlado.
    Retorna classification_id."""
    now = "2026-04-20T00:00:00Z"
    audio_fid = f"file_audio_{idx:02d}"
    trans_fid = f"file_trans_{idx:02d}"
    msg_id = f"msg_{idx:02d}"

    conn.execute(
        """INSERT INTO messages (
            message_id, obra, timestamp_whatsapp, sender, content,
            media_ref, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (msg_id, "EVERALDO", ts_audio, "Everaldo", "audio anexo",
         f"AUDIO-{idx:02d}.opus", now),
    )
    conn.execute(
        """INSERT INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            referenced_by_message, timestamp_resolved, timestamp_source,
            semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            audio_fid, "EVERALDO", f"10_media/audio{idx:02d}.opus", "audio",
            ("a" + str(idx)) * 32, 1000,
            msg_id, ts_audio, "whatsapp_txt",
            "done", now,
        ),
    )
    conn.execute(
        """INSERT INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            derived_from, derivation_method, referenced_by_message,
            timestamp_resolved, timestamp_source,
            semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            trans_fid, "EVERALDO", f"20_transcriptions/audio{idx:02d}.txt",
            "text", ("b" + str(idx)) * 32, 500, audio_fid,
            "whisper-1", msg_id,
            ts_audio, "whatsapp_txt",
            "awaiting_classification", now,
        ),
    )
    conn.execute(
        """INSERT INTO transcriptions (
            obra, file_id, text, language, confidence, low_confidence,
            api_call_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "EVERALDO", trans_fid,
            transcription_text or f"texto original do audio {idx}",
            "portuguese", 0.6, 0, None, now,
        ),
    )
    cur = conn.execute(
        """INSERT INTO classifications (
            obra, source_file_id, source_type,
            quality_flag, quality_reasoning, human_review_needed,
            human_reviewed, human_corrected_text,
            categories, confidence_model, reasoning,
            classifier_api_call_id, classifier_model,
            quality_api_call_id, quality_model,
            source_sha256, semantic_status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "EVERALDO", trans_fid, "transcription",
            "coerente", "ok", 0,
            human_reviewed, human_corrected,
            json.dumps(categories), 0.8, "classif reasoning",
            None, "gpt-4o-mini-2024-07-18",
            None, "gpt-4o-mini-2024-07-18",
            "c" * 64, semantic_status, now, now,
        ),
    )
    conn.commit()
    return cur.lastrowid


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    conn = init_db(tmp_path)
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_zero_classifications_on_date_raises(db, tmp_path):
    """Dia sem classifications -> RuntimeError (CLI converte em exit 1)."""
    _insert_full_record(
        db, idx=1, categories=["cronograma"],
        ts_audio="2026-04-08T09:00:00Z",
    )
    # pergunta por um dia diferente
    with pytest.raises(RuntimeError, match="Nenhuma classification"):
        rdo_piloto.generate_rdo(
            db, obra="EVERALDO", date="2026-04-09",
            output_dir=tmp_path / "reports",
        )


def test_one_classification_produces_category_section(db, tmp_path):
    """Uma classification cronograma -> aparece na secao de cronograma."""
    _insert_full_record(
        db, idx=1, categories=["cronograma"],
        ts_audio="2026-04-08T09:00:00Z",
        transcription_text="daqui a cinco horas eu libero",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports",
    )
    assert result["total"] == 1
    md = result["markdown_path"].read_text(encoding="utf-8")
    assert "## Cronograma e prazos" in md
    assert "daqui a cinco horas" in md
    assert "file_trans_01" in md


def test_human_reviewed_tag_appears(db, tmp_path):
    """human_reviewed=1 -> tag [REVISADO] na linha do evento."""
    _insert_full_record(
        db, idx=1, categories=["pagamento"],
        ts_audio="2026-04-08T11:00:00Z",
        human_reviewed=1,
        human_corrected="texto corrigido manualmente",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    assert "[REVISADO]" in md
    assert "texto corrigido manualmente" in md


def test_not_reviewed_tag_appears(db, tmp_path):
    """human_reviewed=0 -> tag [NÃO REVISADO]."""
    _insert_full_record(
        db, idx=1, categories=["material"],
        ts_audio="2026-04-08T14:00:00Z",
        human_reviewed=0,
    )
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    assert "[NÃO REVISADO]" in md


def test_rejected_classifications_ignored(db, tmp_path):
    """Linha com semantic_status='rejected' nao entra no RDO."""
    _insert_full_record(
        db, idx=1, categories=["ilegivel"],
        ts_audio="2026-04-08T09:00:00Z",
        semantic_status="rejected",
    )
    _insert_full_record(
        db, idx=2, categories=["cronograma"],
        ts_audio="2026-04-08T10:00:00Z",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports",
    )
    assert result["total"] == 1  # so a cronograma entrou
    md = result["markdown_path"].read_text(encoding="utf-8")
    assert "file_trans_01" not in md  # rejected nao aparece
    assert "file_trans_02" in md


def test_mixed_reviewed_unreviewed_both_tags_present(db, tmp_path):
    _insert_full_record(
        db, idx=1, categories=["pagamento"],
        ts_audio="2026-04-08T09:00:00Z",
        human_reviewed=1, human_corrected="corrigido",
    )
    _insert_full_record(
        db, idx=2, categories=["cronograma"],
        ts_audio="2026-04-08T10:00:00Z",
        human_reviewed=0,
    )
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    assert "[REVISADO]" in md
    assert "[NÃO REVISADO]" in md
    assert result["reviewed"] == 1


def test_multi_category_appears_in_both_sections(db, tmp_path):
    """
    Sprint 4 Op5: multi-label classification aparece em AMBAS as secoes
    das suas categorias, com anotacao "(tambem em X)" na secundaria
    (ou "primary em X" na primary quando vista da secundaria).
    """
    _insert_full_record(
        db, idx=1, categories=["pagamento", "negociacao_comercial"],
        ts_audio="2026-04-08T09:00:00Z",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    # Aparece em AMBAS secoes
    pag_idx = md.index("## Discussões financeiras")
    neg_idx = md.index("## Negociações comerciais")
    # Contagem de file_trans_01 no markdown: 2 linhas (uma por secao)
    assert md.count("file_trans_01") == 2
    # Na secao Discussões financeiras eh primary -> mostra "(tambem em negociacao_comercial)"
    # Encontra a proxima secao apos Discussões financeiras pra limitar a sub-string
    pag_end = md.index("\n## ", pag_idx + 1)
    pag_section = md[pag_idx:pag_end]
    assert "file_trans_01" in pag_section
    assert "tambem em negociacao_comercial" in pag_section
    # Na secao Negociacoes comerciais eh secundaria -> mostra "primary em ..."
    neg_end = md.index("\n## ", neg_idx + 1)
    neg_section = md[neg_idx:neg_end]
    assert "file_trans_01" in neg_section
    assert "primary em" in neg_section


def test_ilegivel_goes_to_notas_forenses(db, tmp_path):
    _insert_full_record(
        db, idx=1, categories=["ilegivel"],
        ts_audio="2026-04-08T09:00:00Z",
    )
    _insert_full_record(
        db, idx=2, categories=["cronograma"],
        ts_audio="2026-04-08T10:00:00Z",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    notas_idx = md.index("## Notas forenses")
    ilegivel_line = md[notas_idx:].find("file_trans_01")
    assert ilegivel_line > 0
    assert "Eventos marcados como ilegíveis: **1**" in md


def test_header_contains_obra_and_date(db, tmp_path):
    _insert_full_record(
        db, idx=1, categories=["off_topic"],
        ts_audio="2026-04-15T14:30:00Z",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-15",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    assert "# RDO — EE Santa Quitéria — 2026-04-15" in md
    assert "**Obra:** EVERALDO" in md


def test_chronological_order_within_category(db, tmp_path):
    """Dentro de uma categoria, eventos ordenados por timestamp."""
    _insert_full_record(
        db, idx=1, categories=["cronograma"],
        ts_audio="2026-04-08T15:00:00Z",
        transcription_text="segundo evento (15h)",
    )
    _insert_full_record(
        db, idx=2, categories=["cronograma"],
        ts_audio="2026-04-08T09:00:00Z",
        transcription_text="primeiro evento (9h)",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    primeiro = md.index("primeiro evento")
    segundo = md.index("segundo evento")
    assert primeiro < segundo


def test_hhmm_extraction_handles_missing_timestamp():
    assert rdo_piloto._extract_hhmm(None) == "--:--"
    assert rdo_piloto._extract_hhmm("") == "--:--"
    assert rdo_piloto._extract_hhmm("2026-04-08T09:15:30Z") == "09:15"
    assert rdo_piloto._extract_hhmm("invalido") == "--:--"


# ---------------------------------------------------------------------------
# Sprint 4 Op5 — extensoes (multi-label, modo-fiscal, resumo, tags)
# ---------------------------------------------------------------------------


def _insert_text_message_classification(
    conn: sqlite3.Connection,
    *,
    idx: int,
    categories: list[str],
    content: str,
    ts: str,
) -> int:
    """Cria message + synthetic files row + classification text_message."""
    now = "2026-04-22T00:00:00Z"
    msg_id = f"msg_text_{idx:02d}"
    file_id = f"m_text{idx:02d}aaaa"
    conn.execute(
        """INSERT INTO messages (
            message_id, obra, timestamp_whatsapp, sender, content, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        (msg_id, "EVERALDO", ts, "Lucas", content, now),
    )
    conn.execute(
        """INSERT INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            referenced_by_message, timestamp_resolved, timestamp_source,
            semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (file_id, "EVERALDO", "", "message",
         ("s" + str(idx)) * 32, len(content.encode("utf-8")),
         msg_id, ts, "whatsapp_txt", "awaiting_classification", now),
    )
    cur = conn.execute(
        """INSERT INTO classifications (
            obra, source_file_id, source_type, source_message_id,
            quality_flag, quality_reasoning, human_review_needed,
            categories, confidence_model, reasoning,
            classifier_api_call_id, classifier_model,
            source_sha256, semantic_status, created_at
        ) VALUES (?, ?, 'text_message', ?, ?, ?, 0,
                  ?, ?, ?, ?, ?, ?, 'classified', ?)""",
        ("EVERALDO", file_id, msg_id,
         "coerente", "texto escrito, sem WER",
         json.dumps(categories), 0.8, "r", None,
         "gpt-4o-mini-2024-07-18", "c" * 64, now),
    )
    conn.commit()
    return cur.lastrowid


def test_source_tag_audio_for_transcription(db, tmp_path):
    _insert_full_record(
        db, idx=1, categories=["cronograma"],
        ts_audio="2026-04-08T09:00:00Z",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    assert "[ÁUDIO]" in md


def test_source_tag_texto_for_text_message(db, tmp_path):
    _insert_text_message_classification(
        db, idx=1, categories=["pagamento"],
        content="Pode mandar pix?",
        ts="2026-04-08T14:00:00Z",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    assert "[TEXTO]" in md
    assert "Pode mandar pix" in md


def test_modo_fiscal_omits_off_topic_section(db, tmp_path):
    _insert_full_record(
        db, idx=1, categories=["cronograma"],
        ts_audio="2026-04-08T09:00:00Z",
    )
    _insert_full_record(
        db, idx=2, categories=["off_topic"],
        ts_audio="2026-04-08T10:00:00Z",
        transcription_text="bom dia, tudo bem?",
    )
    # modo normal: off-topic aparece
    result_default = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports_default",
    )
    md_default = result_default["markdown_path"].read_text(encoding="utf-8")
    assert "Eventos fora de escopo" in md_default
    assert "bom dia" in md_default

    # modo fiscal: off-topic omitido
    result_fiscal = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports_fiscal",
        modo_fiscal=True,
    )
    md_fiscal = result_fiscal["markdown_path"].read_text(encoding="utf-8")
    assert "Eventos fora de escopo" not in md_fiscal
    assert "bom dia" not in md_fiscal
    assert "Modo:** fiscal" in md_fiscal


def test_summary_has_category_counts(db, tmp_path):
    _insert_full_record(
        db, idx=1, categories=["cronograma"], ts_audio="2026-04-08T09:00:00Z",
    )
    _insert_full_record(
        db, idx=2, categories=["cronograma"], ts_audio="2026-04-08T10:00:00Z",
    )
    _insert_full_record(
        db, idx=3, categories=["pagamento"], ts_audio="2026-04-08T11:00:00Z",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    # Resumo numerico
    assert "`cronograma`: **2**" in md
    assert "`pagamento`: **1**" in md
    # Por fonte
    assert "áudios transcritos: **3**" in md


def test_text_message_appears_by_message_timestamp(db, tmp_path):
    """text_message usa messages.timestamp_whatsapp para filtro de data."""
    _insert_text_message_classification(
        db, idx=1, categories=["cronograma"],
        content="amanha o Everaldo passa",
        ts="2026-04-08T14:30:00Z",
    )
    _insert_text_message_classification(
        db, idx=2, categories=["pagamento"],
        content="recebido",
        ts="2026-04-09T10:00:00Z",
    )
    # Dia 08 deve ter somente idx=1
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports",
    )
    assert result["total"] == 1
    md = result["markdown_path"].read_text(encoding="utf-8")
    assert "amanha o Everaldo passa" in md
    assert "recebido" not in md


def test_resolve_display_fields_for_text_message():
    """Teste direto do helper para text_message."""
    # Simular um row com source_type='text_message'
    # sqlite3.Row nao eh trivial simular, usar dict via Row-like via __getitem__
    class FakeRow:
        def __init__(self, data):
            self._d = data
        def __getitem__(self, key):
            return self._d.get(key)
    row = FakeRow({
        "source_type": "text_message",
        "human_corrected_text": None,
        "text_message_content": "manda a chave pix",
        "ts_text_direct": "2026-04-08T14:00:00Z",
        "transcription_text": None,
        "visual_analysis_json": None,
        "visual_source_parent": None,
        "ts_visual": None,
        "document_text": None,
        "document_pages": None,
        "ts_document": None,
        "ts_audio": None,
        "ts_trans": None,
        "ts_msg": None,
    })
    d = rdo_piloto._resolve_display_fields(row)
    assert d["text"] == "manda a chave pix"
    assert d["date"] == "2026-04-08"
    assert d["source_kind"] == "texto"


def test_summary_shows_total_counts_including_secondary(db, tmp_path):
    """
    Sprint 4 Op7: resumo inclui bloco 'Por categoria (total)' que conta
    categoria secundária também. Valida que multi-label é refletido
    numericamente no topo do RDO.
    """
    # 1 evento so negociacao
    _insert_full_record(
        db, idx=1, categories=["negociacao_comercial"],
        ts_audio="2026-04-08T09:00:00Z",
    )
    # 2 eventos negociacao + pagamento (secundária)
    _insert_full_record(
        db, idx=2, categories=["negociacao_comercial", "pagamento"],
        ts_audio="2026-04-08T10:00:00Z",
    )
    _insert_full_record(
        db, idx=3, categories=["negociacao_comercial", "pagamento"],
        ts_audio="2026-04-08T11:00:00Z",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")

    # Primary: negociacao=3, pagamento=0
    assert "`negociacao_comercial`: **3**" in md
    # Total: negociacao=3, pagamento=2 (as 2 secundárias)
    assert "**Por categoria (total" in md
    assert "`pagamento`: **2** (+2 secundária)" in md
