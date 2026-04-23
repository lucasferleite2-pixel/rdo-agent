"""
Dossier Builder — Sprint 5 Fase A.

Constroi dossier JSON estruturado que o agente narrador consome.
Dois escopos:

  - `build_day_dossier(conn, obra, date)`: eventos de um dia especifico,
    ordem cronologica, inclui financial_records do dia.
  - `build_obra_overview_dossier(conn, obra)`: sumario da obra inteira.
    Se >50 eventos, amostra representativa (primeiros 30 + ultimos 20),
    inclui daily_summaries por dia + TODOS financial_records.

`compute_dossier_hash(dossier)` gera SHA256 estavel do dossier (sort_keys
no json.dumps) — usado como cache key em forensic_narratives.

Formato: ver briefing Sprint 5 Fase A secao 3.3.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

# Se nao houver scope_ref (obra_overview), usamos None no JSON
CONTENT_FULL_MAX_CHARS = 500
OVERVIEW_SAMPLE_FIRST_N = 30
OVERVIEW_SAMPLE_LAST_N = 20
OVERVIEW_TOP_DENSE_DAYS = 5
"""
Dividia #28: antes `build_obra_overview_dossier` amostrava so os
primeiros 30 + ultimos 20 eventos (50 total), perdendo dias de alta
densidade narrativa (ex: 08/04 do piloto com 48 eventos — totalmente
fora da amostra). Novo criterio: ALL eventos dos top-5 dias com mais
eventos, UNIAO com primeiros-N + ultimos-N pra preservar ancora
temporal.
"""

# Correlation validation threshold (ja persistida, usamos pra filtrar
# o top_validated no obra_overview e marcar "validated" no day)
CORRELATION_VALIDATED_THRESHOLD = 0.70
CORRELATION_OVERVIEW_TOP_N = 10


def _extract_date(ts_iso: str | None) -> str:
    if not ts_iso:
        return ""
    try:
        return datetime.fromisoformat(
            ts_iso.replace("Z", "+00:00")
        ).strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return ""


def _extract_hhmm(ts_iso: str | None) -> str:
    if not ts_iso:
        return "--:--"
    try:
        return datetime.fromisoformat(
            ts_iso.replace("Z", "+00:00")
        ).strftime("%H:%M")
    except (ValueError, AttributeError):
        return "--:--"


def _parse_categories(categories_json: str | None) -> list[str]:
    if not categories_json:
        return []
    try:
        cats = json.loads(categories_json)
        if isinstance(cats, list):
            return [str(c) for c in cats]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _resolve_display_fields(row: dict) -> dict[str, Any]:
    """
    Resolve texto + timestamp de display baseado em source_type.
    Espelha logica do generate_rdo_piloto.py mas simplificado.
    """
    source_type = (row.get("source_type") or "transcription").lower()

    if source_type == "text_message":
        text = row.get("text_message_content") or ""
        ts = row.get("ts_text_direct")
    elif source_type == "visual_analysis":
        try:
            analysis = json.loads(row.get("visual_analysis_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            analysis = {}
        parts = []
        for key in ("atividade_em_curso", "elementos_construtivos",
                    "observacoes_tecnicas"):
            v = analysis.get(key)
            if v:
                parts.append(f"{key}: {v}")
        text = " | ".join(parts) or "(sem conteudo)"
        ts = row.get("ts_visual")
    elif source_type == "document":
        text = row.get("document_text") or "(sem texto extraido)"
        ts = row.get("ts_document")
    else:  # transcription
        text = (
            row.get("human_corrected_text")
            or row.get("transcription_text")
            or ""
        )
        ts = row.get("ts_audio") or row.get("ts_trans") or row.get("ts_msg")

    return {"text": text, "time_iso": ts}


def _fetch_classified_events(
    conn: sqlite3.Connection, obra: str, date_filter: str | None = None,
) -> list[dict]:
    """
    Retorna events com timestamp resolvido, source_type, categories etc.
    Se date_filter (YYYY-MM-DD), filtra por dia; senao pega todos.

    Espelha _fetch_classified_rows do generate_rdo_piloto.py mas simplificado
    para nao duplicar logica — mantem independencia.
    """
    sql = """
        SELECT
            c.id, c.source_file_id, c.source_type, c.source_message_id,
            c.categories, c.confidence_model, c.human_reviewed,
            c.human_corrected_text,
            t.text AS transcription_text,
            f_trans.timestamp_resolved AS ts_trans,
            f_audio.timestamp_resolved AS ts_audio,
            m.timestamp_whatsapp AS ts_msg,
            m_direct.content AS text_message_content,
            m_direct.timestamp_whatsapp AS ts_text_direct,
            va.analysis_json AS visual_analysis_json,
            f_vis_src.timestamp_resolved AS ts_visual,
            f_vis_src.derived_from AS visual_source_parent,
            d.text AS document_text,
            f_pdf.timestamp_resolved AS ts_document
        FROM classifications c
        LEFT JOIN transcriptions t
            ON t.obra = c.obra AND t.file_id = c.source_file_id
        LEFT JOIN files f_trans ON f_trans.file_id = c.source_file_id
        LEFT JOIN files f_audio ON f_audio.file_id = f_trans.derived_from
        LEFT JOIN messages m ON m.message_id = f_audio.referenced_by_message
        LEFT JOIN messages m_direct
            ON m_direct.message_id = c.source_message_id
        LEFT JOIN visual_analyses va
            ON va.obra = c.obra AND va.file_id = c.source_file_id
        LEFT JOIN files f_vis ON f_vis.file_id = c.source_file_id
        LEFT JOIN files f_vis_src ON f_vis_src.file_id = f_vis.derived_from
        LEFT JOIN documents d
            ON d.obra = c.obra AND d.file_id = c.source_file_id
        LEFT JOIN files f_doc ON f_doc.file_id = c.source_file_id
        LEFT JOIN files f_pdf ON f_pdf.file_id = f_doc.derived_from
        WHERE c.obra = ? AND c.semantic_status = 'classified'
        ORDER BY c.id
    """
    rows = [dict(r) for r in conn.execute(sql, (obra,)).fetchall()]
    out: list[dict] = []
    for r in rows:
        d = _resolve_display_fields(r)
        time_iso = d["time_iso"]
        event_date = _extract_date(time_iso)
        if date_filter and event_date != date_filter:
            continue
        cats = _parse_categories(r.get("categories"))
        primary = cats[0] if cats else ""
        secondary = cats[1:] if len(cats) > 1 else []
        content_full = d["text"] or ""
        out.append({
            "id": f"c_{r['id']}",
            "timestamp": time_iso,
            "event_date": event_date,
            "hora_brasilia": _extract_hhmm(time_iso),
            "source_type": r.get("source_type") or "transcription",
            "primary_category": primary,
            "secondary_categories": secondary,
            "content_preview": content_full[:150] if content_full else "",
            "content_full": content_full if len(content_full) <= CONTENT_FULL_MAX_CHARS else None,
            "confidence": r.get("confidence_model"),
            "human_reviewed": bool(r.get("human_reviewed")),
            "file_id": r.get("source_file_id"),
        })
    # Sort cronologico
    out.sort(key=lambda e: e["timestamp"] or "")
    return out


def _fetch_financial_records(
    conn: sqlite3.Connection, obra: str, date_filter: str | None = None,
) -> list[dict]:
    sql = """
        SELECT data_transacao, hora_transacao, valor_centavos, doc_type,
               pagador_nome, recebedor_nome, descricao, confidence,
               source_file_id
        FROM financial_records
        WHERE obra = ?
    """
    params: list[Any] = [obra]
    if date_filter:
        sql += " AND data_transacao = ?"
        params.append(date_filter)
    sql += " ORDER BY data_transacao, hora_transacao"
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "data": r["data_transacao"],
            "hora": (r["hora_transacao"] or "")[:5] if r["hora_transacao"] else "--:--",
            "valor_brl": round((r["valor_centavos"] or 0) / 100, 2),
            "valor_centavos": r["valor_centavos"],
            "doc_type": r["doc_type"],
            "pagador": r["pagador_nome"],
            "recebedor": r["recebedor_nome"],
            "descricao": r["descricao"],
            "confidence": r["confidence"],
            "source_file_id": r["source_file_id"],
        }
        for r in rows
    ]


def _correlation_row_to_dict(r: dict | sqlite3.Row) -> dict[str, Any]:
    return {
        "correlation_type": r["correlation_type"],
        "primary_event_ref": r["primary_event_ref"],
        "primary_event_source": r["primary_event_source"],
        "related_event_ref": r["related_event_ref"],
        "related_event_source": r["related_event_source"],
        "time_gap_seconds": r["time_gap_seconds"],
        "confidence": r["confidence"],
        "rationale": r["rationale"],
        "detected_by": r["detected_by"],
        "validated": (r["confidence"] or 0) >= CORRELATION_VALIDATED_THRESHOLD,
    }


def _fetch_correlations_for_day(
    conn: sqlite3.Connection, obra: str, date: str,
    day_events: list[dict],
) -> list[dict]:
    """
    Correlacoes onde primary OU related referencia:
      - um financial_record cujo data_transacao == date, OU
      - uma classification presente em `day_events` (mesmo dia)

    Ordenadas por confidence desc.
    """
    fr_refs = {
        f"fr_{r['id']}" for r in conn.execute(
            "SELECT id FROM financial_records "
            "WHERE obra = ? AND data_transacao = ?",
            (obra, date),
        )
    }
    cls_refs = {e["id"] for e in day_events}  # formato 'c_<id>'
    all_refs = list(fr_refs | cls_refs)
    if not all_refs:
        return []
    placeholders = ",".join("?" * len(all_refs))
    sql = (
        f"SELECT * FROM correlations WHERE obra = ? AND "
        f"(primary_event_ref IN ({placeholders}) "
        f"OR related_event_ref IN ({placeholders})) "
        f"ORDER BY confidence DESC"
    )
    rows = conn.execute(sql, (obra, *all_refs, *all_refs)).fetchall()
    return [_correlation_row_to_dict(dict(r)) for r in rows]


def _fetch_correlations_summary(
    conn: sqlite3.Connection, obra: str,
) -> dict[str, Any]:
    """Resumo da obra: total, breakdown por tipo, top validadas."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM correlations WHERE obra = ? "
        "ORDER BY confidence DESC",
        (obra,),
    ).fetchall()]
    by_type: dict[str, int] = {}
    for r in rows:
        t = r["correlation_type"]
        by_type[t] = by_type.get(t, 0) + 1
    validated = [
        _correlation_row_to_dict(r) for r in rows
        if (r["confidence"] or 0) >= CORRELATION_VALIDATED_THRESHOLD
    ]
    return {
        "total": len(rows),
        "by_type": by_type,
        "validated_count": len(validated),
        "top_validated": validated[:CORRELATION_OVERVIEW_TOP_N],
    }


def _compute_statistics(events: list[dict]) -> dict[str, Any]:
    by_source: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for e in events:
        st = e["source_type"]
        by_source[st] = by_source.get(st, 0) + 1
        cat = e["primary_category"] or "(sem_categoria)"
        by_category[cat] = by_category.get(cat, 0) + 1
    return {
        "events_total": len(events),
        "by_source_type": by_source,
        "by_primary_category": by_category,
    }


def _compute_context_hints(
    events: list[dict], financial_records: list[dict],
) -> dict[str, bool]:
    """Heuristicas simples pra hints do agente narrador."""
    contract_keywords = ("sinal", "contrato", "50%", "metade", "fechar")
    renegotiation_keywords = (
        "renegociar", "renegociacao", "renegociação",
        "mudanca", "mudança", "reajuste",
    )

    day_has_payment = bool(financial_records)

    day_has_contract = False
    for r in financial_records:
        desc = (r.get("descricao") or "").lower()
        if any(k in desc for k in contract_keywords):
            day_has_contract = True
            break

    day_mentions_renegotiation = False
    if len(events) > 10:
        for e in events:
            content = (e.get("content_full") or e.get("content_preview") or "").lower()
            if any(k in content for k in renegotiation_keywords):
                day_mentions_renegotiation = True
                break

    return {
        "day_has_payment": day_has_payment,
        "day_has_contract_establishment": day_has_contract,
        "day_mentions_renegotiation": day_mentions_renegotiation,
    }


def build_day_dossier(
    conn: sqlite3.Connection, obra: str, date: str,
) -> dict[str, Any]:
    """
    Monta dossier JSON do dia `date` (YYYY-MM-DD) da `obra`.

    Estrutura: ver secao 3.3 do briefing. Events timeline ordenada
    cronologicamente; financial_records do dia com valor em R$.
    """
    events = _fetch_classified_events(conn, obra, date_filter=date)
    financial_records = _fetch_financial_records(conn, obra, date_filter=date)
    stats = _compute_statistics(events)
    hints = _compute_context_hints(events, financial_records)
    correlations = _fetch_correlations_for_day(conn, obra, date, events)

    if events:
        first = events[0]["timestamp"]
        last = events[-1]["timestamp"]
    else:
        first = last = None

    return {
        "obra": obra,
        "scope": "day",
        "scope_ref": date,
        "date_range": {"first_event": first, "last_event": last},
        "statistics": stats,
        "financial_records": financial_records,
        "events_timeline": events,
        "context_hints": hints,
        "correlations": correlations,
    }


def build_obra_overview_dossier(
    conn: sqlite3.Connection, obra: str,
) -> dict[str, Any]:
    """
    Monta dossier JSON da obra inteira.

    Diferencas de day_dossier:
      - events_timeline: se >50 eventos, amostra representativa
        (primeiros OVERVIEW_SAMPLE_FIRST_N + ultimos OVERVIEW_SAMPLE_LAST_N)
      - daily_summaries: {data, events_count, main_topics}
      - financial_records: TODOS comprovantes da obra
    """
    all_events = _fetch_classified_events(conn, obra)
    financial_records = _fetch_financial_records(conn, obra)
    stats = _compute_statistics(all_events)
    hints = _compute_context_hints(all_events, financial_records)

    # Daily summaries (construido antes do sample pra usar nos top-N)
    by_date: dict[str, list[dict]] = {}
    for e in all_events:
        d = e.get("event_date") or "(sem_data)"
        by_date.setdefault(d, []).append(e)

    # Sample — divida #28 fixed:
    # Se corpus eh pequeno, usa tudo
    # Se grande, UNIAO de:
    #   - ALL eventos dos top-5 dias com mais eventos (densidade narrativa)
    #   - primeiros-30 eventos (ancora temporal inicial)
    #   - ultimos-20 eventos (ancora temporal final)
    # Deduplicado por event.id, reordenado cronologicamente.
    if len(all_events) <= (OVERVIEW_SAMPLE_FIRST_N + OVERVIEW_SAMPLE_LAST_N):
        sampled = all_events
    else:
        top_dense_dates = [
            d for d, _ in sorted(
                by_date.items(), key=lambda kv: -len(kv[1]),
            )[:OVERVIEW_TOP_DENSE_DAYS]
        ]
        dense_events: list[dict] = []
        for d in top_dense_dates:
            dense_events.extend(by_date[d])

        combined: list[dict] = (
            all_events[:OVERVIEW_SAMPLE_FIRST_N]
            + dense_events
            + all_events[-OVERVIEW_SAMPLE_LAST_N:]
        )
        # Dedup preservando ordem cronologica do all_events
        seen_ids: set[str] = set()
        unique: list[dict] = []
        for e in combined:
            eid = e.get("id") or ""
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            unique.append(e)
        # Reordena cronologicamente
        unique.sort(key=lambda e: e.get("timestamp") or "")
        sampled = unique

    daily_summaries = []
    for d, evts in sorted(by_date.items()):
        topic_counts: dict[str, int] = {}
        for e in evts:
            cat = e["primary_category"] or "(sem_categoria)"
            topic_counts[cat] = topic_counts.get(cat, 0) + 1
        # top 3 topics
        main_topics = sorted(
            topic_counts.items(), key=lambda kv: -kv[1],
        )[:3]
        daily_summaries.append({
            "data": d,
            "events_count": len(evts),
            "main_topics": [t[0] for t in main_topics],
        })

    if all_events:
        first = all_events[0]["timestamp"]
        last = all_events[-1]["timestamp"]
    else:
        first = last = None

    correlations_summary = _fetch_correlations_summary(conn, obra)

    return {
        "obra": obra,
        "scope": "obra_overview",
        "scope_ref": None,
        "date_range": {"first_event": first, "last_event": last},
        "statistics": stats,
        "financial_records": financial_records,
        "events_timeline": sampled,
        "events_total_in_obra": len(all_events),
        "events_sampled": len(sampled),
        "daily_summaries": daily_summaries,
        "context_hints": hints,
        "correlations_summary": correlations_summary,
    }


def compute_dossier_hash(dossier: dict) -> str:
    """
    SHA256 do dossier serializado com sort_keys + ensure_ascii=False.
    Determinismo: mesmo dossier gera mesmo hash. Usado como cache key
    em forensic_narratives (UNIQUE).
    """
    serialized = json.dumps(
        dossier, sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = [
    "CONTENT_FULL_MAX_CHARS",
    "OVERVIEW_SAMPLE_FIRST_N",
    "OVERVIEW_SAMPLE_LAST_N",
    "build_day_dossier",
    "build_obra_overview_dossier",
    "compute_dossier_hash",
]
