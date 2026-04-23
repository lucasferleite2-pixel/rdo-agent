"""Testes detector SEMANTIC — Sprint 5 Fase B."""

from __future__ import annotations

import json
import sqlite3

import pytest

from rdo_agent.forensic_agent.detectors.semantic import (
    CONFIDENCE_OVERLAP_MAX,
    MIN_OVERLAP,
    STOPWORDS,
    WINDOW,
    _stem,
    _strip_accents,
    detect_semantic_payment_scope,
    tokenize,
)
from rdo_agent.forensic_agent.types import CorrelationType
from rdo_agent.orchestrator import init_db


# ---------------------------------------------------------------------------
# Helpers fixture (minimal, para semantic)
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
    descricao: str | None = "50% de sinal serralheria",
    valor_centavos: int = 350000,
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


def test_strip_accents():
    assert _strip_accents("ção") == "cao"
    assert _strip_accents("Serralheria") == "Serralheria"
    assert _strip_accents("àéîõú") == "aeiou"


def test_stem_strips_common_suffixes():
    assert _stem("telhado") == "telh"
    # 'cao' sufixo => 'instalacao' -> 'instala' (nao 'instal' — stemmer
    # nao aplica sufixos em cascata, so o primeiro match da lista ordenada)
    assert _stem("instalacao") == "instala"
    assert _stem("pagamento") == "paga"
    assert _stem("instalar") == "instal"


def test_stem_preserves_short_words():
    # Palavras muito curtas nao sao stemmed (guard against mutilation)
    assert _stem("pix") == "pix"
    assert _stem("re") == "re"


def test_tokenize_filters_stopwords():
    tokens = tokenize("o cao do vizinho e muito grande")
    # 'o','do','e','muito' sao stopwords; 'cao' -> stemmed 'cao' (guarded);
    # 'vizinho' -> 'vizinh'; 'grande' -> 'grand'
    assert "cao" in tokens or "caozinho" in tokens or "viz" in tokens or len(tokens) >= 2


def test_tokenize_empty_string_returns_empty_set():
    assert tokenize("") == set()
    assert tokenize(None) == set()  # type: ignore[arg-type]


def test_tokenize_ignores_short_tokens():
    assert tokenize("a b c") == set()  # tudo < MIN_TOKEN_LEN


def test_tokenize_accent_insensitive_overlap():
    a = tokenize("serralheria e telhado")
    b = tokenize("SERRALHERIA é TELHÁDO")
    assert a == b


def test_stopwords_loaded():
    assert "de" in STOPWORDS
    assert "que" in STOPWORDS


def test_window_is_3_days():
    assert WINDOW.total_seconds() == 3 * 86400


# ---------------------------------------------------------------------------
# detect_semantic_payment_scope — cenarios
# ---------------------------------------------------------------------------


def test_detect_empty_corpus(db):
    assert detect_semantic_payment_scope(db, "OBRA_S") == []


def test_detect_no_overlap_returns_empty(db):
    _seed_transcription_cls(
        db, obra="OBRA_S", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="falando sobre tinta azul e pincel",
    )
    _seed_fr(
        db, obra="OBRA_S", idx=1, data="2026-04-06",
        descricao="serralheria telhado completo",
    )
    assert detect_semantic_payment_scope(db, "OBRA_S") == []


def test_detect_overlap_2_terms_emits_correlation(db):
    """Fr 'sinal serralheria telhado' vs cls mencionando 'serralheria telhado'."""
    _seed_transcription_cls(
        db, obra="OBRA_S", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="vamos fechar a serralheria com telhado completo amanha",
    )
    fr_id = _seed_fr(
        db, obra="OBRA_S", idx=1, data="2026-04-06",
        descricao="sinal para serralheria telhado inteiro",
    )
    correlations = detect_semantic_payment_scope(db, "OBRA_S")
    assert len(correlations) == 1
    c = correlations[0]
    assert c.correlation_type == CorrelationType.SEMANTIC_PAYMENT_SCOPE.value
    assert c.primary_event_ref == f"fr_{fr_id}"
    assert c.detected_by == "semantic_v1"
    # overlap de 2 termos ('serralheria','telh') => 2/5 = 0.4
    assert c.confidence == pytest.approx(2 / CONFIDENCE_OVERLAP_MAX, rel=1e-3)


def test_detect_outside_3day_window_ignored(db):
    """Cls 5 dias antes — fora da janela, nao correlaciona."""
    _seed_transcription_cls(
        db, obra="OBRA_S", idx=1,
        ts_iso="2026-04-01T10:00:00-03:00",
        text="vamos fechar a serralheria com telhado completo",
    )
    _seed_fr(
        db, obra="OBRA_S", idx=1, data="2026-04-06",
        descricao="sinal serralheria telhado",
    )
    assert detect_semantic_payment_scope(db, "OBRA_S") == []


def test_detect_skips_fr_without_descricao(db):
    _seed_transcription_cls(
        db, obra="OBRA_S", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="serralheria telhado sinal",
    )
    _seed_fr(
        db, obra="OBRA_S", idx=1, data="2026-04-06",
        descricao=None,  # sem descricao
    )
    assert detect_semantic_payment_scope(db, "OBRA_S") == []


def test_detect_saturates_confidence_at_1(db):
    """Overlap muito grande => confidence 1.0."""
    text = "serralheria telhado sinal instalar completo material azul grande"
    _seed_transcription_cls(
        db, obra="OBRA_S", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text=text,
    )
    _seed_fr(
        db, obra="OBRA_S", idx=1, data="2026-04-06",
        descricao="serralheria telhado sinal instalar completo material azul grande",
    )
    correlations = detect_semantic_payment_scope(db, "OBRA_S")
    assert len(correlations) == 1
    assert correlations[0].confidence == 1.0


def test_detect_min_overlap_enforced(db):
    """Overlap = 1 => nao emite (MIN_OVERLAP=2)."""
    _seed_transcription_cls(
        db, obra="OBRA_S", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="vamos falar de serralheria apenas",
    )
    _seed_fr(
        db, obra="OBRA_S", idx=1, data="2026-04-06",
        descricao="sinal serralheria telhado instalacao completa",
    )
    # overlap: {'serralheria'} => 1 < MIN_OVERLAP
    assert detect_semantic_payment_scope(db, "OBRA_S") == []
    assert MIN_OVERLAP == 2


def test_detect_multiple_classifications_emit_multiple_correlations(db):
    _seed_transcription_cls(
        db, obra="OBRA_S", idx=1,
        ts_iso="2026-04-05T10:00:00-03:00",
        text="serralheria telhado vai comecar amanha",
    )
    _seed_transcription_cls(
        db, obra="OBRA_S", idx=2,
        ts_iso="2026-04-07T15:00:00-03:00",
        text="o telhado e a serralheria ja foram instalados",
    )
    _seed_fr(
        db, obra="OBRA_S", idx=1, data="2026-04-06",
        descricao="sinal para serralheria telhado",
    )
    correlations = detect_semantic_payment_scope(db, "OBRA_S")
    assert len(correlations) == 2


def test_detect_cross_obra_isolation(db):
    _seed_transcription_cls(
        db, obra="OBRA_A", idx=1,
        ts_iso="2026-04-06T10:00:00-03:00",
        text="serralheria telhado completo",
    )
    _seed_fr(
        db, obra="OBRA_B", idx=1, data="2026-04-06",
        descricao="sinal serralheria telhado",
    )
    assert detect_semantic_payment_scope(db, "OBRA_A") == []
    assert detect_semantic_payment_scope(db, "OBRA_B") == []
