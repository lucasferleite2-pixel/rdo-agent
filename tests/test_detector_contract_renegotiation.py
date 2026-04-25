"""Testes detector CONTRACT_RENEGOTIATION — Sessao 5, divida #27."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from rdo_agent.forensic_agent.detectors.contract_renegotiation import (
    CONF_MEDIUM,
    CONF_STRONG,
    WINDOW,
    detect_contract_renegotiation,
)
from rdo_agent.forensic_agent.types import CorrelationType
from rdo_agent.orchestrator import init_db


# ---------------------------------------------------------------------------
# Helpers (espelho mais simples do test_detector_temporal)
# ---------------------------------------------------------------------------


def _seed_text_cls(
    conn: sqlite3.Connection, *,
    obra: str, idx: int, ts_iso: str, text: str,
    category: str = "negociacao_comercial",
) -> int:
    """Insere uma mensagem texto + classification classified."""
    now = "2026-04-22T00:00:00Z"
    msg = f"m_{obra}_{idx:03d}"
    fid = f"f_msg_{obra}_{idx:03d}"
    conn.execute(
        """INSERT INTO messages (message_id, obra, timestamp_whatsapp,
        sender, content, media_ref, created_at)
        VALUES (?, ?, ?, 'Lucas', ?, NULL, ?)""",
        (msg, obra, ts_iso, text, now),
    )
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, referenced_by_message, timestamp_resolved,
        timestamp_source, semantic_status, created_at)
        VALUES (?, ?, '', 'message', ?, 0, ?, ?, 'whatsapp_txt',
        'classified', ?)""",
        (fid, obra,
         f"m{idx:06d}".ljust(64, "0"),
         msg, ts_iso, now),
    )
    cur = conn.execute(
        """INSERT INTO classifications (
            obra, source_file_id, source_message_id, source_type,
            quality_flag, quality_reasoning, human_review_needed,
            human_reviewed, categories, confidence_model, reasoning,
            classifier_api_call_id, classifier_model,
            quality_api_call_id, quality_model,
            source_sha256, semantic_status, created_at
        ) VALUES (?, ?, ?, 'text_message',
                  'coerente', 'ok', 0, 0, ?, 0.9, ?,
                  NULL, 'gpt-4o-mini', NULL, 'gpt-4o-mini', ?,
                  'classified', ?)""",
        (obra, fid, msg, json.dumps([category]), text,
         f"c{idx:06d}".ljust(64, "0"), now),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path)


# ---------------------------------------------------------------------------
# Casos minimos
# ---------------------------------------------------------------------------


def test_renegotiation_basic_pattern_emits_correlation(db):
    """
    Pattern empirico EVERALDO: R$ 7.000 (04/04) -> R$ 11.000 (08/04),
    ambos discutindo serralheria/fechamento. Deve emitir 1 correlacao
    CONTRACT_RENEGOTIATION.
    """
    obra = "OBRA_REN"
    _seed_text_cls(
        db, obra=obra, idx=1, ts_iso="2026-04-04T10:00:00Z",
        text=(
            "Fechamos a serralheria do telhado por R$ 7.000 total — "
            "tesoura, terca e ripamento incluidos."
        ),
    )
    _seed_text_cls(
        db, obra=obra, idx=2, ts_iso="2026-04-08T15:00:00Z",
        text=(
            "Reajustando o fechamento: serralheria com alambrado "
            "agora fica em R$ 11.000 total. Inclui telhado completo."
        ),
    )

    out = detect_contract_renegotiation(db, obra=obra)
    assert len(out) == 1
    c = out[0]
    assert c.correlation_type == CorrelationType.CONTRACT_RENEGOTIATION.value
    # Primary = mensagem mais antiga (negociacao inicial)
    assert c.primary_event_ref == "c_1"
    assert c.related_event_ref == "c_2"
    # Confidence anchored em ≥1 HIGH stem (serralheria/telh/fechamento/etc)
    assert c.confidence >= CONF_MEDIUM
    # Time gap em segundos: 4 dias e 5 horas = 4*86400 + 5*3600 = 363600
    assert c.time_gap_seconds == 4 * 86400 + 5 * 3600
    # Rationale legivel
    assert "renegociação" in c.rationale or "renegociacao" in c.rationale
    assert "R$" in c.rationale


def test_renegotiation_strong_when_high_overlap_and_sweet_spot_diff(db):
    """
    Diff em [20%, 70%] + 2+ stems HIGH compartilhados => CONF_STRONG (0.85).
    """
    obra = "OBRA_HIGH"
    _seed_text_cls(
        db, obra=obra, idx=1, ts_iso="2026-04-04T10:00:00Z",
        text=(
            "Sinal de R$ 5.000 do serralheria — tesoura terca telhado "
            "fechamento alambrado tudo incluso."
        ),
    )
    _seed_text_cls(
        db, obra=obra, idx=2, ts_iso="2026-04-08T15:00:00Z",
        text=(
            "Total reajustado para R$ 8.000 — serralheria telhado "
            "fechamento alambrado tesoura terca ripamento."
        ),
    )
    out = detect_contract_renegotiation(db, obra=obra)
    assert len(out) == 1
    # Diff = 3000/8000 = 37.5% (sweet spot 20-70)
    # Stems HIGH compartilhados: serralheria, telh, fecha, alambrad, tesoura, terca
    # >=2 => deve cair em CONF_STRONG
    assert out[0].confidence == CONF_STRONG


def test_renegotiation_no_match_when_unrelated_topics(db):
    """
    Mesmas datas/valores, mas textos sem nenhum stem HIGH em comum
    e poucos stems gerais => sem deteccao.
    """
    obra = "OBRA_UNREL"
    _seed_text_cls(
        db, obra=obra, idx=1, ts_iso="2026-04-04T10:00:00Z",
        text="Aluguel do galpao mensal R$ 7.000 — administrativo escritorio.",
    )
    _seed_text_cls(
        db, obra=obra, idx=2, ts_iso="2026-04-08T15:00:00Z",
        text="Parecer juridico do contencioso tributario R$ 11.000 reais.",
    )
    out = detect_contract_renegotiation(db, obra=obra)
    assert out == []


def test_renegotiation_no_match_outside_30d_window(db):
    """Pares separados por > 30 dias nao sao correlacionados."""
    obra = "OBRA_FAR"
    _seed_text_cls(
        db, obra=obra, idx=1, ts_iso="2026-03-01T10:00:00Z",
        text="Serralheria telhado fechamento alambrado por R$ 7.000.",
    )
    # 32 dias depois (fora da janela)
    _seed_text_cls(
        db, obra=obra, idx=2, ts_iso="2026-04-02T15:00:00Z",
        text="Serralheria telhado fechamento alambrado por R$ 11.000.",
    )
    out = detect_contract_renegotiation(db, obra=obra)
    assert out == []
    # Sanity: deslocando 1 dia para dentro da janela emite
    assert WINDOW == timedelta(days=30)


def test_renegotiation_no_match_when_diff_below_10pct(db):
    """Variacao < 10% nao caracteriza renegociacao (mesma negociacao)."""
    obra = "OBRA_SMALL"
    _seed_text_cls(
        db, obra=obra, idx=1, ts_iso="2026-04-04T10:00:00Z",
        text="Serralheria telhado fechamento alambrado por R$ 10.000.",
    )
    _seed_text_cls(
        db, obra=obra, idx=2, ts_iso="2026-04-05T10:00:00Z",
        text="Serralheria telhado fechamento alambrado: R$ 10.500 — ajuste pequeno.",
    )
    out = detect_contract_renegotiation(db, obra=obra)
    assert out == []  # 5% diff fica abaixo do threshold de 10%


def test_renegotiation_no_match_when_diff_above_80pct(db):
    """Variacao > 80% provavelmente eh outro item, nao renegociacao."""
    obra = "OBRA_HUGE"
    _seed_text_cls(
        db, obra=obra, idx=1, ts_iso="2026-04-04T10:00:00Z",
        text="Serralheria telhado por R$ 1.000 — peca pequena.",
    )
    _seed_text_cls(
        db, obra=obra, idx=2, ts_iso="2026-04-08T10:00:00Z",
        text="Serralheria telhado por R$ 50.000 — projeto inteiro.",
    )
    out = detect_contract_renegotiation(db, obra=obra)
    assert out == []  # 98% diff e' item diferente, nao renegociacao


def test_renegotiation_unitary_values_are_ignored(db):
    """
    Valores UNITARY (R$/metro) nao sao considerados — renegociacao
    contratual eh sobre valor agregado.
    """
    obra = "OBRA_UNITARY"
    _seed_text_cls(
        db, obra=obra, idx=1, ts_iso="2026-04-04T10:00:00Z",
        text="Serralheria telhado fechamento R$ 50 por metro de tesoura.",
    )
    _seed_text_cls(
        db, obra=obra, idx=2, ts_iso="2026-04-08T15:00:00Z",
        text="Serralheria telhado fechamento R$ 80 por metro de alambrado.",
    )
    out = detect_contract_renegotiation(db, obra=obra)
    assert out == []


def test_renegotiation_against_everaldo_corpus_optional():
    """
    Validacao empirica sobre o vault EVERALDO_SANTAQUITERIA, se
    disponivel. Skipa graciosamente em CI/ambientes sem vault.

    Esperado: pelo menos 1 correlacao CONTRACT_RENEGOTIATION para o
    padrao 04/04 -> 08/04 (R$ 7.000 -> R$ 11.000).
    """
    vault_root = Path(
        os.path.expanduser("~/rdo_vaults/EVERALDO_SANTAQUITERIA"),
    )
    db_path = vault_root / "index.sqlite"
    if not db_path.exists():
        pytest.skip(f"Vault nao disponivel: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # _common.fetch_event_texts requer Row
    try:
        out = detect_contract_renegotiation(conn, obra="EVERALDO_SANTAQUITERIA")
    finally:
        conn.close()

    # O detector deve achar pelo menos 1 par (04/04 vs 08/04 do canal real)
    assert len(out) >= 1, (
        "Detector nao identificou renegociacao no corpus EVERALDO; "
        "investigar por que o caso empirico (R$ 7k -> R$ 11k) ficou fora "
        "dos thresholds atuais."
    )
    # Alguma correlacao deve ter confidence >= MEDIUM
    assert any(c.confidence >= CONF_MEDIUM for c in out)
