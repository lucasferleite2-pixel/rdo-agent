"""Testes detector MATH — Sprint 5 Fase B."""

from __future__ import annotations

import json
import sqlite3

import pytest

from rdo_agent.forensic_agent.detectors.math import (
    EXACT_TOLERANCE_CENTS,
    _classify_match,
    detect_math_relations,
    extract_values_cents,
    parse_brl_to_cents,
)
from rdo_agent.forensic_agent.types import CorrelationType
from rdo_agent.orchestrator import init_db


# ---------------------------------------------------------------------------
# Helpers fixture
# ---------------------------------------------------------------------------


def _seed_transcription_cls(
    conn: sqlite3.Connection, *,
    obra: str, idx: int, ts_iso: str, text: str,
    category: str = "pagamento",
) -> int:
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
                  'coerente', 'ok', 0, 0, ?, 0.9, '',
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
    obra: str, idx: int, data: str, hora: str = "11:00:00",
    valor_centavos: int = 350000,
    descricao: str = "pagamento",
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
        VALUES (?, ?, 'pix', ?, 'BRL', ?, ?, 'Vale', 'Everaldo',
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
# Helpers puros
# ---------------------------------------------------------------------------


def test_parse_brl_basic():
    assert parse_brl_to_cents("3500") == 350000
    assert parse_brl_to_cents("3500,00") == 350000
    assert parse_brl_to_cents("3500,50") == 350050
    assert parse_brl_to_cents("3.500,00") == 350000
    assert parse_brl_to_cents("3.500") == 350000
    assert parse_brl_to_cents("1.000.000,00") == 100000000


def test_parse_brl_invalid():
    assert parse_brl_to_cents("") is None
    assert parse_brl_to_cents(None) is None  # type: ignore[arg-type]
    assert parse_brl_to_cents("abc") is None


def test_extract_values_requires_rs_prefix():
    # Sem "R$", numeros nao sao capturados (evita FP)
    assert extract_values_cents("o telefone e 99999999") == []
    assert extract_values_cents("50 pessoas na obra") == []


def test_extract_values_multiple():
    text = "vou pagar R$3.500,00 agora, depois mais R$1.750,00 na semana"
    assert extract_values_cents(text) == [350000, 175000]


def test_extract_values_formats():
    assert extract_values_cents("R$3500") == [350000]
    assert extract_values_cents("R$ 3.500,00") == [350000]
    assert extract_values_cents("R$3500,50") == [350050]


def test_classify_match_exact():
    # diff < R$1 => MATH_VALUE_MATCH conf 1.0
    result = _classify_match(mentioned_cents=350000, target_cents=350000)
    assert result == (CorrelationType.MATH_VALUE_MATCH.value, 1.0)
    result = _classify_match(mentioned_cents=350050, target_cents=350000)
    # 50c diff < R$1 => match
    assert result == (CorrelationType.MATH_VALUE_MATCH.value, 1.0)


def test_classify_match_installment_half():
    # metade: R$1750 de R$3500
    result = _classify_match(mentioned_cents=175000, target_cents=350000)
    assert result == (CorrelationType.MATH_INSTALLMENT_MATCH.value, 0.8)


def test_classify_match_installment_double():
    # dobro: R$7000 quando o pago foi R$3500 (sinal pago, total virou dobro)
    result = _classify_match(mentioned_cents=700000, target_cents=350000)
    assert result == (CorrelationType.MATH_INSTALLMENT_MATCH.value, 0.8)


def test_classify_match_divergence():
    # 0.5*3500=1750 ate 1.5*3500=5250 — valor 4000 esta na faixa mas nao eh
    # exact nem installment
    result = _classify_match(mentioned_cents=400000, target_cents=350000)
    assert result == (CorrelationType.MATH_VALUE_DIVERGENCE.value, 0.6)


def test_classify_match_out_of_range():
    # 200 de 3500 => fora da faixa 0.5-1.5x => None
    result = _classify_match(mentioned_cents=20000, target_cents=350000)
    assert result is None
    # 10000 de 3500 => >1.5x => None
    result = _classify_match(mentioned_cents=1000000, target_cents=350000)
    assert result is None


def test_exact_tolerance_is_r1():
    assert EXACT_TOLERANCE_CENTS == 100


# ---------------------------------------------------------------------------
# detect_math_relations — cenarios
# ---------------------------------------------------------------------------


def test_detect_empty_corpus(db):
    assert detect_math_relations(db, "OBRA_M") == []


def test_detect_exact_match(db):
    _seed_transcription_cls(
        db, obra="OBRA_M", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="combinado R$3.500,00 pelo telhado",
    )
    fr_id = _seed_fr(
        db, obra="OBRA_M", idx=1, data="2026-04-06",
        valor_centavos=350000,
    )
    cs = detect_math_relations(db, "OBRA_M")
    assert len(cs) == 1
    assert cs[0].correlation_type == CorrelationType.MATH_VALUE_MATCH.value
    assert cs[0].confidence == 1.0
    assert cs[0].primary_event_ref == f"fr_{fr_id}"
    assert cs[0].detected_by == "math_v1"


def test_detect_installment_half(db):
    """Menciona R$1750 (metade) vs pago R$3500 => installment."""
    _seed_transcription_cls(
        db, obra="OBRA_M", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="eu mando R$1.750,00 de sinal agora",
    )
    _seed_fr(
        db, obra="OBRA_M", idx=1, data="2026-04-06",
        valor_centavos=350000,
    )
    cs = detect_math_relations(db, "OBRA_M")
    assert len(cs) == 1
    assert cs[0].correlation_type == CorrelationType.MATH_INSTALLMENT_MATCH.value
    assert cs[0].confidence == 0.8


def test_detect_divergence(db):
    """Menciona R$4000 vs pago R$3500 — faixa suspeita sem match exato."""
    _seed_transcription_cls(
        db, obra="OBRA_M", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="na proposta era R$4.000,00",
    )
    _seed_fr(
        db, obra="OBRA_M", idx=1, data="2026-04-06",
        valor_centavos=350000,
    )
    cs = detect_math_relations(db, "OBRA_M")
    assert len(cs) == 1
    assert cs[0].correlation_type == CorrelationType.MATH_VALUE_DIVERGENCE.value
    assert cs[0].confidence == 0.6


def test_detect_multiple_values_in_same_cls(db):
    """Uma cls menciona R$3500 (match) e R$1750 (installment)."""
    _seed_transcription_cls(
        db, obra="OBRA_M", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="total e R$3.500,00, sinal de R$1.750,00",
    )
    _seed_fr(
        db, obra="OBRA_M", idx=1, data="2026-04-06",
        valor_centavos=350000,
    )
    cs = detect_math_relations(db, "OBRA_M")
    assert len(cs) == 2
    types = sorted(c.correlation_type for c in cs)
    assert types == [
        CorrelationType.MATH_INSTALLMENT_MATCH.value,
        CorrelationType.MATH_VALUE_MATCH.value,
    ]


def test_detect_skips_out_of_range_values(db):
    """Valor mencionado totalmente fora da faixa: ignorado."""
    _seed_transcription_cls(
        db, obra="OBRA_M", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="paguei R$50,00 em tinta",
    )
    _seed_fr(
        db, obra="OBRA_M", idx=1, data="2026-04-06",
        valor_centavos=350000,
    )
    assert detect_math_relations(db, "OBRA_M") == []


def test_detect_skips_cls_outside_7day_window(db):
    """Cls 10 dias antes — fora da janela."""
    _seed_transcription_cls(
        db, obra="OBRA_M", idx=1,
        ts_iso="2026-03-25T10:00:00-03:00",
        text="vamos fechar em R$3.500,00",
    )
    _seed_fr(
        db, obra="OBRA_M", idx=1, data="2026-04-06",
        valor_centavos=350000,
    )
    assert detect_math_relations(db, "OBRA_M") == []


def test_detect_skips_fr_without_valor(db):
    _seed_transcription_cls(
        db, obra="OBRA_M", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="R$3.500,00",
    )
    _seed_fr(
        db, obra="OBRA_M", idx=1, data="2026-04-06",
        valor_centavos=None,  # type: ignore[arg-type]
    )
    # sem valor nao emite
    assert detect_math_relations(db, "OBRA_M") == []


def test_detect_cross_obra_isolation(db):
    _seed_transcription_cls(
        db, obra="OBRA_A", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="R$3.500,00",
    )
    _seed_fr(
        db, obra="OBRA_B", idx=1, data="2026-04-06",
        valor_centavos=350000,
    )
    assert detect_math_relations(db, "OBRA_A") == []
    assert detect_math_relations(db, "OBRA_B") == []
