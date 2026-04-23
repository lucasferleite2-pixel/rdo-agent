"""Testes do #26 — classificacao UNITARY/AGGREGATE/AMBIGUOUS no MATH detector."""

from __future__ import annotations

import json
import sqlite3

import pytest

from rdo_agent.forensic_agent.detectors.math import (
    AMBIGUOUS_PENALTY,
    VALUE_KIND_AGGREGATE,
    VALUE_KIND_AMBIGUOUS,
    VALUE_KIND_UNITARY,
    classify_value_mention,
    detect_math_relations,
    extract_value_mentions,
)
from rdo_agent.forensic_agent.types import CorrelationType
from rdo_agent.orchestrator import init_db


# ---------------------------------------------------------------------------
# classify_value_mention — unitario
# ---------------------------------------------------------------------------


def _span_of(text: str, substr: str) -> tuple[int, int]:
    i = text.find(substr)
    assert i >= 0, f"{substr!r} not in {text!r}"
    return (i, i + len(substr))


def test_classify_unitary_por_metro():
    text = "fica R$50,00 por metro no alambrado"
    s, e = _span_of(text, "R$50,00")
    assert classify_value_mention(text, s, e) == VALUE_KIND_UNITARY


def test_classify_unitary_slash_metro():
    text = "R$ 80/m pra instalar"
    s, e = _span_of(text, "R$ 80")
    assert classify_value_mention(text, s, e) == VALUE_KIND_UNITARY


def test_classify_unitary_cada():
    text = "vai ficar R$500,00 cada uma"
    s, e = _span_of(text, "R$500,00")
    assert classify_value_mention(text, s, e) == VALUE_KIND_UNITARY


# ---------------------------------------------------------------------------
# classify_value_mention — agregado
# ---------------------------------------------------------------------------


def test_classify_aggregate_sem_qualificador():
    text = "o pix foi de R$3.500,00 confirmado"
    s, e = _span_of(text, "R$3.500,00")
    assert classify_value_mention(text, s, e) == VALUE_KIND_AGGREGATE


def test_classify_aggregate_total_marker():
    text = "total R$11.000,00 pelo pacote"
    s, e = _span_of(text, "R$11.000,00")
    assert classify_value_mention(text, s, e) == VALUE_KIND_AGGREGATE


def test_classify_aggregate_fechou_em():
    text = "fechamos em R$7.000,00 pela estrutura"
    s, e = _span_of(text, "R$7.000,00")
    assert classify_value_mention(text, s, e) == VALUE_KIND_AGGREGATE


# ---------------------------------------------------------------------------
# classify_value_mention — ambiguous
# ---------------------------------------------------------------------------


def test_classify_ambiguous_mixed_signals():
    # Marker unitario 'cada' E agregado 'total' no mesmo contexto =>
    # ambiguous
    text = "total cada R$100,00 fica dificil"
    s, e = _span_of(text, "R$100,00")
    assert classify_value_mention(text, s, e) == VALUE_KIND_AMBIGUOUS


# ---------------------------------------------------------------------------
# extract_value_mentions — pipeline completo
# ---------------------------------------------------------------------------


def test_extract_mentions_mixed_kinds():
    text = (
        "R$50,00 por metro de alambrado e total R$11.000,00 pelo pacote"
    )
    mentions = extract_value_mentions(text)
    # 5000 centavos (unit) + 1100000 centavos (agg)
    assert (5000, VALUE_KIND_UNITARY) in mentions
    assert (1100000, VALUE_KIND_AGGREGATE) in mentions


def test_extract_mentions_dedup_same_cents_same_kind():
    # mesmo valor repetido com mesmo kind: apenas uma entrada
    text = "total R$3.500,00 e depois total R$3.500,00 de novo"
    mentions = extract_value_mentions(text)
    assert mentions.count((350000, VALUE_KIND_AGGREGATE)) == 1


# ---------------------------------------------------------------------------
# detect_math_relations — integracao
# ---------------------------------------------------------------------------


def _seed_cls(
    conn: sqlite3.Connection, *,
    obra: str, idx: int, ts_iso: str, text: str,
) -> int:
    now = "2026-04-22T00:00:00Z"
    audio = f"f_a_{obra}_{idx:03d}"
    trans = f"f_t_{obra}_{idx:03d}"
    msg = f"m_{obra}_{idx:03d}"
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
        (audio, obra, f"p/a{idx}.opus", f"a{idx:06d}".ljust(64, "0"),
         msg, ts_iso, now),
    )
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, derived_from, derivation_method, referenced_by_message,
        timestamp_resolved, timestamp_source, semantic_status, created_at)
        VALUES (?, ?, ?, 'text', ?, 50, ?, 'whisper-1', ?, ?, 'whatsapp_txt',
                'awaiting_classification', ?)""",
        (trans, obra, f"p/t{idx}.txt", f"t{idx:06d}".ljust(64, "0"),
         audio, msg, ts_iso, now),
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
        ) VALUES (?, ?, 'transcription', 'coerente', 'ok', 0, 0, ?,
                  0.9, '', NULL, 'gpt-4o-mini', NULL, 'gpt-4o-mini',
                  ?, 'classified', ?)""",
        (obra, trans, json.dumps(["pagamento"]),
         f"c{idx:06d}".ljust(64, "0"), now),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _seed_fr(
    conn: sqlite3.Connection, *,
    obra: str, idx: int, data: str, hora: str,
    valor_centavos: int, descricao: str,
) -> int:
    now = "2026-04-22T00:00:00Z"
    fid = f"f_img_{obra}_{idx:03d}"
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, 'image', ?, 1000, 'ocr_extracted', ?)""",
        (fid, obra, f"p/p{idx}.jpg", f"p{idx:06d}".ljust(64, "0"), now),
    )
    cur = conn.execute(
        """INSERT INTO financial_records (obra, source_file_id, doc_type,
        valor_centavos, moeda, data_transacao, hora_transacao,
        descricao, confidence, created_at)
        VALUES (?, ?, 'pix', ?, 'BRL', ?, ?, ?, 0.95, ?)""",
        (obra, fid, valor_centavos, data, hora, descricao, now),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path)


def test_detect_unitary_mention_does_not_correlate_with_fr(db):
    """Mencao de 'R$50 por metro' NAO correlaciona com PIX R$50 total."""
    _seed_cls(
        db, obra="OU", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="o alambrado fica R$50,00 por metro",
    )
    _seed_fr(
        db, obra="OU", idx=1, data="2026-04-06", hora="11:00:00",
        valor_centavos=5000, descricao="pix",
    )
    # O valor mencionado eh unitary => nao deve correlacionar
    cs = detect_math_relations(db, "OU")
    assert cs == []


def test_detect_aggregate_mention_correlates(db):
    """'total R$11.000,00' CORRELACIONA com PIX R$11.000."""
    _seed_cls(
        db, obra="OA", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="fechamos em R$11.000,00 pela cobertura completa",
    )
    _seed_fr(
        db, obra="OA", idx=1, data="2026-04-06", hora="11:00:00",
        valor_centavos=1100000, descricao="telhado",
    )
    cs = detect_math_relations(db, "OA")
    assert len(cs) == 1
    assert cs[0].correlation_type == CorrelationType.MATH_VALUE_MATCH.value
    assert cs[0].confidence == 1.0


def test_detect_ambiguous_mention_receives_penalty(db):
    """Mencao AMBIGUOUS reduz confidence em AMBIGUOUS_PENALTY."""
    _seed_cls(
        db, obra="OB", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        # 'total' + 'cada' no contexto = AMBIGUOUS
        text="total cada R$3.500,00 fechamos",
    )
    _seed_fr(
        db, obra="OB", idx=1, data="2026-04-06", hora="11:00:00",
        valor_centavos=350000, descricao="sinal",
    )
    cs = detect_math_relations(db, "OB")
    assert len(cs) == 1
    # MATH_VALUE_MATCH base = 1.0, penalty 0.2 => 0.8
    assert cs[0].confidence == pytest.approx(1.0 - AMBIGUOUS_PENALTY)


def test_detect_mixed_unitary_and_aggregate_in_same_cls(db):
    """Cls menciona valor unitary E valor aggregate diferentes:
    o unitary eh ignorado; o aggregate correlaciona."""
    _seed_cls(
        db, obra="OM", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text=(
            "R$50,00 por metro de alambrado e total "
            "R$11.000,00 pelo pacote completo"
        ),
    )
    _seed_fr(
        db, obra="OM", idx=1, data="2026-04-06", hora="11:00:00",
        valor_centavos=1100000, descricao="telhado",
    )
    cs = detect_math_relations(db, "OM")
    assert len(cs) == 1
    assert cs[0].correlation_type == CorrelationType.MATH_VALUE_MATCH.value


def test_detect_unitary_kind_tag_in_rationale_only_for_nonagg(db):
    """Rationale adiciona [kind=ambiguous] pra auditabilidade."""
    _seed_cls(
        db, obra="OK", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="total cada R$3.500,00 fechamos",
    )
    _seed_fr(
        db, obra="OK", idx=1, data="2026-04-06", hora="11:00:00",
        valor_centavos=350000, descricao="sinal",
    )
    cs = detect_math_relations(db, "OK")
    assert len(cs) == 1
    assert "kind=ambiguous" in cs[0].rationale
