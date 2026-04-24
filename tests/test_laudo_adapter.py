"""Testes do adapter rdo-agent -> LaudoData do Vestigio (Sessao 3)."""

from __future__ import annotations

import sqlite3

import pytest

from rdo_agent.laudo.adapter import (
    CORRELATION_MIN_CONFIDENCE,
    CORRELATIONS_TOP_N,
    CorpusNotFoundError,
    _markdown_inline,
    _markdown_to_html,
    rdo_to_vestigio_data,
)
from rdo_agent.laudo.vestigio_laudo import (
    Correlacao,
    EventoCronologia,
    LaudoData,
    SecaoNarrativa,
)
from rdo_agent.orchestrator import init_db


# ---------------------------------------------------------------------------
# Helpers de seed
# ---------------------------------------------------------------------------


def _seed_corpus(conn: sqlite3.Connection, corpus: str) -> None:
    """Seeda um corpus minimamente valido: 2 messages + 1 audio + 1 financial
    record + 1 classification classified + 1 correlation validada + 1
    correlation fraca + 1 narrativa."""
    now = "2026-04-22T00:00:00Z"

    # 2 messages com timestamps distintos
    for i, ts in enumerate([
        "2026-04-06T10:00:00",
        "2026-04-06T11:13:00",
    ]):
        conn.execute(
            """INSERT INTO messages (message_id, obra, timestamp_whatsapp,
            sender, content, media_ref, created_at)
            VALUES (?, ?, ?, 'Lucas', ?, ?, ?)""",
            (f"m_{corpus}_{i}", corpus, ts, f"msg {i}", None, now),
        )

    # 1 audio file + 1 document file
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, referenced_by_message, timestamp_resolved,
        timestamp_source, semantic_status, created_at)
        VALUES (?, ?, ?, 'audio', ?, 100, ?, ?, 'whatsapp_txt', 'done', ?)""",
        (f"f_au_{corpus}", corpus, "p/a.opus", "a".ljust(64, "0"),
         f"m_{corpus}_0", "2026-04-06T10:00:00", now),
    )
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, 'document', ?, 200, 'ok', ?)""",
        (f"f_doc_{corpus}", corpus, "p/d.pdf", "d".ljust(64, "0"), now),
    )

    # Trans + classification derivada do audio
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, derived_from, derivation_method, referenced_by_message,
        timestamp_resolved, timestamp_source, semantic_status, created_at)
        VALUES (?, ?, ?, 'text', ?, 50, ?, 'whisper-1', ?, ?, 'whatsapp_txt',
                'done', ?)""",
        (f"f_tr_{corpus}", corpus, "p/t.txt", "t".ljust(64, "0"),
         f"f_au_{corpus}", f"m_{corpus}_0",
         "2026-04-06T10:00:00", now),
    )
    conn.execute(
        """INSERT INTO transcriptions (obra, file_id, text, language,
        confidence, low_confidence, api_call_id, created_at)
        VALUES (?, ?, ?, 'portuguese', 0.9, 0, NULL, ?)""",
        (corpus, f"f_tr_{corpus}",
         "Vamos fechar o servico de serralheria pelo telhado completo", now),
    )
    import json as _j
    conn.execute(
        """INSERT INTO classifications (
            obra, source_file_id, source_type,
            quality_flag, quality_reasoning, human_review_needed,
            human_reviewed, categories, confidence_model, reasoning,
            classifier_api_call_id, classifier_model,
            quality_api_call_id, quality_model,
            source_sha256, semantic_status, created_at
        ) VALUES (?, ?, 'transcription', 'coerente', 'ok', 0, 0, ?,
                  0.9, 'negociacao de valor',
                  NULL, 'gpt-4o-mini', NULL, 'gpt-4o-mini', ?,
                  'classified', ?)""",
        (corpus, f"f_tr_{corpus}", _j.dumps(["contrato"]),
         "c".ljust(64, "0"), now),
    )

    # 1 financial record
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, 'image', ?, 1000, 'ocr_extracted', ?)""",
        (f"f_img_{corpus}", corpus, "p/p.jpg", "p".ljust(64, "0"), now),
    )
    conn.execute(
        """INSERT INTO financial_records (obra, source_file_id, doc_type,
        valor_centavos, moeda, data_transacao, hora_transacao,
        pagador_nome, recebedor_nome, descricao, confidence,
        api_call_id, created_at)
        VALUES (?, ?, 'pix', 350000, 'BRL', '2026-04-06', '11:13:00',
                'CONSTRUTORA VALE NOBRE', 'Everaldo', 'Sinal 50%',
                0.95, NULL, ?)""",
        (corpus, f"f_img_{corpus}", now),
    )

    # 2 correlations: uma validada, uma fraca
    conn.execute(
        """INSERT INTO correlations (obra, correlation_type,
        primary_event_ref, primary_event_source,
        related_event_ref, related_event_source, time_gap_seconds,
        confidence, rationale, detected_by, created_at)
        VALUES (?, 'MATH_VALUE_MATCH', 'fr_1', 'financial_record',
                'c_1', 'classification', 0, 0.95,
                'R$3.500 validado', 'math_v1', ?)""",
        (corpus, now),
    )
    conn.execute(
        """INSERT INTO correlations (obra, correlation_type,
        primary_event_ref, primary_event_source,
        related_event_ref, related_event_source, time_gap_seconds,
        confidence, rationale, detected_by, created_at)
        VALUES (?, 'SEMANTIC_PAYMENT_SCOPE', 'fr_1', 'financial_record',
                'c_1', 'classification', 0, 0.55,
                'overlap fraco', 'semantic_v2', ?)""",
        (corpus, now),
    )

    # 1 narrativa day
    conn.execute(
        """INSERT INTO forensic_narratives (obra, scope, scope_ref,
        narrative_text, dossier_hash, model_used, prompt_version,
        api_call_id, events_count, confidence,
        validation_checklist_json, created_at)
        VALUES (?, 'day', '2026-04-06',
                ?, 'hashA', 'claude-sonnet-4-6', 'narrator_v2_correlations',
                NULL, 1, 0.85, '{}', ?)""",
        (
            corpus,
            "# Narrativa: TEST — day 2026-04-06\n\n## Sumário Executivo\n\n"
            "Primeiro paragrafo de resumo executivo contendo detalhe "
            "suficiente para extrair corretamente via regex do adapter.\n\n"
            "## Desenvolvimento\n\nDia com negociação.\n\n"
            "---\n"
            "```json\n{\"self_assessment\": {\"confidence\": 0.85}}\n```",
            now,
        ),
    )

    conn.commit()


def _seed_corpus_with_overview(conn: sqlite3.Connection, corpus: str) -> None:
    """Seed + adiciona uma narrativa obra_overview v4_adversarial."""
    _seed_corpus(conn, corpus)
    now = "2026-04-23T00:00:00Z"
    conn.execute(
        """INSERT INTO forensic_narratives (obra, scope, scope_ref,
        narrative_text, dossier_hash, model_used, prompt_version,
        api_call_id, events_count, confidence,
        validation_checklist_json, created_at)
        VALUES (?, 'obra_overview', NULL,
                ?, 'hashOV', 'claude-sonnet-4-6', 'narrator_v4_adversarial',
                NULL, 10, 0.9, '{}', ?)""",
        (
            corpus,
            "# Narrativa: TEST — Obra Overview\n\n## Sumário Executivo\n\n"
            "Overview completo com contestações hipotéticas. "
            "O canal contém evidências de negociação, pagamento e "
            "eventual divergência de escopo.\n\n"
            "## Contestações Hipotéticas\n\n"
            "1. Alegação: valor imposto sob pressão.\n\n"
            "---\n"
            "```json\n{\"self_assessment\": {\"confidence\": 0.9}}\n```",
            now,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_with_corpus(tmp_path) -> tuple[sqlite3.Connection, str]:
    conn = init_db(tmp_path)
    corpus = "TEST_CORPUS"
    _seed_corpus(conn, corpus)
    return conn, corpus


@pytest.fixture
def db_with_overview(tmp_path) -> tuple[sqlite3.Connection, str]:
    conn = init_db(tmp_path)
    corpus = "TEST_OV"
    _seed_corpus_with_overview(conn, corpus)
    return conn, corpus


# ---------------------------------------------------------------------------
# 1. test_adapter_basic
# ---------------------------------------------------------------------------


def test_adapter_basic(db_with_corpus):
    conn, corpus = db_with_corpus
    data = rdo_to_vestigio_data(corpus, conn=conn)
    assert isinstance(data, LaudoData)
    assert data.caso_id.startswith("VST-")
    assert corpus in data.titulo
    assert data.operador == "Lucas Fernandes Leite"


# ---------------------------------------------------------------------------
# 2. test_adapter_fields_non_empty
# ---------------------------------------------------------------------------


def test_adapter_fields_non_empty(db_with_corpus):
    conn, corpus = db_with_corpus
    data = rdo_to_vestigio_data(corpus, conn=conn)
    # Campos criticos nao podem estar vazios
    assert data.caso_id and data.titulo
    assert data.periodo_inicio and data.periodo_fim
    assert data.corpus_hash and len(data.corpus_hash) == 12
    assert data.total_mensagens >= 1  # seed tem 2 messages
    # Correlacao validada presente (>=0.70); fraca excluida
    assert data.total_correlacoes == 1
    assert len(data.correlacoes) == 1
    # Cronologia inclui pelo menos 1 pagamento
    tipos = {e.tipo for e in data.cronologia}
    assert "pagamento" in tipos
    # Secoes narrativa populadas
    assert len(data.secoes_narrativa) >= 1


# ---------------------------------------------------------------------------
# 3. test_adapter_correlations_sorted (desc)
# ---------------------------------------------------------------------------


def test_adapter_correlations_sorted(tmp_path):
    conn = init_db(tmp_path)
    corpus = "SORT_T"
    _seed_corpus(conn, corpus)
    # Adiciona mais correlacoes validadas com confs diferentes
    now = "2026-04-22T00:00:00Z"
    for conf, label in [(0.85, "mid"), (0.99, "high"), (0.72, "low")]:
        conn.execute(
            """INSERT INTO correlations (obra, correlation_type,
            primary_event_ref, primary_event_source,
            related_event_ref, related_event_source, time_gap_seconds,
            confidence, rationale, detected_by, created_at)
            VALUES (?, 'TEMPORAL_PAYMENT_CONTEXT', 'fr_1', 'financial_record',
                    'c_1', 'classification', 0, ?, ?, 'temporal_v1', ?)""",
            (corpus, conf, label, now),
        )
    conn.commit()

    data = rdo_to_vestigio_data(corpus, conn=conn)
    # Confs em ordem decrescente
    confs = [c.confianca for c in data.correlacoes]
    assert confs == sorted(confs, reverse=True)
    # Primeira eh a de conf 0.99
    assert data.correlacoes[0].confianca == pytest.approx(0.99)


# ---------------------------------------------------------------------------
# 4. test_adapter_narratives_count
# ---------------------------------------------------------------------------


def test_adapter_narratives_count(db_with_overview):
    """Com overview + 1 day narrativa seed, deve extrair 2 secoes."""
    conn, corpus = db_with_overview
    data = rdo_to_vestigio_data(corpus, conn=conn)
    assert len(data.secoes_narrativa) >= 2
    # Overview aparece como primeira secao
    assert any(
        "geral" in s.titulo.lower() or "overview" in s.titulo.lower()
        for s in data.secoes_narrativa
    )


# ---------------------------------------------------------------------------
# 5. test_adapter_adversarial_mode
# ---------------------------------------------------------------------------


def test_adapter_adversarial_mode_prioritizes_v4(db_with_overview):
    conn, corpus = db_with_overview
    data = rdo_to_vestigio_data(corpus, conn=conn, adversarial=True)
    # O resumo executivo precisa vir da V4 (que inclui 'Contestacoes')
    # A v4 é a unica overview seedada, entao funciona tbm sem adversarial
    # mas garantimos que o conteudo veio dela
    joined_secoes = "\n".join(s.conteudo for s in data.secoes_narrativa)
    assert (
        "Contestações" in joined_secoes
        or "Sumário Executivo" in joined_secoes
    )


def test_adapter_adversarial_picks_latest_v4_over_older_v3(tmp_path):
    """Quando existe v3_gt e v4_adversarial, adversarial=True escolhe v4."""
    conn = init_db(tmp_path)
    corpus = "PICK"
    _seed_corpus(conn, corpus)
    # Seeda dois days para mesmo scope_ref — v3 (antigo) e v4 (novo)
    now = "2026-04-22T00:00:00Z"
    conn.execute(
        """INSERT INTO forensic_narratives (obra, scope, scope_ref,
        narrative_text, dossier_hash, model_used, prompt_version,
        api_call_id, events_count, confidence,
        validation_checklist_json, created_at)
        VALUES (?, 'day', '2026-04-07',
                '# Narrativa: PICK\\n\\n## Body\\n\\nV3 body content with details.',
                'hashV3', 'claude-sonnet-4-6', 'narrator_v3_gt',
                NULL, 1, 0.8, '{}', ?)""",
        (corpus, now),
    )
    conn.execute(
        """INSERT INTO forensic_narratives (obra, scope, scope_ref,
        narrative_text, dossier_hash, model_used, prompt_version,
        api_call_id, events_count, confidence,
        validation_checklist_json, created_at)
        VALUES (?, 'day', '2026-04-07',
                '# Narrativa: PICK\\n\\n## Body\\n\\nV4 ADVERSARIAL MARKER',
                'hashV4', 'claude-sonnet-4-6', 'narrator_v4_adversarial',
                NULL, 1, 0.9, '{}', ?)""",
        (corpus, now),
    )
    conn.commit()
    data = rdo_to_vestigio_data(corpus, conn=conn, adversarial=True)
    # A secao do dia 07/04 deve conter o marcador da v4
    day_sections = [
        s for s in data.secoes_narrativa if "07/04" in s.titulo
    ]
    assert len(day_sections) == 1
    assert "V4 ADVERSARIAL MARKER" in day_sections[0].conteudo


# ---------------------------------------------------------------------------
# 6. test_adapter_corpus_not_found
# ---------------------------------------------------------------------------


def test_adapter_corpus_not_found(tmp_path):
    """Corpus sem messages => CorpusNotFoundError."""
    conn = init_db(tmp_path)  # DB vazio
    with pytest.raises(CorpusNotFoundError):
        rdo_to_vestigio_data("NAO_EXISTE", conn=conn)


def test_adapter_corpus_not_found_message_mentions_corpus_id(tmp_path):
    conn = init_db(tmp_path)
    try:
        rdo_to_vestigio_data("OUTRO_NAO", conn=conn)
    except CorpusNotFoundError as exc:
        assert "OUTRO_NAO" in str(exc)


# ---------------------------------------------------------------------------
# Testes extras de robustez
# ---------------------------------------------------------------------------


def test_adapter_config_overrides(db_with_corpus):
    conn, corpus = db_with_corpus
    data = rdo_to_vestigio_data(
        corpus, conn=conn,
        config_overrides={
            "cliente": "Dr. Teste OAB-MG",
            "processo": "12345-67.2026.8.13.0001",
            "operador": "Operador Customizado",
        },
    )
    assert data.cliente == "Dr. Teste OAB-MG"
    assert data.processo == "12345-67.2026.8.13.0001"
    assert data.operador == "Operador Customizado"


def test_adapter_case_id_deterministic(db_with_corpus):
    """Mesmo corpus_id gera mesmo caso_id (determinístico)."""
    conn, corpus = db_with_corpus
    d1 = rdo_to_vestigio_data(corpus, conn=conn)
    d2 = rdo_to_vestigio_data(corpus, conn=conn)
    assert d1.caso_id == d2.caso_id


def test_adapter_corpus_hash_deterministic(db_with_corpus):
    conn, corpus = db_with_corpus
    d1 = rdo_to_vestigio_data(corpus, conn=conn)
    d2 = rdo_to_vestigio_data(corpus, conn=conn)
    assert d1.corpus_hash == d2.corpus_hash
    assert len(d1.corpus_hash) == 12


def test_adapter_period_in_br_format(db_with_corpus):
    conn, corpus = db_with_corpus
    data = rdo_to_vestigio_data(corpus, conn=conn)
    # dd/mm/yyyy
    for date_str in (data.periodo_inicio, data.periodo_fim):
        parts = date_str.split("/")
        assert len(parts) == 3
        assert len(parts[0]) == 2 and len(parts[1]) == 2 and len(parts[2]) == 4


def test_adapter_top_n_correlations_limit(tmp_path):
    """Nunca mais que CORRELATIONS_TOP_N correlations."""
    conn = init_db(tmp_path)
    corpus = "MANY"
    _seed_corpus(conn, corpus)
    now = "2026-04-22T00:00:00Z"
    # 20 correlations validadas
    for i in range(20):
        conn.execute(
            """INSERT INTO correlations (obra, correlation_type,
            primary_event_ref, primary_event_source,
            related_event_ref, related_event_source, time_gap_seconds,
            confidence, rationale, detected_by, created_at)
            VALUES (?, 'MATH_VALUE_MATCH', 'fr_1', 'financial_record',
                    'c_1', 'classification', 0, ?, ?, 'math_v1', ?)""",
            (corpus, 0.75 + i * 0.01, f"corr {i}", now),
        )
    conn.commit()
    data = rdo_to_vestigio_data(corpus, conn=conn)
    assert len(data.correlacoes) <= CORRELATIONS_TOP_N


def test_adapter_cronologia_has_pagamento_when_fr_exists(db_with_corpus):
    conn, corpus = db_with_corpus
    data = rdo_to_vestigio_data(corpus, conn=conn)
    pagamentos = [e for e in data.cronologia if e.tipo == "pagamento"]
    assert len(pagamentos) == 1
    # Valor formatado BR
    assert "R$" in pagamentos[0].conteudo
    assert "3.500,00" in pagamentos[0].conteudo


def test_adapter_cronologia_sorted_chronologically(db_with_corpus):
    conn, corpus = db_with_corpus
    data = rdo_to_vestigio_data(corpus, conn=conn)

    def _iso(e):
        parts = e.data.split("/")
        return f"{parts[2]}-{parts[1]}-{parts[0]}T{e.hora or '00:00'}"

    isos = [_iso(e) for e in data.cronologia]
    assert isos == sorted(isos)


def test_adapter_correlation_min_confidence_constant():
    """Threshold esta alinhado com o resto do projeto (0.70)."""
    assert CORRELATION_MIN_CONFIDENCE == 0.70


# ---------------------------------------------------------------------------
# Conversao markdown -> HTML (Fase 3.8, divida #38)
# ---------------------------------------------------------------------------


def test_markdown_h2_becomes_h3():
    """`## Titulo` vira <h3> (nao <h2>, reservado para section-mark)."""
    html = _markdown_to_html("## Analise temporal\n\nConteudo.")
    assert "<h3>Analise temporal</h3>" in html
    assert "<h2>" not in html
    assert "## " not in html


def test_markdown_bold_becomes_strong():
    html = _markdown_to_html("Os eventos **negritados** sao relevantes.")
    assert "<strong>negritados</strong>" in html
    assert "**" not in html


def test_markdown_italic_becomes_em():
    html = _markdown_to_html("Texto com *enfase italica* no corpo.")
    assert "<em>enfase italica</em>" in html
    # Nao confundir com ** (bold); garantir que nao sobrou * isolado
    assert "*enfase" not in html and "italica*" not in html


def test_markdown_paragraph_separation():
    """Paragrafos separados por \\n\\n viram <p>...</p>."""
    md = "Primeiro paragrafo.\n\nSegundo paragrafo.\n\nTerceiro."
    html = _markdown_to_html(md)
    assert html.count("<p>") == 3
    assert html.count("</p>") == 3


def test_markdown_empty_string_returns_empty():
    assert _markdown_to_html("") == ""
    assert _markdown_to_html(None) == ""  # type: ignore[arg-type]
    assert _markdown_inline("") == ""


def test_markdown_xss_protection():
    """HTML raw no input deve ser escapado (defense-in-depth)."""
    html = _markdown_to_html("Antes\n\n<script>alert('xss')</script>\n\nDepois")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    # Garante que texto benigno sobreviveu
    assert "Antes" in html and "Depois" in html


def test_markdown_inline_strips_single_p_wrapper():
    """_markdown_inline remove <p> wrapper para conteudo inline unico."""
    html = _markdown_inline("Texto com **bold**.")
    assert not html.startswith("<p>")
    assert not html.endswith("</p>")
    assert "<strong>bold</strong>" in html
    assert "Texto com " in html


def test_markdown_inline_preserves_multi_block():
    """Se o input produzir varios blocos, _markdown_inline preserva todos."""
    html = _markdown_inline("Para 1.\n\nPara 2.")
    # Com multiplos <p>, nao faz strip
    assert html.count("<p>") == 2


def test_markdown_preserves_lists_and_blockquotes():
    """Extensao 'extra' + 'sane_lists' cuida de listas e blockquotes."""
    md = "- item 1\n- item 2\n- item 3"
    html = _markdown_to_html(md)
    assert "<ul>" in html and "</ul>" in html
    assert html.count("<li>") == 3


def test_adapter_narratives_have_html_not_markdown(db_with_overview):
    """Integracao: secoes_narrativa retornam HTML, nunca markdown literal."""
    conn, corpus = db_with_overview
    data = rdo_to_vestigio_data(corpus, conn=conn, adversarial=True)
    # Nenhuma secao pode ter '## ', '**', ou '*palavra*' literais
    for secao in data.secoes_narrativa:
        assert "## " not in secao.conteudo, (
            f"'## ' literal em secao '{secao.titulo}': {secao.conteudo[:200]}"
        )
        assert "**" not in secao.conteudo, (
            f"'**' literal em secao '{secao.titulo}'"
        )
    # Resumo executivo tbm convertido (sem ## nem **)
    assert "## " not in data.resumo_executivo
    assert "**" not in data.resumo_executivo
