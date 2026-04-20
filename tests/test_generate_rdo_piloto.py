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


def test_multi_category_grouped_by_primary(db, tmp_path):
    """Classification com 2 categorias entra na primary (primeira)."""
    _insert_full_record(
        db, idx=1, categories=["pagamento", "negociacao_comercial"],
        ts_audio="2026-04-08T09:00:00Z",
    )
    result = rdo_piloto.generate_rdo(
        db, obra="EVERALDO", date="2026-04-08",
        output_dir=tmp_path / "reports",
    )
    md = result["markdown_path"].read_text(encoding="utf-8")
    # Aparece em Pagamentos mas nao em Negociacoes comerciais
    # (como primary eh pagamento, secondary nao duplica)
    pag_idx = md.index("## Pagamentos")
    neg_idx = md.index("## Negociações comerciais")
    # file_trans_01 aparece apos Pagamentos e antes de Cronograma
    fid_idx = md.index("file_trans_01")
    # fid esta dentro da secao Pagamentos (entre pag_idx e proxima ##)
    assert pag_idx < fid_idx
    # nao aparece na secao Negociacoes comerciais
    neg_section = md[neg_idx:md.index("## Pagamentos", neg_idx + 1)] if md.count("## Pagamentos") > 1 else md[neg_idx:pag_idx]
    assert "file_trans_01" not in neg_section


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
