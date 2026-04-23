"""Testes dossier_builder — Sprint 5 Fase A F2."""

from __future__ import annotations

import json
import sqlite3

import pytest

from rdo_agent.forensic_agent.dossier_builder import (
    OVERVIEW_SAMPLE_FIRST_N,
    OVERVIEW_SAMPLE_LAST_N,
    _parse_categories,
    build_day_dossier,
    build_obra_overview_dossier,
    compute_dossier_hash,
)
from rdo_agent.orchestrator import init_db

# ---------------------------------------------------------------------------
# Helpers fixture
# ---------------------------------------------------------------------------


def _seed_classification_transcription(
    conn: sqlite3.Connection, *,
    obra: str, idx: int, date: str, hhmm: str = "09:00",
    category: str = "cronograma", secondary: list[str] | None = None,
    text: str = "evento de teste",
) -> None:
    """Cria audio + transcription + classification pra data dada."""
    now = "2026-04-22T00:00:00Z"
    ts = f"{date}T{hhmm}:00Z"
    audio = f"f_audio_{obra}_{idx:03d}"
    trans = f"f_trans_{obra}_{idx:03d}"
    msg = f"msg_{obra}_{idx:03d}"

    conn.execute(
        """INSERT INTO messages (message_id, obra, timestamp_whatsapp,
        sender, content, media_ref, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (msg, obra, ts, "Lucas", "audio", f"AUDIO-{idx}.opus", now),
    )
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, referenced_by_message, timestamp_resolved,
        timestamp_source, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (audio, obra, f"10_media/a{idx}.opus", "audio",
         f"a{idx:06d}".ljust(64, "0"), 100, msg, ts, "whatsapp_txt",
         "done", now),
    )
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, derived_from, derivation_method, referenced_by_message,
        timestamp_resolved, timestamp_source, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (trans, obra, f"20_transcriptions/t{idx}.txt", "text",
         f"t{idx:06d}".ljust(64, "0"), 50, audio, "whisper-1",
         msg, ts, "whatsapp_txt", "awaiting_classification", now),
    )
    conn.execute(
        """INSERT INTO transcriptions (obra, file_id, text, language,
        confidence, low_confidence, api_call_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (obra, trans, text, "portuguese", 0.6, 0, None, now),
    )
    cats = [category] + (secondary or [])
    conn.execute(
        """INSERT INTO classifications (
            obra, source_file_id, source_type,
            quality_flag, quality_reasoning, human_review_needed,
            human_reviewed, categories, confidence_model, reasoning,
            classifier_api_call_id, classifier_model,
            quality_api_call_id, quality_model,
            source_sha256, semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (obra, trans, "transcription",
         "coerente", "ok", 0, 0, json.dumps(cats), 0.85, "x",
         None, "gpt-4o-mini", None, "gpt-4o-mini",
         f"c{idx:06d}".ljust(64, "0"), "classified", now),
    )
    conn.commit()


def _seed_financial_record(
    conn: sqlite3.Connection, *,
    obra: str, idx: int, date: str, hora: str = "11:13:24",
    valor_centavos: int = 350000,
    descricao: str = "50% sinal serralheria",
) -> None:
    now = "2026-04-22T00:00:00Z"
    fid = f"f_img_{obra}_{idx:03d}"
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (fid, obra, f"10_media/p{idx}.jpg", "image",
         f"p{idx:06d}".ljust(64, "0"), 1000, "ocr_extracted", now),
    )
    conn.execute(
        """INSERT INTO financial_records (obra, source_file_id, doc_type,
        valor_centavos, moeda, data_transacao, hora_transacao,
        pagador_nome, recebedor_nome, descricao, confidence,
        api_call_id, created_at)
        VALUES (?, ?, 'pix', ?, 'BRL', ?, ?, 'Vale Nobre', 'Everaldo',
                ?, 0.95, NULL, ?)""",
        (obra, fid, valor_centavos, date, hora, descricao, now),
    )
    conn.commit()


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path)


# ---------------------------------------------------------------------------
# _parse_categories (helper puro)
# ---------------------------------------------------------------------------


def test_parse_categories_valid_json():
    assert _parse_categories('["a","b"]') == ["a", "b"]


def test_parse_categories_empty():
    assert _parse_categories(None) == []
    assert _parse_categories("") == []
    assert _parse_categories("invalid") == []


# ---------------------------------------------------------------------------
# build_day_dossier
# ---------------------------------------------------------------------------


def test_build_day_dossier_empty_returns_empty_timeline(db):
    d = build_day_dossier(db, "OBRA_X", "2026-04-06")
    assert d["obra"] == "OBRA_X"
    assert d["scope"] == "day"
    assert d["scope_ref"] == "2026-04-06"
    assert d["events_timeline"] == []
    assert d["statistics"]["events_total"] == 0
    assert d["financial_records"] == []


def test_build_day_dossier_3_events_chronological_order(db):
    """Events inseridos fora de ordem devem ser ordenados por timestamp."""
    _seed_classification_transcription(
        db, obra="OBRA_C", idx=3, date="2026-04-06", hhmm="15:00",
        text="terceiro",
    )
    _seed_classification_transcription(
        db, obra="OBRA_C", idx=1, date="2026-04-06", hhmm="09:00",
        text="primeiro",
    )
    _seed_classification_transcription(
        db, obra="OBRA_C", idx=2, date="2026-04-06", hhmm="12:00",
        text="segundo",
    )
    d = build_day_dossier(db, "OBRA_C", "2026-04-06")
    assert d["statistics"]["events_total"] == 3
    texts = [e["content_full"] for e in d["events_timeline"]]
    assert texts == ["primeiro", "segundo", "terceiro"]
    # horarios
    horas = [e["hora_brasilia"] for e in d["events_timeline"]]
    assert horas == ["09:00", "12:00", "15:00"]


def test_build_day_dossier_filters_other_dates(db):
    _seed_classification_transcription(
        db, obra="OBRA_F", idx=1, date="2026-04-06", text="dia 6",
    )
    _seed_classification_transcription(
        db, obra="OBRA_F", idx=2, date="2026-04-07", text="dia 7",
    )
    d = build_day_dossier(db, "OBRA_F", "2026-04-06")
    assert d["statistics"]["events_total"] == 1
    assert d["events_timeline"][0]["content_full"] == "dia 6"


def test_build_day_dossier_includes_financial_records(db):
    _seed_classification_transcription(
        db, obra="OBRA_FIN", idx=1, date="2026-04-06", text="audio",
    )
    _seed_financial_record(
        db, obra="OBRA_FIN", idx=1, date="2026-04-06",
        valor_centavos=350000, descricao="50% de sinal serralheria",
    )
    d = build_day_dossier(db, "OBRA_FIN", "2026-04-06")
    assert len(d["financial_records"]) == 1
    assert d["financial_records"][0]["valor_brl"] == 3500.00
    assert d["context_hints"]["day_has_payment"] is True
    assert d["context_hints"]["day_has_contract_establishment"] is True


def test_build_day_dossier_statistics_by_category(db):
    _seed_classification_transcription(
        db, obra="O", idx=1, date="2026-04-06",
        category="pagamento", text="x",
    )
    _seed_classification_transcription(
        db, obra="O", idx=2, date="2026-04-06",
        category="cronograma", text="y",
    )
    _seed_classification_transcription(
        db, obra="O", idx=3, date="2026-04-06",
        category="cronograma", text="z",
    )
    d = build_day_dossier(db, "O", "2026-04-06")
    assert d["statistics"]["by_primary_category"] == {
        "pagamento": 1, "cronograma": 2,
    }
    assert d["statistics"]["by_source_type"] == {"transcription": 3}


def test_build_day_dossier_secondary_categories_preserved(db):
    _seed_classification_transcription(
        db, obra="OS", idx=1, date="2026-04-06",
        category="reporte_execucao", secondary=["especificacao_tecnica"],
        text="medicao de tubo",
    )
    d = build_day_dossier(db, "OS", "2026-04-06")
    evt = d["events_timeline"][0]
    assert evt["primary_category"] == "reporte_execucao"
    assert evt["secondary_categories"] == ["especificacao_tecnica"]


def test_build_day_dossier_content_full_omitted_when_long(db):
    long_text = "A" * 600
    _seed_classification_transcription(
        db, obra="OL", idx=1, date="2026-04-06", text=long_text,
    )
    d = build_day_dossier(db, "OL", "2026-04-06")
    evt = d["events_timeline"][0]
    assert evt["content_full"] is None
    assert len(evt["content_preview"]) == 150


# ---------------------------------------------------------------------------
# build_obra_overview_dossier
# ---------------------------------------------------------------------------


def test_build_overview_small_obra_returns_all_events(db):
    for i in range(5):
        _seed_classification_transcription(
            db, obra="OV1", idx=i, date=f"2026-04-0{6 + (i % 3)}",
            text=f"evento {i}",
        )
    d = build_obra_overview_dossier(db, "OV1")
    assert d["scope"] == "obra_overview"
    assert d["scope_ref"] is None
    assert d["events_total_in_obra"] == 5
    assert d["events_sampled"] == 5
    assert len(d["events_timeline"]) == 5


def test_build_overview_large_obra_samples_first_last_plus_dense_days(db):
    """
    >50 eventos: amostra primeiros 30 + ultimos 20 UNIAO com todos os
    eventos dos top-5 dias com mais eventos (divida #28). Dias
    sao todos o mesmo aqui, entao sample cobre TUDO (unico dia denso).
    """
    total = OVERVIEW_SAMPLE_FIRST_N + OVERVIEW_SAMPLE_LAST_N + 20
    for i in range(total):
        hour = 8 + (i % 10)
        _seed_classification_transcription(
            db, obra="OVBIG", idx=i, date="2026-04-06",
            hhmm=f"{hour:02d}:{(i * 3) % 60:02d}",
            text=f"evt {i}",
        )
    d = build_obra_overview_dossier(db, "OVBIG")
    assert d["events_total_in_obra"] == total
    # Unico dia denso => todos eventos entram via top-dense-days
    assert d["events_sampled"] == total


def test_build_overview_includes_dense_day_events_not_just_extremes(db):
    """
    Divida #28 regressao: dia denso no meio do corpus deve estar na
    amostra mesmo quando ficaria fora do first-30+last-20 padrao.
    """
    # 60 eventos em 2026-04-01 (forma o first-30 + part of middle)
    for i in range(60):
        _seed_classification_transcription(
            db, obra="OVDENSE", idx=1000 + i, date="2026-04-01",
            hhmm=f"{8 + i // 8:02d}:{(i * 7) % 60:02d}",
            text=f"early {i}",
        )
    # 48 eventos em 2026-04-08 (DIA DENSO ao meio — deve entrar)
    for i in range(48):
        _seed_classification_transcription(
            db, obra="OVDENSE", idx=2000 + i, date="2026-04-08",
            hhmm=f"{8 + i // 5:02d}:{(i * 11) % 60:02d}",
            text=f"dense {i}",
        )
    # 30 eventos em 2026-04-15 (last-N anchor)
    for i in range(30):
        _seed_classification_transcription(
            db, obra="OVDENSE", idx=3000 + i, date="2026-04-15",
            hhmm=f"{8 + i // 4:02d}:{(i * 13) % 60:02d}",
            text=f"late {i}",
        )
    d = build_obra_overview_dossier(db, "OVDENSE")
    # Total de eventos
    assert d["events_total_in_obra"] == 60 + 48 + 30
    # Verifica que ALGUM evento do dia 08/04 esta no sample (diferente
    # do comportamento antigo que perdia esse dia)
    sampled_dates = {e["event_date"] for e in d["events_timeline"]}
    assert "2026-04-08" in sampled_dates
    # E mais: todos os eventos do dia denso devem estar la
    sampled_dense = [
        e for e in d["events_timeline"] if e["event_date"] == "2026-04-08"
    ]
    assert len(sampled_dense) == 48


def test_build_overview_daily_summaries(db):
    _seed_classification_transcription(
        db, obra="OD", idx=1, date="2026-04-06",
        category="pagamento", text="a",
    )
    _seed_classification_transcription(
        db, obra="OD", idx=2, date="2026-04-06",
        category="cronograma", text="b",
    )
    _seed_classification_transcription(
        db, obra="OD", idx=3, date="2026-04-07",
        category="material", text="c",
    )
    d = build_obra_overview_dossier(db, "OD")
    ds = {x["data"]: x for x in d["daily_summaries"]}
    assert ds["2026-04-06"]["events_count"] == 2
    assert ds["2026-04-07"]["events_count"] == 1
    assert "pagamento" in ds["2026-04-06"]["main_topics"]
    assert "material" in ds["2026-04-07"]["main_topics"]


def test_build_overview_includes_all_financial_records(db):
    _seed_classification_transcription(
        db, obra="OFIN", idx=1, date="2026-04-06", text="x",
    )
    _seed_financial_record(
        db, obra="OFIN", idx=1, date="2026-04-06",
        valor_centavos=100000, descricao="a",
    )
    _seed_financial_record(
        db, obra="OFIN", idx=2, date="2026-04-10",
        valor_centavos=200000, descricao="b",
    )
    d = build_obra_overview_dossier(db, "OFIN")
    assert len(d["financial_records"]) == 2


# ---------------------------------------------------------------------------
# compute_dossier_hash
# ---------------------------------------------------------------------------


def test_build_day_dossier_min_correlation_conf_filters(db):
    """Divida #25: threshold remove correlations abaixo."""
    import sqlite3
    # Seed 1 classification + 1 fr + duas correlations (conf 0.5 e 0.9)
    _seed_classification_transcription(
        db, obra="OTH", idx=1, date="2026-04-06", text="evento",
    )
    _seed_financial_record(
        db, obra="OTH", idx=1, date="2026-04-06",
        valor_centavos=100000, descricao="sinal",
    )
    now = "2026-04-22T00:00:00Z"
    cls_id = db.execute(
        "SELECT id FROM classifications WHERE obra='OTH'"
    ).fetchone()["id"]
    fr_id = db.execute(
        "SELECT id FROM financial_records WHERE obra='OTH'"
    ).fetchone()["id"]
    db.execute(
        """INSERT INTO correlations (obra, correlation_type,
        primary_event_ref, primary_event_source,
        related_event_ref, related_event_source, time_gap_seconds,
        confidence, rationale, detected_by, created_at)
        VALUES ('OTH', 'SEMANTIC_PAYMENT_SCOPE', ?, 'financial_record',
                ?, 'classification', 0, 0.5, 'weak', 'x', ?)""",
        (f"fr_{fr_id}", f"c_{cls_id}", now),
    )
    db.execute(
        """INSERT INTO correlations (obra, correlation_type,
        primary_event_ref, primary_event_source,
        related_event_ref, related_event_source, time_gap_seconds,
        confidence, rationale, detected_by, created_at)
        VALUES ('OTH', 'MATH_VALUE_MATCH', ?, 'financial_record',
                ?, 'classification', 0, 0.9, 'strong', 'x', ?)""",
        (f"fr_{fr_id}", f"c_{cls_id}", now),
    )
    db.commit()

    d_all = build_day_dossier(db, "OTH", "2026-04-06")
    assert len(d_all["correlations"]) == 2

    d_filtered = build_day_dossier(
        db, "OTH", "2026-04-06", min_correlation_confidence=0.70,
    )
    assert len(d_filtered["correlations"]) == 1
    assert d_filtered["correlations"][0]["confidence"] == 0.9

    d_strict = build_day_dossier(
        db, "OTH", "2026-04-06", min_correlation_confidence=0.95,
    )
    assert d_strict["correlations"] == []


def test_build_overview_includes_sample_weak(db):
    """Divida #30: sample_weak contem correlacoes 0.40-0.70 (top N por tipo)."""
    _seed_classification_transcription(
        db, obra="OSW", idx=1, date="2026-04-06", text="e",
    )
    _seed_financial_record(
        db, obra="OSW", idx=1, date="2026-04-06",
        valor_centavos=100000, descricao="sinal",
    )
    now = "2026-04-22T00:00:00Z"
    # 3 weak (0.4, 0.5, 0.6) + 1 validated (0.9) + 1 low (0.3 - fora do
    # range weak)
    for conf, ctype in [
        (0.4, "SEMANTIC_PAYMENT_SCOPE"),
        (0.5, "SEMANTIC_PAYMENT_SCOPE"),
        (0.6, "MATH_VALUE_DIVERGENCE"),
        (0.9, "MATH_VALUE_MATCH"),
        (0.3, "TEMPORAL_PAYMENT_CONTEXT"),
    ]:
        db.execute(
            """INSERT INTO correlations (obra, correlation_type,
            primary_event_ref, primary_event_source,
            related_event_ref, related_event_source, time_gap_seconds,
            confidence, rationale, detected_by, created_at)
            VALUES ('OSW', ?, 'fr_1', 'financial_record', 'c_1',
                    'classification', 0, ?, 'r', 'x', ?)""",
            (ctype, conf, now),
        )
    db.commit()
    d = build_obra_overview_dossier(db, "OSW")
    sw = d["correlations_summary"]["sample_weak"]
    # 3 weak (0.4, 0.5, 0.6) — 0.3 fica fora, 0.9 eh validated
    assert len(sw) == 3
    confs = sorted([c["confidence"] for c in sw], reverse=True)
    assert confs == [0.6, 0.5, 0.4]


def test_build_overview_sample_weak_caps_per_type(db):
    """sample_weak limita TOP_PER_TYPE=5 por tipo."""
    from rdo_agent.forensic_agent.dossier_builder import (
        CORRELATION_WEAK_TOP_PER_TYPE,
    )
    _seed_classification_transcription(
        db, obra="OSC", idx=1, date="2026-04-06", text="e",
    )
    _seed_financial_record(
        db, obra="OSC", idx=1, date="2026-04-06",
        valor_centavos=100000, descricao="sinal",
    )
    now = "2026-04-22T00:00:00Z"
    # Insere 8 weak SEMANTIC => deve cappar em TOP_PER_TYPE=5
    for i in range(8):
        conf = 0.4 + i * 0.01
        db.execute(
            """INSERT INTO correlations (obra, correlation_type,
            primary_event_ref, primary_event_source,
            related_event_ref, related_event_source, time_gap_seconds,
            confidence, rationale, detected_by, created_at)
            VALUES ('OSC', 'SEMANTIC_PAYMENT_SCOPE', 'fr_1',
                    'financial_record', 'c_1', 'classification', 0,
                    ?, ?, 'x', ?)""",
            (conf, f"rat {i}", now),
        )
    db.commit()
    d = build_obra_overview_dossier(db, "OSC")
    sw = d["correlations_summary"]["sample_weak"]
    # So ha 1 tipo e cap eh 5 por tipo, limit total 15
    assert len(sw) == CORRELATION_WEAK_TOP_PER_TYPE


def test_build_overview_min_correlation_conf_filters(db):
    _seed_classification_transcription(
        db, obra="OTHV", idx=1, date="2026-04-06", text="e",
    )
    _seed_financial_record(
        db, obra="OTHV", idx=1, date="2026-04-06",
        valor_centavos=100000, descricao="sinal",
    )
    now = "2026-04-22T00:00:00Z"
    db.execute(
        """INSERT INTO correlations (obra, correlation_type,
        primary_event_ref, primary_event_source,
        related_event_ref, related_event_source, time_gap_seconds,
        confidence, rationale, detected_by, created_at)
        VALUES ('OTHV', 'SEMANTIC_PAYMENT_SCOPE', 'fr_1', 'financial_record',
                'c_1', 'classification', 0, 0.4, 'weak', 'x', ?)""",
        (now,),
    )
    db.execute(
        """INSERT INTO correlations (obra, correlation_type,
        primary_event_ref, primary_event_source,
        related_event_ref, related_event_source, time_gap_seconds,
        confidence, rationale, detected_by, created_at)
        VALUES ('OTHV', 'MATH_VALUE_MATCH', 'fr_1', 'financial_record',
                'c_1', 'classification', 0, 0.9, 'strong', 'x', ?)""",
        (now,),
    )
    db.commit()

    d_all = build_obra_overview_dossier(db, "OTHV")
    assert d_all["correlations_summary"]["total"] == 2

    d_filt = build_obra_overview_dossier(
        db, "OTHV", min_correlation_confidence=0.70,
    )
    # Filtro aplicado antes de compor summary
    assert d_filt["correlations_summary"]["total"] == 1


def test_build_day_dossier_without_gt_has_no_ground_truth_field(db):
    _seed_classification_transcription(
        db, obra="OGTN", idx=1, date="2026-04-06", text="a",
    )
    d = build_day_dossier(db, "OGTN", "2026-04-06")
    assert "ground_truth" not in d


def test_build_day_dossier_with_gt_injects_field(db):
    from rdo_agent.ground_truth import (
        Canal, CanalParte, Contrato, GroundTruth, ObraReal,
    )
    _seed_classification_transcription(
        db, obra="OGTY", idx=1, date="2026-04-06", text="a",
    )
    gt = GroundTruth(
        obra_real=ObraReal(nome="X", contratada="Y"),
        canal=Canal(
            id="OGTY", tipo="whatsapp",
            parte_A=CanalParte(nome="A", papel="a"),
            parte_B=CanalParte(nome="B", papel="b"),
        ),
        contratos=[
            Contrato(id="C1", escopo="tesouras", valor_total=7000.0),
        ],
    )
    d = build_day_dossier(db, "OGTY", "2026-04-06", gt=gt)
    assert "ground_truth" in d
    assert d["ground_truth"]["obra_real"]["nome"] == "X"
    assert d["ground_truth"]["contratos"][0]["id"] == "C1"
    # 'raw' nao deve aparecer (redundante)
    assert "raw" not in d["ground_truth"]


def test_build_overview_with_gt_injects_field(db):
    from rdo_agent.ground_truth import (
        Canal, CanalParte, GroundTruth, ObraReal,
    )
    _seed_classification_transcription(
        db, obra="OGV", idx=1, date="2026-04-06", text="a",
    )
    gt = GroundTruth(
        obra_real=ObraReal(nome="X", contratada="Y"),
        canal=Canal(
            id="OGV", tipo="whatsapp",
            parte_A=CanalParte(nome="A", papel="a"),
            parte_B=CanalParte(nome="B", papel="b"),
        ),
    )
    d = build_obra_overview_dossier(db, "OGV", gt=gt)
    assert "ground_truth" in d


def test_dossier_hash_changes_when_gt_added(db):
    from rdo_agent.ground_truth import (
        Canal, CanalParte, GroundTruth, ObraReal,
    )
    _seed_classification_transcription(
        db, obra="OH", idx=1, date="2026-04-06", text="a",
    )
    d_no_gt = build_day_dossier(db, "OH", "2026-04-06")
    gt = GroundTruth(
        obra_real=ObraReal(nome="X", contratada="Y"),
        canal=Canal(
            id="OH", tipo="whatsapp",
            parte_A=CanalParte(nome="A", papel="a"),
            parte_B=CanalParte(nome="B", papel="b"),
        ),
    )
    d_with_gt = build_day_dossier(db, "OH", "2026-04-06", gt=gt)
    # Hashes diferentes => cache invalidado automatico
    assert compute_dossier_hash(d_no_gt) != compute_dossier_hash(d_with_gt)


def test_compute_dossier_hash_deterministic():
    d1 = {"a": 1, "b": 2}
    d2 = {"b": 2, "a": 1}
    assert compute_dossier_hash(d1) == compute_dossier_hash(d2)


def test_compute_dossier_hash_differs_when_content_differs():
    d1 = {"a": 1}
    d2 = {"a": 2}
    assert compute_dossier_hash(d1) != compute_dossier_hash(d2)


def test_compute_dossier_hash_sha256_format():
    h = compute_dossier_hash({"x": "y"})
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
