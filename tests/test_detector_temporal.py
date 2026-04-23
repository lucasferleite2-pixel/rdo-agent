"""Testes detector TEMPORAL — Sprint 5 Fase B."""

from __future__ import annotations

import json
import sqlite3

import pytest

from rdo_agent.forensic_agent.detectors.temporal import (
    PAYMENT_KEYWORDS,
    WINDOW,
    _count_unique_matches,
    detect_temporal_payment_context,
)
from rdo_agent.forensic_agent.types import CorrelationType
from rdo_agent.orchestrator import init_db


# ---------------------------------------------------------------------------
# Helpers fixture (espelho simplificado de test_dossier_builder.py)
# ---------------------------------------------------------------------------


def _seed_transcription_cls(
    conn: sqlite3.Connection, *,
    obra: str, idx: int, ts_iso: str, text: str,
    category: str = "pagamento",
) -> int:
    """Insere audio + transcription + classification classified. Retorna c.id."""
    now = "2026-04-22T00:00:00Z"
    audio = f"f_aud_{obra}_{idx:03d}"
    trans = f"f_trn_{obra}_{idx:03d}"
    msg = f"msg_{obra}_{idx:03d}"
    conn.execute(
        """INSERT INTO messages (message_id, obra, timestamp_whatsapp,
        sender, content, media_ref, created_at)
        VALUES (?, ?, ?, 'Lucas', 'audio', ?, ?)""",
        (msg, obra, ts_iso, f"A-{idx}.opus", now),
    )
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, referenced_by_message, timestamp_resolved,
        timestamp_source, semantic_status, created_at)
        VALUES (?, ?, ?, 'audio', ?, 100, ?, ?, 'whatsapp_txt', 'done', ?)""",
        (audio, obra, f"p/a{idx}.opus",
         f"a{idx:06d}".ljust(64, "0"), msg, ts_iso, now),
    )
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, derived_from, derivation_method, referenced_by_message,
        timestamp_resolved, timestamp_source, semantic_status, created_at)
        VALUES (?, ?, ?, 'text', ?, 50, ?, 'whisper-1', ?, ?, 'whatsapp_txt',
                'awaiting_classification', ?)""",
        (trans, obra, f"p/t{idx}.txt",
         f"t{idx:06d}".ljust(64, "0"), audio, msg, ts_iso, now),
    )
    conn.execute(
        """INSERT INTO transcriptions (obra, file_id, text, language,
        confidence, low_confidence, api_call_id, created_at)
        VALUES (?, ?, ?, 'portuguese', 0.8, 0, NULL, ?)""",
        (obra, trans, text, now),
    )
    cur = conn.execute(
        """INSERT INTO classifications (
            obra, source_file_id, source_type,
            quality_flag, quality_reasoning, human_review_needed,
            human_reviewed, categories, confidence_model, reasoning,
            classifier_api_call_id, classifier_model,
            quality_api_call_id, quality_model,
            source_sha256, semantic_status, created_at
        ) VALUES (?, ?, 'transcription',
                  'coerente', 'ok', 0, 0, ?, 0.9, 'pedido pagto',
                  NULL, 'gpt-4o-mini', NULL, 'gpt-4o-mini', ?,
                  'classified', ?)""",
        (obra, trans, json.dumps([category]),
         f"c{idx:06d}".ljust(64, "0"), now),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _seed_fr(
    conn: sqlite3.Connection, *,
    obra: str, idx: int, data: str, hora: str,
    valor_centavos: int = 350000, descricao: str = "sinal",
) -> int:
    now = "2026-04-22T00:00:00Z"
    fid = f"f_img_{obra}_{idx:03d}"
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, 'image', ?, 1000, 'ocr_extracted', ?)""",
        (fid, obra, f"p/p{idx}.jpg",
         f"p{idx:06d}".ljust(64, "0"), now),
    )
    cur = conn.execute(
        """INSERT INTO financial_records (obra, source_file_id, doc_type,
        valor_centavos, moeda, data_transacao, hora_transacao,
        pagador_nome, recebedor_nome, descricao, confidence,
        api_call_id, created_at)
        VALUES (?, ?, 'pix', ?, 'BRL', ?, ?, 'Vale Nobre', 'Everaldo',
                ?, 0.95, NULL, ?)""",
        (obra, fid, valor_centavos, data, hora, descricao, now),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path)


# ---------------------------------------------------------------------------
# _count_unique_matches (puro)
# ---------------------------------------------------------------------------


def test_count_unique_matches_empty():
    assert _count_unique_matches("") == 0
    assert _count_unique_matches("nada aqui") == 0


def test_count_unique_matches_case_insensitive():
    assert _count_unique_matches("PIX URGENTE") == 1
    assert _count_unique_matches("Pix e Transferencia") == 2


def test_count_unique_matches_no_double_count():
    # "pix" aparece 3 vezes — conta como 1 unique match
    assert _count_unique_matches("pix pix pix") == 1


def test_payment_keywords_contains_expected():
    for kw in ("pix", "transferencia", "chave", "valor", "sinal"):
        assert kw in PAYMENT_KEYWORDS


def test_window_is_30_minutes():
    assert WINDOW.total_seconds() == 30 * 60


# ---------------------------------------------------------------------------
# detect_temporal_payment_context — cenarios
# ---------------------------------------------------------------------------


def test_detect_returns_empty_when_no_financial_records(db):
    _seed_transcription_cls(
        db, obra="OBRA_T", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="manda o pix por favor",
    )
    assert detect_temporal_payment_context(db, "OBRA_T") == []


def test_detect_returns_empty_when_no_classifications(db):
    _seed_fr(db, obra="OBRA_T", idx=1, data="2026-04-06", hora="11:00:00")
    assert detect_temporal_payment_context(db, "OBRA_T") == []


def test_detect_single_match_within_window_before_payment(db):
    """Classification 20min antes do pagamento com 2 keywords."""
    _seed_transcription_cls(
        db, obra="OBRA_T", idx=1,
        ts_iso="2026-04-06T10:40:00-03:00",
        text="me manda a chave do pix com o valor combinado",
    )
    fr_id = _seed_fr(
        db, obra="OBRA_T", idx=1, data="2026-04-06", hora="11:00:00",
    )

    correlations = detect_temporal_payment_context(db, "OBRA_T")
    assert len(correlations) == 1
    c = correlations[0]
    assert c.correlation_type == CorrelationType.TEMPORAL_PAYMENT_CONTEXT.value
    assert c.primary_event_ref == f"fr_{fr_id}"
    assert c.primary_event_source == "financial_record"
    assert c.related_event_source == "classification"
    assert c.time_gap_seconds == -20 * 60  # cls antes do fr => negativo
    # 4 keywords unicas (manda, chave, pix, valor) => saturated >= 1.0
    assert c.confidence == 1.0
    assert c.detected_by == "temporal_v1"


def test_detect_skips_classification_outside_window(db):
    """Classification 45min antes — fora da janela, nao correlaciona."""
    _seed_transcription_cls(
        db, obra="OBRA_T", idx=1,
        ts_iso="2026-04-06T10:15:00-03:00",
        text="pix chave valor",
    )
    _seed_fr(
        db, obra="OBRA_T", idx=1, data="2026-04-06", hora="11:00:00",
    )
    assert detect_temporal_payment_context(db, "OBRA_T") == []


def test_detect_skips_classification_without_keywords(db):
    """Classification dentro da janela mas sem keywords."""
    _seed_transcription_cls(
        db, obra="OBRA_T", idx=1,
        ts_iso="2026-04-06T10:45:00-03:00",
        text="bom dia, tudo certo?",
    )
    _seed_fr(
        db, obra="OBRA_T", idx=1, data="2026-04-06", hora="11:00:00",
    )
    assert detect_temporal_payment_context(db, "OBRA_T") == []


def test_detect_multiple_classifications_in_window(db):
    """3 classifications na janela, 2 com keywords — emite 2 correlations."""
    _seed_transcription_cls(
        db, obra="OBRA_T", idx=1,
        ts_iso="2026-04-06T10:50:00-03:00",
        text="pix recebido",  # 1 kw
    )
    _seed_transcription_cls(
        db, obra="OBRA_T", idx=2,
        ts_iso="2026-04-06T11:05:00-03:00",
        text="comprovante valor certo",  # 2 kw
    )
    _seed_transcription_cls(
        db, obra="OBRA_T", idx=3,
        ts_iso="2026-04-06T11:10:00-03:00",
        text="vou comprar tinta mais tarde",  # 0 kw
    )
    _seed_fr(
        db, obra="OBRA_T", idx=1, data="2026-04-06", hora="11:00:00",
    )
    correlations = detect_temporal_payment_context(db, "OBRA_T")
    assert len(correlations) == 2
    # time_gap ordenado nao eh garantido, mas valores devem existir:
    gaps = sorted(c.time_gap_seconds for c in correlations)
    assert gaps == [-10 * 60, 5 * 60]


def test_detect_confidence_scales_with_unique_matches(db):
    """1 kw => 0.333, 2 kw => 0.666, 3+ kw => 1.0"""
    _seed_transcription_cls(
        db, obra="OBRA_T", idx=1,
        ts_iso="2026-04-06T11:10:00-03:00",
        text="pix",  # 1 kw
    )
    _seed_fr(db, obra="OBRA_T", idx=1, data="2026-04-06", hora="11:00:00")
    cs = detect_temporal_payment_context(db, "OBRA_T")
    assert len(cs) == 1
    assert cs[0].confidence == pytest.approx(1 / 3, rel=1e-3)


def test_detect_ignores_fr_without_timestamp(db):
    _seed_transcription_cls(
        db, obra="OBRA_T", idx=1,
        ts_iso="2026-04-06T11:10:00-03:00",
        text="pix chave",
    )
    # fr sem hora_transacao
    now = "2026-04-22T00:00:00Z"
    db.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, semantic_status, created_at)
        VALUES ('f_no_ts', 'OBRA_T', 'p/q.jpg', 'image', ?,
                1000, 'ocr_extracted', ?)""",
        ("n".ljust(64, "0"), now),
    )
    db.execute(
        """INSERT INTO financial_records (obra, source_file_id, doc_type,
        valor_centavos, moeda, data_transacao, hora_transacao,
        descricao, confidence, created_at)
        VALUES ('OBRA_T', 'f_no_ts', 'pix', 350000, 'BRL',
                '2026-04-06', NULL, 'sinal', 0.9, ?)""",
        (now,),
    )
    db.commit()
    assert detect_temporal_payment_context(db, "OBRA_T") == []


def test_detect_cross_obra_isolation(db):
    """FR e cls de obras diferentes nao correlacionam."""
    _seed_transcription_cls(
        db, obra="OBRA_A", idx=1,
        ts_iso="2026-04-06T11:10:00-03:00",
        text="pix chave valor",
    )
    _seed_fr(db, obra="OBRA_B", idx=1, data="2026-04-06", hora="11:00:00")
    assert detect_temporal_payment_context(db, "OBRA_A") == []
    assert detect_temporal_payment_context(db, "OBRA_B") == []
