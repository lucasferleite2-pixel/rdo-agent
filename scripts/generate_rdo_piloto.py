"""
Gerador de RDO piloto — Sprint 3 Fase 4 (Camada 4).

Agrega `classifications` de um dia especifico da obra em RDO markdown
e, opcionalmente, PDF (via weasyprint). NAO chama API.

Uso:
    python scripts/generate_rdo_piloto.py \\
        --obra EVERALDO_SANTAQUITERIA --data 2026-04-08
    python scripts/generate_rdo_piloto.py \\
        --obra EVERALDO_SANTAQUITERIA --data 2026-04-08 --output-dir reports

Exit codes:
    0 — RDO gerado (markdown e possivelmente PDF)
    1 — zero classifications para a data informada
    2 — banco nao encontrado

Dependencia opcional: weasyprint. Se ausente, gera apenas markdown.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

# Ordem canonica das secoes do RDO. ilegivel fica em "Notas forenses".
CATEGORY_HEADERS: list[tuple[str, str]] = [
    ("negociacao_comercial", "Negociações comerciais"),
    ("pagamento", "Discussões financeiras"),
    ("cronograma", "Cronograma e prazos"),
    ("especificacao_tecnica", "Especificações técnicas"),
    ("solicitacao_servico", "Solicitações de serviço"),
    ("material", "Materiais"),
    ("reporte_execucao", "Reporte de execução"),
    ("off_topic", "Eventos fora de escopo (off-topic)"),
]

# Categorias contratualmente relevantes (apagar off_topic no --modo-fiscal)
FISCAL_EXCLUDED_CATEGORIES: tuple[str, ...] = ("off_topic",)

# Tag curta por source_type para rastreabilidade no markdown
SOURCE_TAGS: dict[str, str] = {
    "transcription": "[ÁUDIO]",
    "text_message": "[TEXTO]",
    "visual_analysis": "[IMAGEM]",
    "document": "[PDF]",
}


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fetch_classified_rows(
    conn: sqlite3.Connection, obra: str, date: str,
) -> list[sqlite3.Row]:
    """
    Retorna rows classifications.classified cujo timestamp resolvido
    (via source_type) cai no dia informado. Filtragem por data feita em
    Python (timestamp path varia por source_type) — query puxa todos e
    `_resolve_display_fields` decide qual data usar.

    Retorno inclui colunas suficientes para suportar 4 source_types:
    transcription (via transcriptions+files+messages), text_message
    (via messages direto), visual_analysis (via visual_analyses +
    files derivados), document (via documents+files).
    """
    sql = """
        SELECT
            c.id AS classification_id,
            c.source_file_id,
            c.source_type,
            c.source_message_id,
            c.categories,
            c.confidence_model,
            c.reasoning AS classifier_reasoning,
            c.human_reviewed,
            c.human_corrected_text,

            t.text AS transcription_text,
            f_trans.timestamp_resolved AS ts_trans,
            f_audio.file_path AS audio_path,
            f_audio.timestamp_resolved AS ts_audio,
            m.timestamp_whatsapp AS ts_msg,

            m_direct.content AS text_message_content,
            m_direct.timestamp_whatsapp AS ts_text_direct,

            va.analysis_json AS visual_analysis_json,
            f_vis_src.file_path AS visual_source_path,
            f_vis_src.file_type AS visual_source_type,
            f_vis_src.timestamp_resolved AS ts_visual,
            f_vis_src.derived_from AS visual_source_parent,

            d.text AS document_text,
            d.page_count AS document_pages,
            f_pdf.file_path AS document_pdf_path,
            f_pdf.timestamp_resolved AS ts_document
        FROM classifications c
        LEFT JOIN transcriptions t
            ON t.obra = c.obra AND t.file_id = c.source_file_id
        LEFT JOIN files f_trans
            ON f_trans.file_id = c.source_file_id
        LEFT JOIN files f_audio
            ON f_audio.file_id = f_trans.derived_from
        LEFT JOIN messages m
            ON m.message_id = f_audio.referenced_by_message
        LEFT JOIN messages m_direct
            ON m_direct.message_id = c.source_message_id
        LEFT JOIN visual_analyses va
            ON va.obra = c.obra AND va.file_id = c.source_file_id
        LEFT JOIN files f_vis
            ON f_vis.file_id = c.source_file_id
        LEFT JOIN files f_vis_src
            ON f_vis_src.file_id = f_vis.derived_from
        LEFT JOIN documents d
            ON d.obra = c.obra AND d.file_id = c.source_file_id
        LEFT JOIN files f_doc
            ON f_doc.file_id = c.source_file_id
        LEFT JOIN files f_pdf
            ON f_pdf.file_id = f_doc.derived_from
        WHERE c.obra = ?
          AND c.semantic_status = 'classified'
        ORDER BY c.id
    """
    all_rows = list(conn.execute(sql, (obra,)).fetchall())
    # Filtro por data em Python (timestamp_path varia por source_type)
    return [
        r for r in all_rows
        if _resolve_display_fields(r)["date"] == date
    ]


def _resolve_display_fields(row: sqlite3.Row) -> dict:
    """
    Dado um row do SELECT multi-source de `_fetch_classified_rows`, deriva:
      - text: string a exibir no RDO (respeita human_corrected_text)
      - time_iso: timestamp ISO pra extrair HH:MM + ordenar
      - date: YYYY-MM-DD pra filtrar por dia
      - source_kind: 'audio' | 'texto' | 'imagem' | 'video-frame' | 'pdf'
        para decidir a tag de rastreabilidade

    Centraliza a logica que antes estava espalhada (transcription-only)
    e generaliza para text_message, visual_analysis, document.
    """
    source_type = (row["source_type"] or "transcription").lower()
    human_corrected = row["human_corrected_text"]

    if source_type == "text_message":
        text = human_corrected or row["text_message_content"] or "(mensagem vazia)"
        ts = row["ts_text_direct"]
        kind = "texto"
    elif source_type == "visual_analysis":
        # Distingue imagem original vs frame extraido de video:
        # f_vis_src.derived_from populado = veio de video (frame)
        parent = row["visual_source_parent"]
        kind = "video-frame" if parent else "imagem"
        if human_corrected:
            text = human_corrected
        else:
            analysis_json = row["visual_analysis_json"] or ""
            text = _analysis_json_to_display(analysis_json)
        ts = row["ts_visual"]
    elif source_type == "document":
        text = (
            human_corrected or row["document_text"]
            or f"(planta/doc com {row['document_pages'] or '?'} paginas, "
            f"texto digital ausente)"
        )
        ts = row["ts_document"]
        kind = "pdf"
    else:  # transcription (default)
        text = (
            human_corrected or row["transcription_text"] or "(texto ausente)"
        )
        ts = row["ts_audio"] or row["ts_trans"] or row["ts_msg"]
        kind = "audio"

    date = _extract_ymd(ts)
    return {"text": text, "time_iso": ts, "date": date, "source_kind": kind}


def _analysis_json_to_display(analysis_json_str: str) -> str:
    """
    Converte analysis_json (JSON com campos de Vision) em texto plano
    pra RDO. Concat dos 4 campos Vision se existirem, ou '(analise
    visual vazia)' pra sentinels.
    """
    try:
        analysis = json.loads(analysis_json_str)
    except (json.JSONDecodeError, TypeError):
        return "(analise visual invalida)"
    if isinstance(analysis, dict) and analysis.get("_sentinel"):
        return f"(analise visual sem conteudo: {analysis.get('reason', '?')})"
    if not isinstance(analysis, dict):
        return "(analise visual malformada)"
    parts: list[str] = []
    for key, label in (
        ("atividade_em_curso", "atividade"),
        ("elementos_construtivos", "elementos"),
        ("observacoes_tecnicas", "obs"),
    ):
        v = analysis.get(key)
        if v and str(v).strip():
            parts.append(f"{label}: {v}")
    return " | ".join(parts) if parts else "(analise visual vazia)"


def _extract_ymd(ts_iso: str | None) -> str:
    """YYYY-MM-DD do timestamp, ou '' se invalido."""
    if not ts_iso:
        return ""
    try:
        cleaned = ts_iso.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _extract_hhmm(ts_iso: str | None) -> str:
    """Extrai HH:MM do timestamp ISO. Retorna '--:--' se ausente/invalido."""
    if not ts_iso:
        return "--:--"
    try:
        # Aceita tanto "2026-04-08T09:15:00Z" quanto variantes com microsegundos
        cleaned = ts_iso.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).strftime("%H:%M")
    except ValueError:
        return "--:--"


def _parse_categories(categories_json: str) -> list[str]:
    """Parseia o JSON array de categorias; [] se invalido."""
    try:
        cats = json.loads(categories_json)
        if isinstance(cats, list):
            return [str(c) for c in cats]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _primary_category(categories_json: str) -> str:
    """Compat: retorna primeiro elemento (legacy)."""
    cats = _parse_categories(categories_json)
    return cats[0] if cats else ""


def _group_by_primary(
    rows: list[sqlite3.Row],
) -> dict[str, list[sqlite3.Row]]:
    """Compat: agrupamento single-label por primary (legacy)."""
    by: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        primary = _primary_category(r["categories"])
        by.setdefault(primary, []).append(r)
    return by


def _group_by_all_categories(
    rows: list[sqlite3.Row],
) -> dict[str, list[tuple[sqlite3.Row, bool, list[str]]]]:
    """
    Multi-label: row aparece em TODAS as secoes das suas categorias.
    Retorna dict categoria -> list[(row, is_primary, other_categories)].
    `other_categories`: categorias adicionais do row (alem da atual),
    pra renderizar "(tambem em X, Y)". `is_primary`: se a categoria
    atual eh o primary do row.
    """
    by: dict[str, list[tuple[sqlite3.Row, bool, list[str]]]] = {}
    for r in rows:
        cats = _parse_categories(r["categories"])
        if not cats:
            by.setdefault("", []).append((r, True, []))
            continue
        for idx, cat in enumerate(cats):
            others = [c for c in cats if c != cat]
            is_primary = (idx == 0)
            by.setdefault(cat, []).append((r, is_primary, others))
    return by


# ---------------------------------------------------------------------------
# Sprint 4 Op10 — integracao financial_records (ledger de comprovantes PIX)
# ---------------------------------------------------------------------------


def _fetch_financial_records_for_date(
    conn: sqlite3.Connection, obra: str, date_str: str,
) -> list[sqlite3.Row]:
    """
    Retorna comprovantes financeiros (PIX/TED/boleto etc) da obra na data.
    Ordenados por hora_transacao (asc). Formato ISO YYYY-MM-DD em date_str.

    Data coluna eh `data_transacao` extraida pelo financial_ocr (Op8).
    """
    cur = conn.execute(
        """
        SELECT data_transacao, hora_transacao, valor_centavos, doc_type,
               pagador_nome, recebedor_nome, descricao, confidence,
               source_file_id
        FROM financial_records
        WHERE obra = ? AND data_transacao = ?
        ORDER BY hora_transacao
        """,
        (obra, date_str),
    )
    return list(cur.fetchall())


def _format_brl(cents: int | None) -> str:
    """Formata centavos em R$ X.XXX,XX (padrao brasileiro)."""
    if cents is None:
        return "n/a"
    reais = abs(cents) // 100
    centavos = abs(cents) % 100
    signal = "-" if cents < 0 else ""
    reais_str = f"{reais:,}".replace(",", ".")
    return f"{signal}R$ {reais_str},{centavos:02d}"


def _truncate(s: str | None, maxlen: int = 40) -> str:
    if not s:
        return "—"
    s = s.strip()
    if len(s) <= maxlen:
        return s
    return s[: maxlen - 1] + "…"


def _render_financial_section(
    records: list[sqlite3.Row],
) -> list[str]:
    """
    Renderiza seção markdown de comprovantes financeiros (PIX/NF/boleto).
    Se lista vazia, retorna lista vazia (secao eh omitida pelo caller).
    """
    if not records:
        return []
    lines: list[str] = []
    lines.append("## 💰 Comprovantes financeiros")
    lines.append("")
    lines.append("| Hora | Valor | Tipo | De → Para | Descrição |")
    lines.append("|---|---:|:---:|---|---|")
    total_cents = 0
    for r in records:
        hora = r["hora_transacao"] or "--:--"
        # Remove segundos pra economizar espaço (HH:MM:SS -> HH:MM)
        if isinstance(hora, str) and len(hora) >= 5:
            hora = hora[:5]
        valor = _format_brl(r["valor_centavos"])
        total_cents += r["valor_centavos"] or 0
        tipo = (r["doc_type"] or "outro").upper()
        pagador = _truncate(r["pagador_nome"], 28)
        recebedor = _truncate(r["recebedor_nome"], 28)
        de_para = f"{pagador} → {recebedor}"
        desc = _truncate(r["descricao"], 50)
        lines.append(f"| {hora} | {valor} | {tipo} | {de_para} | {desc} |")
    lines.append("")
    lines.append(f"**Total do dia:** {_format_brl(total_cents)}")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Sprint 5 Fase B — integracao correlations rule-based
# ---------------------------------------------------------------------------


CORRELATION_VALIDATED_THRESHOLD = 0.70


def _fetch_correlations_for_date(
    conn: sqlite3.Connection, obra: str, date_str: str,
    classified_rows: list[sqlite3.Row],
) -> list[sqlite3.Row]:
    """
    Correlacoes do dia: primary_event eh fr do dia OU related_event eh
    cls presente em `classified_rows` (mesmo dia). Ordenadas por
    confidence desc, correlation_type asc.
    """
    fr_ids = [
        r["id"] for r in conn.execute(
            "SELECT id FROM financial_records "
            "WHERE obra = ? AND data_transacao = ?",
            (obra, date_str),
        )
    ]
    fr_refs = {f"fr_{i}" for i in fr_ids}
    cls_refs = {f"c_{r['classification_id']}" for r in classified_rows}
    all_refs = list(fr_refs | cls_refs)
    if not all_refs:
        return []
    placeholders = ",".join("?" * len(all_refs))
    sql = (
        f"SELECT * FROM correlations WHERE obra = ? AND "
        f"(primary_event_ref IN ({placeholders}) "
        f"OR related_event_ref IN ({placeholders})) "
        f"ORDER BY confidence DESC, correlation_type"
    )
    return list(conn.execute(sql, (obra, *all_refs, *all_refs)).fetchall())


def _render_correlations_section(
    correlations: list[sqlite3.Row],
) -> list[str]:
    """
    Renderiza secao markdown de correlacoes detectadas. Vazio -> [].
    Agrupa por correlation_type; marca com ✅ validadas (conf>=0.70).
    """
    if not correlations:
        return []
    lines: list[str] = [
        f"## 🔗 Correlações detectadas ({len(correlations)})",
        "",
        "Relações pairwise entre eventos (rule-based, Sprint 5 Fase B). "
        "✅ = validada (confidence ≥ 0,70).",
        "",
    ]
    grouped: dict[str, list[sqlite3.Row]] = {}
    for c in correlations:
        grouped.setdefault(c["correlation_type"], []).append(c)
    for ctype in sorted(grouped.keys()):
        items = grouped[ctype]
        n_val = sum(
            1 for c in items
            if (c["confidence"] or 0) >= CORRELATION_VALIDATED_THRESHOLD
        )
        lines.append(f"### `{ctype}` ({len(items)} total, {n_val} validadas)")
        lines.append("")
        for c in items:
            badge = (
                "✅" if (c["confidence"] or 0) >= CORRELATION_VALIDATED_THRESHOLD
                else "·"
            )
            conf = f"{c['confidence']:.2f}" if c["confidence"] is not None else "n/a"
            gap = c["time_gap_seconds"]
            gap_str = f"{gap:+d}s" if gap is not None else "n/a"
            rationale = (c["rationale"] or "").strip()
            lines.append(
                f"- {badge} `{c['primary_event_ref']} → "
                f"{c['related_event_ref']}` conf={conf} gap={gap_str} — "
                f"{rationale}"
            )
        lines.append("")
    return lines


def _format_item_line(
    row: sqlite3.Row,
    *,
    other_categories: list[str] | None = None,
    is_primary: bool = True,
) -> str:
    """
    Uma linha markdown por classification.
    Exemplo:
      - [09:15] [AUDIO] [REVISADO] file_id=`file_trans_07` — texto...
      - [09:15] [TEXTO] [NÃO REVISADO] file_id=`m_abc` — msg (tambem em pagamento)
    """
    display = _resolve_display_fields(row)
    hhmm = _extract_hhmm(display["time_iso"])
    source_tag = SOURCE_TAGS.get(
        row["source_type"] or "transcription", "[?]",
    )
    # Diferencia video-frame de imagem pura (ambos source_type=visual_analysis)
    if row["source_type"] == "visual_analysis" and display["source_kind"] == "video-frame":
        source_tag = "[VIDEO-FRAME]"
    review_tag = "[REVISADO]" if row["human_reviewed"] else "[NÃO REVISADO]"
    text_flat = " ".join((display["text"] or "").split())

    extras = ""
    if other_categories and not is_primary:
        extras = f" _(primary em `{row['categories']}`)_"
    elif other_categories and is_primary:
        extras = f" _(tambem em {', '.join(other_categories)})_"

    return (
        f"- [{hhmm}] {source_tag} {review_tag} "
        f"file_id=`{row['source_file_id']}` — {text_flat}{extras}"
    )


def render_markdown(
    obra: str, date: str, rows: list[sqlite3.Row],
    *, modo_fiscal: bool = False,
    financial_records: list[sqlite3.Row] | None = None,
    correlations: list[sqlite3.Row] | None = None,
) -> str:
    """
    Renderiza RDO em markdown.

    Sprint 4 Op5 extensoes:
      - Multi-label: evento aparece em TODAS suas categorias, com nota
        "(tambem em X, Y)" pras secundarias
      - --modo-fiscal: omite secao off_topic (eventos so-contratuais)
      - Resumo numerico por categoria no topo
      - Tags de source por evento ([AUDIO]/[TEXTO]/[IMAGEM]/[VIDEO-FRAME]/[PDF])

    Sprint 4 Op10 extensao:
      - financial_records: se fornecido e nao-vazio, seção "Comprovantes
        financeiros" eh inserida apos o resumo com ledger tabular de
        PIX/TED/boleto. Seção omitida se vazia.
    """
    by_cat = _group_by_all_categories(rows)
    total = len(rows)
    reviewed = sum(1 for r in rows if r["human_reviewed"])

    # Distribuicao por source_type para resumo
    by_source: dict[str, int] = {}
    for r in rows:
        st = r["source_type"] or "transcription"
        by_source[st] = by_source.get(st, 0) + 1

    # Contagem por categoria primary (para o resumo — uma categoria por evento)
    primary_counts: dict[str, int] = {}
    for r in rows:
        primary = _primary_category(r["categories"])
        if primary:
            primary_counts[primary] = primary_counts.get(primary, 0) + 1

    # Contagem por categoria TOTAL (inclui secundárias — reflete multi-label)
    total_counts: dict[str, int] = {}
    for r in rows:
        cats = _parse_categories(r["categories"])
        for cat in cats:
            total_counts[cat] = total_counts.get(cat, 0) + 1

    lines: list[str] = []
    lines.append(f"# RDO — EE Santa Quitéria — {date}")
    lines.append("")
    lines.append(f"**Obra:** {obra}")
    lines.append(f"**Data:** {date}")
    lines.append(f"**Gerado em:** {_now_iso_utc()}")
    if modo_fiscal:
        lines.append("**Modo:** fiscal (off-topic omitido)")
    lines.append("")
    lines.append("## Resumo do dia")
    lines.append("")
    lines.append(f"- Eventos classificados: **{total}**")
    lines.append(f"- Revisados por humano: **{reviewed}**")
    lines.append(
        f"- Não revisados (classificados direto pelo detector): "
        f"**{total - reviewed}**"
    )
    lines.append("")
    lines.append("**Por fonte:**")
    source_label = {
        "transcription": "áudios transcritos",
        "text_message": "mensagens de texto",
        "visual_analysis": "imagens/frames analisados",
        "document": "documentos extraídos",
    }
    for st, n in sorted(by_source.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {source_label.get(st, st)}: **{n}**")
    lines.append("")
    lines.append("**Por categoria (primary):**")
    ordered_primary = [
        (code, primary_counts.get(code, 0))
        for code, _ in CATEGORY_HEADERS
    ] + [("ilegivel", primary_counts.get("ilegivel", 0))]
    for code, n in ordered_primary:
        if n == 0 and (modo_fiscal and code in FISCAL_EXCLUDED_CATEGORIES):
            continue
        if n:
            lines.append(f"- `{code}`: **{n}**")
    lines.append("")

    lines.append("**Por categoria (total — primary ou secundária):**")
    ordered_total = [
        (code, total_counts.get(code, 0))
        for code, _ in CATEGORY_HEADERS
    ] + [("ilegivel", total_counts.get("ilegivel", 0))]
    for code, n in ordered_total:
        if n == 0 and (modo_fiscal and code in FISCAL_EXCLUDED_CATEGORIES):
            continue
        if n:
            delta = n - primary_counts.get(code, 0)
            suffix = f" (+{delta} secundária)" if delta > 0 else ""
            lines.append(f"- `{code}`: **{n}**{suffix}")
    lines.append("")

    # Sprint 4 Op10: ledger financeiro (logo apos resumo, antes das
    # categorias semanticas) — so aparece se houver comprovantes no dia
    if financial_records:
        lines.extend(_render_financial_section(financial_records))

    # Sprint 5 Fase B: correlacoes rule-based logo apos o ledger financeiro
    # (contextualizam os pagamentos com o que foi discutido em audio/texto).
    # Omitida se vazio.
    if correlations:
        lines.extend(_render_correlations_section(correlations))

    for code, header in CATEGORY_HEADERS:
        if modo_fiscal and code in FISCAL_EXCLUDED_CATEGORIES:
            continue
        items = by_cat.get(code, [])
        lines.append(f"## {header}")
        lines.append("")
        if not items:
            lines.append("_(nenhum evento desta categoria)_")
        else:
            # Ordena por timestamp dentro da categoria
            sorted_items = sorted(
                items,
                key=lambda t: _resolve_display_fields(t[0])["time_iso"] or "",
            )
            for row, is_primary, others in sorted_items:
                lines.append(
                    _format_item_line(
                        row, other_categories=others, is_primary=is_primary,
                    )
                )
        lines.append("")

    # ilegivel -> Notas forenses (nao afetado por modo_fiscal)
    ilegivel_items = by_cat.get("ilegivel", [])
    lines.append("## Notas forenses")
    lines.append("")
    lines.append(
        f"- Eventos marcados como ilegíveis: **{len(ilegivel_items)}**"
    )
    if ilegivel_items:
        for row, _is_primary, _others in ilegivel_items:
            lines.append(
                f"  - file_id=`{row['source_file_id']}` — "
                f"fonte degradada (source_type=`{row['source_type']}`)"
            )
    unknown = [
        k for k in by_cat
        if k not in [c for c, _ in CATEGORY_HEADERS] + ["ilegivel", ""]
    ]
    if unknown:
        lines.append(f"- ⚠ Categorias inesperadas encontradas: {unknown}")
    empty_cat = by_cat.get("", [])
    if empty_cat:
        lines.append(
            f"- ⚠ {len(empty_cat)} classifications sem category primary valido"
        )
    lines.append("")

    return "\n".join(lines)


def _markdown_to_pdf(markdown_text: str, output_pdf: Path) -> bool:
    """
    Gera PDF via weasyprint a partir de HTML minimal do markdown.

    Faz uma renderizacao HTML bem simples (nao usa biblioteca markdown —
    faz uma conversao manual de headers/bullets). Objetivo: PDF legivel,
    nao tipografia perfeita.

    Retorna True se PDF foi gerado; False se weasyprint indisponivel.
    """
    try:
        from weasyprint import HTML
    except ImportError:
        return False

    html_lines = ["<html><head><meta charset='utf-8'>"]
    html_lines.append("<style>")
    html_lines.append(
        "body{font-family:Helvetica,Arial,sans-serif;font-size:10pt;"
        "line-height:1.35;margin:1.5cm;}"
    )
    html_lines.append("h1{font-size:16pt;border-bottom:2px solid #333;}")
    html_lines.append("h2{font-size:12pt;margin-top:1em;color:#333;}")
    html_lines.append("code{background:#eee;padding:0 3px;border-radius:3px;}")
    html_lines.append("em{color:#888;}")
    html_lines.append("li{margin:0.2em 0;}")
    html_lines.append("</style></head><body>")

    in_list = False
    for raw in markdown_text.split("\n"):
        line = raw.rstrip()
        if line.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h1>{_html_escape(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{_html_escape(line[3:])}</h2>")
        elif line.startswith("- ") or line.startswith("  - "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_md_inline(line.lstrip('- '))}</li>")
        elif line.strip() == "":
            if in_list:
                html_lines.append("</ul>")
                in_list = False
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{_md_inline(line)}</p>")
    if in_list:
        html_lines.append("</ul>")
    html_lines.append("</body></html>")

    HTML(string="\n".join(html_lines)).write_pdf(str(output_pdf))
    return True


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _md_inline(s: str) -> str:
    """Converte inline markdown basico: **bold**, `code`, _italic_."""
    out = _html_escape(s)
    # **bold**
    parts: list[str] = []
    rest = out
    while "**" in rest:
        pre, _, rest2 = rest.partition("**")
        parts.append(pre)
        mid, _, rest2 = rest2.partition("**")
        parts.append(f"<strong>{mid}</strong>")
        rest = rest2
    parts.append(rest)
    out = "".join(parts)
    # `code`
    parts = []
    rest = out
    while "`" in rest:
        pre, _, rest2 = rest.partition("`")
        parts.append(pre)
        mid, _, rest2 = rest2.partition("`")
        parts.append(f"<code>{mid}</code>")
        rest = rest2
    parts.append(rest)
    out = "".join(parts)
    # _italic_  (simples, pula se tiver underscore no code)
    parts = []
    rest = out
    while "_" in rest and rest.count("_") >= 2:
        pre, _, rest2 = rest.partition("_")
        parts.append(pre)
        mid, _, rest2 = rest2.partition("_")
        parts.append(f"<em>{mid}</em>")
        rest = rest2
    parts.append(rest)
    return "".join(parts)


def generate_rdo(
    conn: sqlite3.Connection,
    *,
    obra: str,
    date: str,
    output_dir: Path,
    modo_fiscal: bool = False,
) -> dict:
    """
    Gera RDO markdown (+ PDF se weasyprint disponivel).

    Args:
        modo_fiscal: se True, omite secao off_topic (entrega fiscalizacao).

    Returns:
        dict com chaves:
          - markdown_path: Path do .md gerado
          - pdf_path: Path do .pdf ou None
          - total: numero de classifications incluidas
          - reviewed: numero human_reviewed=1 incluidas
    """
    rows = _fetch_classified_rows(conn, obra, date)
    if not rows:
        raise RuntimeError(
            f"Nenhuma classification classificada para obra={obra} data={date}"
        )
    # Op10: busca tambem comprovantes financeiros da data (pode ser vazio)
    financial_records = _fetch_financial_records_for_date(conn, obra, date)
    # Fase B: correlations do dia (pode ser vazio se correlate nao foi rodado)
    correlations = _fetch_correlations_for_date(conn, obra, date, rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_fiscal" if modo_fiscal else ""
    base = f"rdo_piloto_{obra}_{date}{suffix}"
    md_path = output_dir / f"{base}.md"
    md_text = render_markdown(
        obra, date, rows, modo_fiscal=modo_fiscal,
        financial_records=financial_records,
        correlations=correlations,
    )
    md_path.write_text(md_text, encoding="utf-8")

    pdf_path: Path | None = output_dir / f"{base}.pdf"
    assert pdf_path is not None
    try:
        pdf_ok = _markdown_to_pdf(md_text, pdf_path)
    except Exception as exc:
        # weasyprint pode falhar por causa de system deps (pango/cairo ausentes)
        print(
            f"[warn] falha ao gerar PDF ({type(exc).__name__}: {exc}); "
            f"apenas markdown foi gerado.", file=sys.stderr,
        )
        pdf_ok = False
    if not pdf_ok:
        pdf_path = None

    reviewed = sum(1 for r in rows if r["human_reviewed"])
    return {
        "markdown_path": md_path,
        "pdf_path": pdf_path,
        "total": len(rows),
        "reviewed": reviewed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera RDO piloto de um dia especifico da obra.",
    )
    parser.add_argument("--obra", required=True, help="CODESC da obra")
    parser.add_argument("--data", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--output-dir", default="reports", help="Diretorio de saida (default reports/)",
    )
    parser.add_argument(
        "--modo-fiscal", action="store_true",
        help="Omite secao off-topic (entrega para fiscalizacao)",
    )
    args = parser.parse_args()

    from rdo_agent.utils import config
    vault_path = config.get().vault_path(args.obra)
    db_path = vault_path / "index.sqlite"
    if not db_path.exists():
        print(f"[err] banco nao encontrado: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        try:
            result = generate_rdo(
                conn, obra=args.obra, date=args.data,
                output_dir=Path(args.output_dir),
                modo_fiscal=args.modo_fiscal,
            )
        except RuntimeError as exc:
            print(f"[err] {exc}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    print(f"[ok] markdown: {result['markdown_path']}")
    if result["pdf_path"]:
        print(f"[ok] pdf:      {result['pdf_path']}")
    else:
        print("[warn] PDF nao gerado (weasyprint indisponivel ou falhou).")
    print(
        f"[ok] eventos incluidos: {result['total']} "
        f"(revisados: {result['reviewed']}, nao-revisados: "
        f"{result['total'] - result['reviewed']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
