"""
_common.py — helpers compartilhados entre detectores Sprint 5 Fase B.

Fornece abstracoes de baixa granularidade usadas pelos tres detectores
(temporal, semantic, math):

  - `EventText`: tupla (id, timestamp_naive, text, source_type)
    representando um classification com timestamp e conteudo textual
    agregado pronto pra keyword/overlap/regex search.
  - `fetch_event_texts(conn, obra)`: retorna list[EventText] pra obra
    inteira, ja com timestamp resolvido e texto extraido por source_type.
  - `fetch_financial_timestamps(conn, obra)`: retorna list[FinancialEvent]
    com (id, timestamp_naive, valor_centavos, descricao).
  - `parse_iso_naive`: parse ISO8601 (com ou sem TZ) -> datetime naive
    (TZ e descartada, assumindo todos os eventos do piloto estao em
    America/Sao_Paulo — nao misturar fusos).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class EventText:
    """Texto agregado + timestamp de uma classification."""

    classification_id: int
    timestamp: datetime | None
    text: str
    source_type: str


@dataclass(frozen=True)
class FinancialEvent:
    """Financial_record com timestamp combinado pronto pra comparar."""

    financial_id: int
    timestamp: datetime | None
    valor_centavos: int | None
    descricao: str | None


def parse_iso_naive(ts: str | None) -> datetime | None:
    """Parse ISO8601 (qualquer TZ) em datetime naive (TZ descartada)."""
    if not ts:
        return None
    try:
        # aceita 'Z' e offsets explicitos
        normalized = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def _parse_categories(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        cats = json.loads(raw)
        return [str(c) for c in cats] if isinstance(cats, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _visual_text(analysis_json: str | None) -> str:
    if not analysis_json:
        return ""
    try:
        data = json.loads(analysis_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    parts: list[str] = []
    for key in (
        "atividade_em_curso",
        "elementos_construtivos",
        "observacoes_tecnicas",
        "materiais_visiveis",
    ):
        val = data.get(key)
        if val:
            parts.append(str(val))
    return " | ".join(parts)


_FETCH_SQL = """
    SELECT
        c.id, c.source_type,
        c.reasoning, c.human_corrected_text, c.categories,
        t.text AS transcription_text,
        f_trans.timestamp_resolved AS ts_trans,
        f_audio.timestamp_resolved AS ts_audio,
        m.timestamp_whatsapp AS ts_msg,
        m_direct.content AS text_message_content,
        m_direct.timestamp_whatsapp AS ts_text_direct,
        va.analysis_json AS visual_analysis_json,
        f_vis_src.timestamp_resolved AS ts_visual,
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
"""


def fetch_event_texts(
    conn: sqlite3.Connection, obra: str,
) -> list[EventText]:
    """
    Retorna todos os classifications `classified` da obra com:

    - timestamp naive (TZ descartada)
    - texto agregado (reasoning + corpo principal por source_type +
      categorias textualizadas)
    - source_type

    Eventos sem timestamp resolvido mantem timestamp=None (detector
    temporal ignora; semantic/math podem ainda aproveitar).
    """
    rows = [dict(r) for r in conn.execute(_FETCH_SQL, (obra,)).fetchall()]
    events: list[EventText] = []
    for r in rows:
        st = (r.get("source_type") or "transcription").lower()
        if st == "text_message":
            body = r.get("text_message_content") or ""
            ts_raw = r.get("ts_text_direct")
        elif st == "visual_analysis":
            body = _visual_text(r.get("visual_analysis_json"))
            ts_raw = r.get("ts_visual")
        elif st == "document":
            body = r.get("document_text") or ""
            ts_raw = r.get("ts_document")
        else:
            body = (
                r.get("human_corrected_text")
                or r.get("transcription_text")
                or ""
            )
            ts_raw = r.get("ts_audio") or r.get("ts_trans") or r.get("ts_msg")

        cats = _parse_categories(r.get("categories"))
        reasoning = r.get("reasoning") or ""
        # Reasoning + corpo + categorias concatenados — cobre as tres
        # superficies texturais uteis pros detectores (razao do classifier,
        # conteudo literal da fala/texto, labels aplicados).
        text = " \n ".join(filter(None, [reasoning, body, " ".join(cats)]))

        events.append(EventText(
            classification_id=int(r["id"]),
            timestamp=parse_iso_naive(ts_raw),
            text=text,
            source_type=st,
        ))
    return events


def fetch_financial_timestamps(
    conn: sqlite3.Connection, obra: str,
) -> list[FinancialEvent]:
    """
    Retorna financial_records da obra com timestamp combinado.

    Records sem data_transacao OU hora_transacao: timestamp=None
    (detector temporal skipa; math/semantic ainda podem usar).
    """
    rows = conn.execute(
        """SELECT id, data_transacao, hora_transacao,
                  valor_centavos, descricao
           FROM financial_records WHERE obra = ?""",
        (obra,),
    ).fetchall()
    out: list[FinancialEvent] = []
    for r in rows:
        data = r["data_transacao"]
        hora = r["hora_transacao"]
        ts: datetime | None
        if data and hora:
            try:
                ts = datetime.fromisoformat(f"{data}T{hora}")
            except ValueError:
                ts = None
        else:
            ts = None
        out.append(FinancialEvent(
            financial_id=int(r["id"]),
            timestamp=ts,
            valor_centavos=r["valor_centavos"],
            descricao=r["descricao"],
        ))
    return out


__all__ = [
    "EventText",
    "FinancialEvent",
    "fetch_event_texts",
    "fetch_financial_timestamps",
    "parse_iso_naive",
]
