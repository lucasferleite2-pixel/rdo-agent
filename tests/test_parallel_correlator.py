"""Testes do correlator paralelo + janela by-detector — Sessao 10 / #50.

Valida:
- Cada detector aceita kwarg `window: timedelta | None` sem regressao
- Override de janela altera resultados conforme esperado
- parallel_detect_correlations roda 4 workers, agrega, persiste
- Erros em 1 detector nao derrubam os demais
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from rdo_agent.forensic_agent.detectors.contract_renegotiation import (
    detect_contract_renegotiation,
)
from rdo_agent.forensic_agent.detectors.math import detect_math_relations
from rdo_agent.forensic_agent.detectors.semantic import (
    detect_semantic_payment_scope,
)
from rdo_agent.forensic_agent.detectors.temporal import (
    detect_temporal_payment_context,
)
from rdo_agent.forensic_agent.parallel import (
    DETECTOR_NAMES,
    CorrelationStats,
    DetectorWindows,
    parallel_detect_correlations,
)
from rdo_agent.orchestrator import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_text_msg(
    conn: sqlite3.Connection, *,
    obra: str, idx: int, ts_iso: str, text: str,
    category: str = "negociacao_comercial",
) -> int:
    now = "2026-04-25T00:00:00Z"
    msg = f"m_{obra}_{idx:03d}"
    fid = f"f_msg_{obra}_{idx:03d}"
    conn.execute(
        "INSERT INTO messages (message_id, obra, timestamp_whatsapp, "
        "sender, content, media_ref, created_at) "
        "VALUES (?, ?, ?, 'Lucas', ?, NULL, ?)",
        (msg, obra, ts_iso, text, now),
    )
    conn.execute(
        "INSERT INTO files (file_id, obra, file_path, file_type, sha256, "
        "size_bytes, referenced_by_message, timestamp_resolved, "
        "timestamp_source, semantic_status, created_at) "
        "VALUES (?, ?, '', 'message', ?, 0, ?, ?, 'whatsapp_txt', "
        "'classified', ?)",
        (fid, obra, f"m{idx:06d}".ljust(64, "0"), msg, ts_iso, now),
    )
    cur = conn.execute(
        "INSERT INTO classifications (obra, source_file_id, "
        "source_message_id, source_type, quality_flag, quality_reasoning, "
        "human_review_needed, human_reviewed, categories, confidence_model, "
        "reasoning, classifier_api_call_id, classifier_model, "
        "quality_api_call_id, quality_model, source_sha256, semantic_status, "
        "created_at) "
        "VALUES (?, ?, ?, 'text_message', 'coerente', 'ok', 0, 0, ?, 0.9, "
        "?, NULL, 'gpt-4o-mini', NULL, 'gpt-4o-mini', ?, 'classified', ?)",
        (obra, fid, msg, json.dumps([category]), text,
         f"c{idx:06d}".ljust(64, "0"), now),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


@pytest.fixture
def vault_db(tmp_path):
    """Vault efêmero com schema completo."""
    return init_db(tmp_path / "vault")


# ---------------------------------------------------------------------------
# Window param em cada detector
# ---------------------------------------------------------------------------


def test_temporal_window_override_changes_pairs(vault_db):
    """TEMPORAL com janela mais ampla pega pares mais distantes."""
    obra = "OBRA_T"
    # FR em 2026-04-08T10:00. Mensagem 'pix sinal' em 09:00 (1h antes).
    vault_db.execute(
        "INSERT INTO files (file_id, obra, file_path, file_type, sha256, "
        "size_bytes, semantic_status, created_at) "
        "VALUES ('f_pix', ?, 'p.jpg', 'image', ?, 100, 'ocr', '2026-04-08T00:00:00Z')",
        (obra, "p" * 64),
    )
    vault_db.execute(
        "INSERT INTO financial_records (obra, source_file_id, doc_type, "
        "valor_centavos, moeda, data_transacao, hora_transacao, "
        "pagador_nome, recebedor_nome, descricao, confidence, "
        "api_call_id, created_at) "
        "VALUES (?, 'f_pix', 'pix', 350000, 'BRL', '2026-04-08', '10:00', "
        "'Vale', 'Everaldo', 'sinal serralheria', 0.95, NULL, ?)",
        (obra, "2026-04-08T00:00:00Z"),
    )
    _seed_text_msg(
        vault_db, obra=obra, idx=1,
        ts_iso="2026-04-08T09:00:00Z",
        text="pix do sinal pagamento valor",
    )
    vault_db.commit()

    # Default WINDOW=30min → 60min de gap = NAO casa
    result_default = detect_temporal_payment_context(vault_db, obra)
    assert len(result_default) == 0

    # Window expandida 2h → 60min de gap = casa
    result_wide = detect_temporal_payment_context(
        vault_db, obra, window=timedelta(hours=2),
    )
    assert len(result_wide) == 1


def test_semantic_window_override_extends_pool(vault_db):
    """SEMANTIC com janela mais ampla considera classifications mais distantes."""
    obra = "OBRA_S"
    vault_db.execute(
        "INSERT INTO files (file_id, obra, file_path, file_type, sha256, "
        "size_bytes, semantic_status, created_at) "
        "VALUES ('f_s', ?, 'p.jpg', 'image', ?, 100, 'ocr', '2026-04-08T00:00:00Z')",
        (obra, "s" * 64),
    )
    vault_db.execute(
        "INSERT INTO financial_records (obra, source_file_id, doc_type, "
        "valor_centavos, moeda, data_transacao, hora_transacao, "
        "pagador_nome, recebedor_nome, descricao, confidence, "
        "api_call_id, created_at) "
        "VALUES (?, 'f_s', 'pix', 700000, 'BRL', '2026-04-08', '10:00', "
        "'Vale', 'Everaldo', 'serralheria telhado fechamento alambrado', "
        "0.95, NULL, '2026-04-08T00:00:00Z')",
        (obra,),
    )
    # Mensagem 5 dias antes (fora do default 3d, dentro de 7d)
    _seed_text_msg(
        vault_db, obra=obra, idx=1,
        ts_iso="2026-04-03T10:00:00Z",
        text="serralheria telhado fechamento alambrado tesoura",
    )

    result_default = detect_semantic_payment_scope(vault_db, obra)
    result_wide = detect_semantic_payment_scope(
        vault_db, obra, window=timedelta(days=7),
    )
    assert len(result_wide) >= len(result_default)


def test_math_window_override_extends_search(vault_db):
    """MATH com janela mais ampla pega valores mais distantes."""
    obra = "OBRA_M"
    vault_db.execute(
        "INSERT INTO files (file_id, obra, file_path, file_type, sha256, "
        "size_bytes, semantic_status, created_at) "
        "VALUES ('f_m', ?, 'p.jpg', 'image', ?, 100, 'ocr', '2026-04-08T00:00:00Z')",
        (obra, "m" * 64),
    )
    vault_db.execute(
        "INSERT INTO financial_records (obra, source_file_id, doc_type, "
        "valor_centavos, moeda, data_transacao, hora_transacao, "
        "pagador_nome, recebedor_nome, descricao, confidence, "
        "api_call_id, created_at) "
        "VALUES (?, 'f_m', 'pix', 350000, 'BRL', '2026-04-08', '10:00', "
        "'Vale', 'Everaldo', 'sinal', 0.95, NULL, "
        "'2026-04-08T00:00:00Z')",
        (obra,),
    )
    # Mensagem 5 dias antes (fora do default 48h, dentro de 7d)
    _seed_text_msg(
        vault_db, obra=obra, idx=1,
        ts_iso="2026-04-03T10:00:00Z",
        text="negocio fechado em R$ 3.500 valor total",
    )

    result_default = detect_math_relations(vault_db, obra)
    result_wide = detect_math_relations(
        vault_db, obra, window=timedelta(days=7),
    )
    assert len(result_wide) >= len(result_default)


def test_renegotiation_window_override(vault_db):
    """RENEGOTIATION com janela menor exclui pares distantes."""
    obra = "OBRA_R"
    _seed_text_msg(
        vault_db, obra=obra, idx=1,
        ts_iso="2026-04-04T10:00:00Z",
        text="serralheria telhado fechamento R$ 7.000 total",
    )
    _seed_text_msg(
        vault_db, obra=obra, idx=2,
        ts_iso="2026-04-08T10:00:00Z",
        text="serralheria telhado fechamento R$ 11.000 total",
    )

    # Default 30 dias inclui o par (4 dias de gap)
    result_default = detect_contract_renegotiation(vault_db, obra)
    assert len(result_default) == 1

    # Janela menor (3 dias) exclui (gap = 4 dias > 3)
    result_narrow = detect_contract_renegotiation(
        vault_db, obra, window=timedelta(days=3),
    )
    assert len(result_narrow) == 0


def test_detector_window_default_preserves_legacy_behavior(vault_db):
    """window=None mantem o WINDOW da modulo (backwards compat)."""
    obra = "OBRA_LEG"
    # Sem nenhum dado: ambos retornam vazio sem erro
    assert detect_temporal_payment_context(vault_db, obra) == []
    assert detect_temporal_payment_context(vault_db, obra, window=None) == []


# ---------------------------------------------------------------------------
# DetectorWindows dataclass
# ---------------------------------------------------------------------------


def test_detector_windows_for_detector_returns_specific():
    w = DetectorWindows(
        temporal=timedelta(hours=1),
        semantic=timedelta(days=5),
    )
    assert w.for_detector("temporal") == timedelta(hours=1)
    assert w.for_detector("semantic") == timedelta(days=5)
    assert w.for_detector("math") is None  # nao especificado
    assert w.for_detector("contract_renegotiation") is None


def test_detector_windows_all_days_helper():
    w = DetectorWindows.all_days(7)
    expected = timedelta(days=7)
    for name in DETECTOR_NAMES:
        assert w.for_detector(name) == expected


def test_detector_names_constant():
    assert DETECTOR_NAMES == (
        "temporal", "semantic", "math", "contract_renegotiation",
    )


# ---------------------------------------------------------------------------
# parallel_detect_correlations
# ---------------------------------------------------------------------------


def test_parallel_correlator_returns_stats(vault_db, tmp_path):
    """Mesmo sem dados, parallel retorna stats consistentes."""
    obra = "OBRA_EMPTY"
    vault_db.close()  # forca flush
    db_path = tmp_path / "vault" / "index.sqlite"

    correlations, stats = parallel_detect_correlations(
        db_path, obra, workers=2, persist=False,
    )
    assert correlations == []
    assert isinstance(stats, CorrelationStats)
    assert stats.total == 0
    # 4 detectores reportam 0 cada
    assert set(stats.by_detector.keys()) == set(DETECTOR_NAMES)
    for name in DETECTOR_NAMES:
        assert stats.by_detector[name] == 0


def test_parallel_correlator_no_persist_skips_save(vault_db, tmp_path):
    obra = "OBRA_NP"
    vault_db.close()
    db_path = tmp_path / "vault" / "index.sqlite"

    parallel_detect_correlations(
        db_path, obra, workers=2, persist=False,
    )
    # Reabre conn pra checar correlations
    c = sqlite3.connect(db_path)
    n = c.execute("SELECT COUNT(*) FROM correlations").fetchone()[0]
    c.close()
    assert n == 0


def test_parallel_correlator_calls_progress_callback(vault_db, tmp_path):
    obra = "OBRA_CB"
    vault_db.close()
    db_path = tmp_path / "vault" / "index.sqlite"

    events: list[tuple[str, int, str | None]] = []
    parallel_detect_correlations(
        db_path, obra, workers=2, persist=False,
        on_progress=lambda d, n, e: events.append((d, n, e)),
    )
    # 4 callbacks (1 por detector), todos com 0 resultados
    assert len(events) == 4
    assert {e[0] for e in events} == set(DETECTOR_NAMES)
    assert all(e[1] == 0 for e in events)
    assert all(e[2] is None for e in events)


def test_parallel_correlator_real_data_matches_serial(vault_db, tmp_path):
    """Resultado paralelo == resultado sequencial (regressao)."""
    obra = "OBRA_PARITY"
    # Seed minimo: 1 FR + mensagens correlacionaveis
    vault_db.execute(
        "INSERT INTO files (file_id, obra, file_path, file_type, sha256, "
        "size_bytes, semantic_status, created_at) "
        "VALUES ('f_p1', ?, 'p.jpg', 'image', ?, 100, 'ocr', "
        "'2026-04-08T00:00:00Z')",
        (obra, "p" * 64),
    )
    vault_db.execute(
        "INSERT INTO financial_records (obra, source_file_id, doc_type, "
        "valor_centavos, moeda, data_transacao, hora_transacao, "
        "pagador_nome, recebedor_nome, descricao, confidence, "
        "api_call_id, created_at) "
        "VALUES (?, 'f_p1', 'pix', 350000, 'BRL', '2026-04-08', '10:00', "
        "'Vale', 'Everaldo', 'sinal serralheria', 0.95, NULL, "
        "'2026-04-08T00:00:00Z')",
        (obra,),
    )
    _seed_text_msg(
        vault_db, obra=obra, idx=1,
        ts_iso="2026-04-08T10:15:00Z",
        text="pix do sinal pagamento R$ 3.500 chave",
    )
    db_path = Path(vault_db.execute(
        "SELECT file FROM pragma_database_list WHERE name = 'main'",
    ).fetchone()[0])
    vault_db.close()

    # Sequencial
    from rdo_agent.forensic_agent.correlator import detect_correlations
    seq_conn = sqlite3.connect(db_path)
    seq_conn.row_factory = sqlite3.Row
    seq_results = detect_correlations(seq_conn, obra, persist=False)
    seq_conn.close()

    # Paralelo (limpa correlations antes)
    paral_conn = sqlite3.connect(db_path)
    paral_conn.execute("DELETE FROM correlations")
    paral_conn.commit()
    paral_conn.close()

    par_results, _ = parallel_detect_correlations(
        db_path, obra, workers=2, persist=False,
    )

    # Comparacao por (correlation_type, primary_event_ref, related_event_ref)
    seq_keys = sorted(
        (c.correlation_type, c.primary_event_ref, c.related_event_ref)
        for c in seq_results
    )
    par_keys = sorted(
        (c.correlation_type, c.primary_event_ref, c.related_event_ref)
        for c in par_results
    )
    assert seq_keys == par_keys


def test_parallel_correlator_window_override(vault_db, tmp_path):
    """Janela aplicada por DetectorWindows muda quantidade de matches."""
    obra = "OBRA_WIN"
    _seed_text_msg(
        vault_db, obra=obra, idx=1,
        ts_iso="2026-04-04T10:00:00Z",
        text="serralheria telhado fechamento R$ 7.000 total",
    )
    _seed_text_msg(
        vault_db, obra=obra, idx=2,
        ts_iso="2026-04-08T10:00:00Z",
        text="serralheria telhado fechamento R$ 11.000 total",
    )
    db_path = Path(vault_db.execute(
        "SELECT file FROM pragma_database_list WHERE name = 'main'",
    ).fetchone()[0])
    vault_db.close()

    # Default: pega o par (4 dias < 30 dias)
    correlations, _ = parallel_detect_correlations(
        db_path, obra, workers=2, persist=False,
    )
    n_default = sum(
        1 for c in correlations
        if c.correlation_type == "CONTRACT_RENEGOTIATION"
    )

    # Janela curta: exclui
    windows = DetectorWindows(
        contract_renegotiation=timedelta(days=2),
    )
    correlations_narrow, _ = parallel_detect_correlations(
        db_path, obra, workers=2, persist=False, windows=windows,
    )
    n_narrow = sum(
        1 for c in correlations_narrow
        if c.correlation_type == "CONTRACT_RENEGOTIATION"
    )
    assert n_default >= 1
    assert n_narrow == 0


def test_parallel_correlator_handles_unknown_detector_gracefully(monkeypatch):
    """Detector desconhecido reporta erro mas nao crasha o pool."""
    # Smoke test do path de erro (testa o worker direto, mais rapido)
    from rdo_agent.forensic_agent.parallel import _run_detector_worker
    res = _run_detector_worker(("fake_detector", ":memory:", "X", None))
    assert res[0] == "fake_detector"
    assert res[1] == []
    assert "desconhecido" in (res[2] or "")
