"""
Testes integracao correlator (Sprint 5 Fase B Fase 5).

Exercita detect_correlations + get_correlations + delete_correlations_for_obra
num cenario sintetico que dispara os 3 detectores ao mesmo tempo.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from rdo_agent.forensic_agent.correlator import (
    delete_correlations_for_obra,
    detect_correlations,
    get_correlations,
)
from rdo_agent.forensic_agent.types import CorrelationType
from rdo_agent.orchestrator import init_db


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
        (fid, obra, f"p/p{idx}.jpg",
         f"p{idx:06d}".ljust(64, "0"), now),
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


@pytest.fixture
def seeded_db(db):
    """DB com 1 fr + 3 cls estrategicas disparando os 3 detectores."""
    # FR: R$3500 em 2026-04-06 11:00, desc "sinal serralheria telhado"
    _seed_fr(
        db, obra="OBRA_I", idx=1,
        data="2026-04-06", hora="11:00:00",
        valor_centavos=350000,
        descricao="sinal para serralheria telhado completo",
    )
    # cls A (15min antes): "manda a chave do pix, R$3.500,00 no telhado"
    # -> dispara TEMPORAL + MATH + SEMANTIC
    _seed_cls(
        db, obra="OBRA_I", idx=1,
        ts_iso="2026-04-06T10:45:00-03:00",
        text="me manda a chave do pix, R$3.500,00 pelo telhado serralheria",
    )
    # cls B (2 dias depois): texto discutindo serralheria+telhado sem valor
    # -> dispara SEMANTIC apenas
    _seed_cls(
        db, obra="OBRA_I", idx=2,
        ts_iso="2026-04-08T14:00:00-03:00",
        text="o telhado da serralheria ficou bonito, completo",
    )
    # cls C (muito longe): nada dispara
    _seed_cls(
        db, obra="OBRA_I", idx=3,
        ts_iso="2026-03-01T09:00:00-03:00",
        text="bom dia equipe",
    )
    return db


# ---------------------------------------------------------------------------
# detect_correlations
# ---------------------------------------------------------------------------


def test_detect_correlations_empty_db_returns_empty(db):
    assert detect_correlations(db, "OBRA_I") == []


def test_detect_correlations_disparos_3_detectores(seeded_db):
    """cls A dispara 3 detectores; cls B dispara so SEMANTIC; cls C nada."""
    results = detect_correlations(seeded_db, "OBRA_I", persist=False)
    types = {c.correlation_type for c in results}
    assert CorrelationType.TEMPORAL_PAYMENT_CONTEXT.value in types
    assert CorrelationType.SEMANTIC_PAYMENT_SCOPE.value in types
    assert CorrelationType.MATH_VALUE_MATCH.value in types


def test_detect_correlations_persist_default_true(seeded_db):
    """persist=True (default) grava na tabela."""
    before = seeded_db.execute(
        "SELECT COUNT(*) FROM correlations WHERE obra='OBRA_I'"
    ).fetchone()[0]
    assert before == 0
    detect_correlations(seeded_db, "OBRA_I")  # default persist=True
    after = seeded_db.execute(
        "SELECT COUNT(*) FROM correlations WHERE obra='OBRA_I'"
    ).fetchone()[0]
    assert after > 0


def test_detect_correlations_persist_false_does_not_write(seeded_db):
    detect_correlations(seeded_db, "OBRA_I", persist=False)
    after = seeded_db.execute(
        "SELECT COUNT(*) FROM correlations WHERE obra='OBRA_I'"
    ).fetchone()[0]
    assert after == 0


# ---------------------------------------------------------------------------
# get_correlations
# ---------------------------------------------------------------------------


def test_get_correlations_filter_type(seeded_db):
    detect_correlations(seeded_db, "OBRA_I")
    temporal_only = get_correlations(
        seeded_db, "OBRA_I",
        filter_type=CorrelationType.TEMPORAL_PAYMENT_CONTEXT.value,
    )
    assert len(temporal_only) >= 1
    for c in temporal_only:
        assert c.correlation_type == CorrelationType.TEMPORAL_PAYMENT_CONTEXT.value


def test_get_correlations_min_confidence(seeded_db):
    detect_correlations(seeded_db, "OBRA_I")
    high = get_correlations(seeded_db, "OBRA_I", min_confidence=0.99)
    for c in high:
        assert c.confidence >= 0.99
    # Ha pelo menos 1 exact match (conf=1.0) no cenario
    assert len(high) >= 1


def test_get_correlations_empty_when_not_detected_yet(seeded_db):
    """get_correlations nao roda detectores; se nada foi detectado retorna []."""
    assert get_correlations(seeded_db, "OBRA_I") == []


# ---------------------------------------------------------------------------
# delete_correlations_for_obra
# ---------------------------------------------------------------------------


def test_delete_correlations_for_obra(seeded_db):
    detect_correlations(seeded_db, "OBRA_I")
    before = seeded_db.execute(
        "SELECT COUNT(*) FROM correlations WHERE obra='OBRA_I'"
    ).fetchone()[0]
    assert before > 0

    removed = delete_correlations_for_obra(seeded_db, "OBRA_I")
    assert removed == before

    after = seeded_db.execute(
        "SELECT COUNT(*) FROM correlations WHERE obra='OBRA_I'"
    ).fetchone()[0]
    assert after == 0


def test_delete_correlations_other_obra_not_affected(seeded_db):
    detect_correlations(seeded_db, "OBRA_I")
    delete_correlations_for_obra(seeded_db, "OBRA_OTHER")
    # OBRA_I correlations intactas
    count = seeded_db.execute(
        "SELECT COUNT(*) FROM correlations WHERE obra='OBRA_I'"
    ).fetchone()[0]
    assert count > 0


# ---------------------------------------------------------------------------
# rebuild flow end-to-end
# ---------------------------------------------------------------------------


def test_rebuild_flow_is_idempotent(seeded_db):
    """detect -> delete -> detect produz o mesmo resultado em persistencia."""
    detect_correlations(seeded_db, "OBRA_I")
    first_count = seeded_db.execute(
        "SELECT COUNT(*) FROM correlations WHERE obra='OBRA_I'"
    ).fetchone()[0]

    delete_correlations_for_obra(seeded_db, "OBRA_I")
    detect_correlations(seeded_db, "OBRA_I")
    second_count = seeded_db.execute(
        "SELECT COUNT(*) FROM correlations WHERE obra='OBRA_I'"
    ).fetchone()[0]

    assert first_count == second_count
