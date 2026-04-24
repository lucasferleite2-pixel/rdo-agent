"""
adapter.py — Converte estado rdo-agent em LaudoData do Vestígio.

Funcao principal: `rdo_to_vestigio_data(corpus_id, ...)`.

Por convencao historica do projeto (mantida ate v2.0 conforme
PROJECT_CONTEXT seccao 2), a coluna do DB chama-se `obra`, mas
semanticamente equivale a um *canal* / *corpus*. A API publica do
adapter usa `corpus_id` pra alinhar com a nomenclatura Vestigio; o
mapping pra SQL eh 1:1 (corpus_id -> obra).

Zero chamadas a API externa. Puro SQL + transformacao de dados.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from rdo_agent.laudo.vestigio_laudo import (
    Correlacao,
    EventoCronologia,
    LaudoData,
    SecaoNarrativa,
)
from rdo_agent.utils import config


# =============================================================================
# Constantes e defaults
# =============================================================================

DEFAULT_OPERATOR = "Lucas Fernandes Leite"
DEFAULT_OBJETO = (
    "Análise forense de canal digital de comunicação em obra, com foco "
    "em reconstrução cronológica, detecção de correlações entre eventos "
    "e verificação de coerência contratual."
)

# Confidence threshold pra correlacao "validada" (alinha com o resto do projeto)
CORRELATION_MIN_CONFIDENCE = 0.70
# Numero maximo de correlacoes no laudo
CORRELATIONS_TOP_N = 10
# Numero maximo de eventos na cronologia (capacidade do layout)
CRONOLOGIA_MAX = 20

# Mapeia prefixo do correlation_type do rdo-agent pro tipo do laudo
_CORRELATION_TYPE_MAP = {
    "TEMPORAL": "TEMPORAL",
    "SEMANTIC": "SEMANTIC",
    "MATH": "MATH",
}


class CorpusNotFoundError(RuntimeError):
    """Corpus/canal nao encontrado no vault."""


# =============================================================================
# API publica
# =============================================================================


def rdo_to_vestigio_data(
    corpus_id: str,
    *,
    adversarial: bool = False,
    include_ground_truth: bool = False,
    config_overrides: dict | None = None,
    conn: sqlite3.Connection | None = None,
) -> LaudoData:
    """
    Converte estado completo de um corpus rdo-agent em LaudoData.

    Args:
        corpus_id: Nome do canal (ex: 'EVERALDO_SANTAQUITERIA'). Mapeia
            pra coluna `obra` no SQLite.
        adversarial: Se True, prioriza narrativas v4_adversarial.
        include_ground_truth: Se True, marca no laudo que GT foi usado.
        config_overrides: Dict opcional com {cliente, processo, objeto,
            operador} pra sobrescrever defaults.
        conn: Conexao SQLite ja aberta (pra testes). Se None, abre vault
            default baseado em `config.get().vault_path(corpus_id)`.

    Returns:
        LaudoData preenchido com dados reais.

    Raises:
        CorpusNotFoundError: se corpus nao tem dados no vault.
    """
    overrides = config_overrides or {}

    # 1) Conexao SQLite
    owns_conn = False
    if conn is None:
        vault = config.get().vault_path(corpus_id)
        db_path = vault / "index.sqlite"
        if not db_path.exists():
            raise CorpusNotFoundError(
                f"Vault nao encontrada: {db_path}. Corpus '{corpus_id}' "
                "precisa ter sido ingerido antes de exportar laudo."
            )
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        owns_conn = True

    try:
        return _build_laudo_data(
            conn, corpus_id,
            adversarial=adversarial,
            include_ground_truth=include_ground_truth,
            overrides=overrides,
        )
    finally:
        if owns_conn:
            conn.close()


# =============================================================================
# Implementacao
# =============================================================================


def _build_laudo_data(
    conn: sqlite3.Connection, corpus_id: str,
    *,
    adversarial: bool,
    include_ground_truth: bool,
    overrides: dict,
) -> LaudoData:
    # 1) Contadores basicos
    total_messages = _count_messages(conn, corpus_id)
    if total_messages == 0:
        raise CorpusNotFoundError(
            f"Corpus '{corpus_id}' nao possui mensagens no DB. "
            "Laudo nao pode ser gerado."
        )

    total_documents = _count_files_by_type(conn, corpus_id, "document")
    total_audios = _count_files_by_type(conn, corpus_id, "audio")
    total_correlations_validated = _count_validated_correlations(
        conn, corpus_id,
    )

    # 2) Periodo (min/max timestamps de messages)
    periodo_inicio, periodo_fim = _compute_period(conn, corpus_id)

    # 3) Hash do corpus (determinístico a partir dos message_ids)
    corpus_hash = _compute_corpus_hash(conn, corpus_id)

    # 4) Caso ID (VST-YYYY-HASH4)
    caso_id = _generate_case_id(corpus_id)

    # 5) Narrativas -> secoes
    secoes, resumo_exec = _extract_narratives(
        conn, corpus_id, adversarial=adversarial,
    )

    # 6) Cronologia (financial_records + top classifications)
    cronologia = _build_cronologia(conn, corpus_id)

    # 7) Correlacoes validadas
    correlacoes = _extract_correlations(conn, corpus_id)

    # 8) Defaults + overrides
    cliente = overrides.get("cliente", "")
    processo = overrides.get("processo", "")
    objeto = overrides.get("objeto", DEFAULT_OBJETO)
    operador = overrides.get("operador", DEFAULT_OPERATOR)
    titulo = overrides.get(
        "titulo",
        f"Análise Forense · {corpus_id}",
    )

    return LaudoData(
        caso_id=caso_id,
        titulo=titulo,
        periodo_inicio=periodo_inicio,
        periodo_fim=periodo_fim,
        operador=operador,
        corpus_hash=corpus_hash,
        total_mensagens=total_messages,
        total_documentos=total_documents,
        total_audios=total_audios,
        total_correlacoes=total_correlations_validated,
        cliente=cliente,
        processo=processo,
        objeto=objeto,
        resumo_executivo=resumo_exec,
        secoes_narrativa=secoes,
        cronologia=cronologia,
        correlacoes=correlacoes,
        versao_laudo="1.0",
        incluir_ground_truth=include_ground_truth,
    )


# =============================================================================
# Queries auxiliares
# =============================================================================


def _count_messages(conn: sqlite3.Connection, corpus_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE obra = ?",
        (corpus_id,),
    ).fetchone()
    return int(row["n"]) if row else 0


def _count_files_by_type(
    conn: sqlite3.Connection, corpus_id: str, file_type: str,
) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM files WHERE obra = ? AND file_type = ?",
        (corpus_id, file_type),
    ).fetchone()
    return int(row["n"]) if row else 0


def _count_validated_correlations(
    conn: sqlite3.Connection, corpus_id: str,
) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM correlations "
        "WHERE obra = ? AND confidence >= ?",
        (corpus_id, CORRELATION_MIN_CONFIDENCE),
    ).fetchone()
    return int(row["n"]) if row else 0


def _compute_period(
    conn: sqlite3.Connection, corpus_id: str,
) -> tuple[str, str]:
    """Retorna (dd/mm/yyyy inicio, dd/mm/yyyy fim) dos messages."""
    row = conn.execute(
        "SELECT MIN(timestamp_whatsapp) AS lo, MAX(timestamp_whatsapp) AS hi "
        "FROM messages WHERE obra = ?",
        (corpus_id,),
    ).fetchone()
    if not row or not row["lo"]:
        # Fallback: hoje (corpus vazio deveria ter levantado antes)
        today = datetime.now().strftime("%d/%m/%Y")
        return today, today
    return _iso_to_br(row["lo"]), _iso_to_br(row["hi"])


def _iso_to_br(ts_iso: str | None) -> str:
    """Converte 'YYYY-MM-DDTHH:MM[:SS]' em 'dd/mm/yyyy'."""
    if not ts_iso:
        return ""
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        # Tenta parse simplificado
        date_part = ts_iso.split("T")[0]
        try:
            dt = datetime.strptime(date_part, "%Y-%m-%d")
        except ValueError:
            return ts_iso
    return dt.strftime("%d/%m/%Y")


def _compute_corpus_hash(
    conn: sqlite3.Connection, corpus_id: str,
) -> str:
    """sha256 dos message_ids concatenados (determinístico e auditavel)."""
    rows = conn.execute(
        "SELECT message_id FROM messages WHERE obra = ? "
        "ORDER BY message_id",
        (corpus_id,),
    ).fetchall()
    if not rows:
        # Fallback: hash do corpus_id
        return hashlib.sha256(corpus_id.encode()).hexdigest()[:12]
    concat = "".join(r["message_id"] for r in rows)
    return hashlib.sha256(concat.encode()).hexdigest()[:12]


def _generate_case_id(corpus_id: str) -> str:
    """VST-YYYY-HASH4 onde HASH4 = sha256(corpus_id)[:4].upper()."""
    year = datetime.now().year
    h = hashlib.sha256(corpus_id.encode()).hexdigest()[:4].upper()
    return f"VST-{year}-{h}"


# =============================================================================
# Narrativas -> SecaoNarrativa
# =============================================================================


# Scopes possiveis em forensic_narratives.scope
_SCOPE_DAY = "day"
_SCOPE_OVERVIEW = "obra_overview"


def _extract_narratives(
    conn: sqlite3.Connection, corpus_id: str, *, adversarial: bool,
) -> tuple[list[SecaoNarrativa], str]:
    """
    Pega narrativas do DB. Seleciona a mais recente por (scope, scope_ref)
    com preferencia pela prompt_version mais avancada (v4 > v3 > v2 > v1).

    Retorna (secoes, resumo_executivo).
    """
    # Prioridade de prompt_version. Quando adversarial=True, prioriza v4;
    # quando False, prioriza v3_gt / v2 / v1 nessa ordem.
    priority = _prompt_version_priority(adversarial)

    rows = conn.execute(
        "SELECT id, scope, scope_ref, narrative_text, prompt_version, "
        "created_at FROM forensic_narratives "
        "WHERE obra = ? ORDER BY scope, scope_ref, id",
        (corpus_id,),
    ).fetchall()

    # Agrupa por (scope, scope_ref); escolhe a melhor via priority
    best_per_key: dict[tuple[str, str | None], sqlite3.Row] = {}
    for r in rows:
        key = (r["scope"], r["scope_ref"])
        current = best_per_key.get(key)
        if current is None or _narrative_score(r, priority) > _narrative_score(
            current, priority,
        ):
            best_per_key[key] = r

    # Extrai resumo executivo do overview (primeiro paragrafo apos "Sumario
    # Executivo" ou os primeiros 400 chars do corpo)
    overview = best_per_key.get((_SCOPE_OVERVIEW, None))
    resumo_exec = ""
    if overview is not None:
        resumo_exec = _extract_resumo_from_overview(overview["narrative_text"])

    # Ordena day narratives por scope_ref (data)
    day_rows = sorted(
        [r for (s, _), r in best_per_key.items() if s == _SCOPE_DAY],
        key=lambda r: r["scope_ref"] or "",
    )

    secoes: list[SecaoNarrativa] = []
    # Overview vira 1 secao (se houver) — no inicio
    if overview is not None:
        body = _strip_narrative_boilerplate(overview["narrative_text"])
        secoes.append(SecaoNarrativa(
            titulo="Visão geral do canal",
            conteudo=body,
        ))

    # Cada dia vira uma secao
    for r in day_rows:
        ref = r["scope_ref"] or "sem data"
        br_date = _iso_to_br(ref + "T00:00:00")
        body = _strip_narrative_boilerplate(r["narrative_text"])
        secoes.append(SecaoNarrativa(
            titulo=f"Dia {br_date}",
            conteudo=body,
        ))

    return secoes, resumo_exec


def _prompt_version_priority(adversarial: bool) -> dict[str, int]:
    """
    Score maior = preferida. Em adversarial=True, v4 eh topo; senao
    v3_gt (cita GT) eh o topo. Abaixo: v2 > v1 > desconhecida.
    """
    if adversarial:
        return {
            "narrator_v4_adversarial": 100,
            "narrator_v3_1_anchoring": 80,
            "narrator_v3_gt": 70,
            "narrator_v2_1_anchoring": 60,
            "narrator_v2_correlations": 50,
            "narrator_v1": 40,
        }
    return {
        "narrator_v3_1_anchoring": 100,
        "narrator_v3_gt": 90,
        "narrator_v4_adversarial": 85,
        "narrator_v2_1_anchoring": 70,
        "narrator_v2_correlations": 60,
        "narrator_v1": 50,
    }


def _narrative_score(row: sqlite3.Row, priority: dict[str, int]) -> int:
    """Score composto: prioridade do prompt_version + id (recencia)."""
    pv = row["prompt_version"] or ""
    base = priority.get(pv, 0)
    # Desempate: id mais alto = mais recente
    return base * 100000 + int(row["id"])


def _extract_resumo_from_overview(text: str) -> str:
    """
    Extrai o primeiro paragrafo apos secao 'Sumario Executivo' (ou
    primeiras 400-600 chars do body se estrutura diferir).
    """
    # Procura 'Sumario Executivo' / 'Sumário Executivo' como heading
    pattern = re.compile(
        r"##\s*Sum[áa]rio\s+Executivo\s*\n+([^#]{80,1500}?)(?:\n\n|\n##|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(text)
    if m:
        para = m.group(1).strip()
        # Limita a ~800 chars pra caber no card (template tem scroll)
        if len(para) > 800:
            para = para[:797].rsplit(" ", 1)[0] + "…"
        return para
    # Fallback: primeiro paragrafo substancial apos o titulo
    body = _strip_narrative_boilerplate(text)
    paragraphs = [p.strip() for p in body.split("\n\n") if len(p.strip()) > 80]
    if paragraphs:
        para = paragraphs[0]
        if len(para) > 800:
            para = para[:797].rsplit(" ", 1)[0] + "…"
        return para
    return body[:800]


def _strip_narrative_boilerplate(text: str) -> str:
    """
    Remove header '# Narrativa: ...' e bloco final ```json...```
    (self_assessment). Preserva o resto como markdown.
    """
    if not text:
        return ""
    # Remove header principal
    text = re.sub(r"^#\s+Narrativa:[^\n]*\n+", "", text, count=1)
    # Remove bloco de self_assessment no fim
    text = re.sub(r"\n```json\s*\{[\s\S]*?\}\s*```\s*$", "", text.rstrip())
    # Remove separador trailing '---'
    text = re.sub(r"\n---\s*$", "", text.rstrip())
    return text.strip()


# =============================================================================
# Cronologia
# =============================================================================


def _build_cronologia(
    conn: sqlite3.Connection, corpus_id: str,
) -> list[EventoCronologia]:
    """
    Constroi cronologia top-CRONOLOGIA_MAX eventos misturando:
      - financial_records -> tipo='pagamento' (todos)
      - classifications classified com conteudo relevante -> 'mensagem' ou
        'decisao' (categoria-dependente). Prioriza human_reviewed ou
        confidence_model alto.

    Fallback da divida #35 (events table esta vazia): dados saem de
    financial_records + classifications + messages.
    """
    events: list[EventoCronologia] = []

    # 1) Pagamentos
    fr_rows = conn.execute(
        "SELECT data_transacao, hora_transacao, valor_centavos, "
        "pagador_nome, recebedor_nome, descricao "
        "FROM financial_records WHERE obra = ? "
        "ORDER BY data_transacao, hora_transacao",
        (corpus_id,),
    ).fetchall()
    for r in fr_rows:
        valor_brl = (r["valor_centavos"] or 0) / 100
        # Formata valor no padrao BR
        valor_fmt = f"R$ {valor_brl:,.2f}".replace(",", "X").replace(
            ".", ",",
        ).replace("X", ".")
        autor = r["pagador_nome"] or "Pagador"
        desc = r["descricao"] or ""
        conteudo = f"{valor_fmt} → {r['recebedor_nome'] or 'Recebedor'}"
        if desc:
            conteudo += f". {desc}"
        br_date = _iso_to_br(r["data_transacao"] + "T00:00:00") \
            if r["data_transacao"] else ""
        hora = (r["hora_transacao"] or "")[:5] if r["hora_transacao"] else None
        events.append(EventoCronologia(
            data=br_date,
            hora=hora,
            autor=autor,
            conteudo=conteudo,
            tipo="pagamento",
            tags=["pix"] if valor_brl > 0 else [],
        ))

    # 2) Classifications relevantes
    cls_rows = conn.execute(
        """
        SELECT
            c.id,
            c.source_type,
            c.source_file_id,
            c.categories,
            c.reasoning,
            c.human_reviewed,
            c.confidence_model,
            c.human_corrected_text,
            t.text AS transcription_text,
            f_audio.timestamp_resolved AS ts_audio,
            f_trans.timestamp_resolved AS ts_trans,
            m.timestamp_whatsapp AS ts_msg,
            m_direct.content AS text_message_content,
            m_direct.timestamp_whatsapp AS ts_text_direct,
            m_direct.sender AS text_message_sender
        FROM classifications c
        LEFT JOIN transcriptions t
            ON t.obra = c.obra AND t.file_id = c.source_file_id
        LEFT JOIN files f_trans ON f_trans.file_id = c.source_file_id
        LEFT JOIN files f_audio ON f_audio.file_id = f_trans.derived_from
        LEFT JOIN messages m
            ON m.message_id = f_audio.referenced_by_message
        LEFT JOIN messages m_direct
            ON m_direct.message_id = c.source_message_id
        WHERE c.obra = ? AND c.semantic_status = 'classified'
        """,
        (corpus_id,),
    ).fetchall()
    # Ordena: human_reviewed desc, confidence_model desc
    sorted_cls = sorted(
        cls_rows,
        key=lambda r: (
            -int(r["human_reviewed"] or 0),
            -float(r["confidence_model"] or 0),
        ),
    )
    # Categoria -> tipo de cronologia
    cat_to_tipo = {
        "pagamento": "pagamento",  # subsumido pelos financial_records
        "contrato": "decisao",
        "cronograma": "decisao",
        "material": "mensagem",
        "servico": "mensagem",
    }
    # Reserva budget = CRONOLOGIA_MAX - len(events)
    remaining = CRONOLOGIA_MAX - len(events)
    for r in sorted_cls:
        if remaining <= 0:
            break
        cats = _parse_json_list(r["categories"])
        primary = cats[0] if cats else ""
        tipo = cat_to_tipo.get(primary, "mensagem")
        # Remove pagamentos duplicados (ja temos de financial_records)
        if tipo == "pagamento":
            continue
        text = (
            r["human_corrected_text"]
            or r["transcription_text"]
            or r["text_message_content"]
            or (r["reasoning"] or "")
        )
        if not text or len(text.strip()) < 20:
            continue
        text = text.strip()
        if len(text) > 300:
            text = text[:297].rsplit(" ", 1)[0] + "…"

        ts_iso = (
            r["ts_text_direct"] or r["ts_audio"]
            or r["ts_trans"] or r["ts_msg"]
        )
        br_date, hora = _parse_iso_to_br_parts(ts_iso)
        if not br_date:
            continue
        autor = (
            r["text_message_sender"]
            or _infer_author(r["source_type"])
        )
        tags = cats[:2] if cats else []
        events.append(EventoCronologia(
            data=br_date,
            hora=hora,
            autor=autor or "",
            conteudo=text,
            tipo=tipo,
            tags=tags,
        ))
        remaining -= 1

    # 3) Ordena cronologicamente: (data, hora) asc
    def _sort_key(e: EventoCronologia) -> tuple:
        return (
            _br_date_to_iso(e.data),
            e.hora or "00:00",
        )

    events.sort(key=_sort_key)
    return events[:CRONOLOGIA_MAX]


def _parse_iso_to_br_parts(ts_iso: str | None) -> tuple[str, str | None]:
    """'2026-04-06T11:13:00-03:00' -> ('06/04/2026', '11:13')."""
    if not ts_iso:
        return "", None
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        try:
            dt = datetime.strptime(ts_iso[:10], "%Y-%m-%d")
        except ValueError:
            return "", None
    return dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M")


def _br_date_to_iso(br_date: str) -> str:
    """'06/04/2026' -> '2026-04-06' pra sort. Vazio -> vazio."""
    if not br_date or "/" not in br_date:
        return br_date
    parts = br_date.split("/")
    if len(parts) != 3:
        return br_date
    d, m, y = parts
    return f"{y}-{m.zfill(2)}-{d.zfill(2)}"


def _infer_author(source_type: str | None) -> str:
    mapping = {
        "transcription": "Áudio transcrito",
        "text_message": "Mensagem WhatsApp",
        "visual_analysis": "Imagem analisada",
        "document": "Documento",
    }
    return mapping.get((source_type or "").lower(), "Participante do canal")


def _parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


# =============================================================================
# Correlacoes
# =============================================================================


def _extract_correlations(
    conn: sqlite3.Connection, corpus_id: str,
) -> list[Correlacao]:
    """
    Top CORRELATIONS_TOP_N correlacoes com confidence >= threshold.
    Excertos extraidos do rationale; fallback: file_ids dos eventos.
    """
    rows = conn.execute(
        "SELECT correlation_type, primary_event_ref, primary_event_source, "
        "related_event_ref, related_event_source, time_gap_seconds, "
        "confidence, rationale, detected_by "
        "FROM correlations WHERE obra = ? AND confidence >= ? "
        "ORDER BY confidence DESC, id DESC "
        "LIMIT ?",
        (corpus_id, CORRELATION_MIN_CONFIDENCE, CORRELATIONS_TOP_N),
    ).fetchall()

    out: list[Correlacao] = []
    for r in rows:
        ctype = _map_correlation_type(r["correlation_type"])
        descricao = r["rationale"] or ""
        # Excertos: como nao temos os textos dos eventos aqui, usamos refs
        # + explicacao do rationale como fallback. Ex:
        #   excerto_a: 'Evento fr_1 (financial_record): valor mencionado ...'
        excerto_a = _build_excerpt(
            r["primary_event_ref"], r["primary_event_source"], conn, corpus_id,
        )
        excerto_b = _build_excerpt(
            r["related_event_ref"], r["related_event_source"], conn, corpus_id,
        )
        conf = float(r["confidence"] or 0)
        out.append(Correlacao(
            tipo=ctype,
            descricao=descricao,
            excerto_a=excerto_a,
            excerto_b=excerto_b,
            confianca=conf,
        ))
    return out


def _map_correlation_type(corr_type: str | None) -> str:
    """Prefixo -> tipo display (TEMPORAL / SEMANTIC / MATH)."""
    if not corr_type:
        return "SEMANTIC"
    upper = corr_type.upper()
    for prefix in _CORRELATION_TYPE_MAP:
        if upper.startswith(prefix):
            return _CORRELATION_TYPE_MAP[prefix]
    return "SEMANTIC"


def _build_excerpt(
    event_ref: str | None, event_source: str | None,
    conn: sqlite3.Connection, corpus_id: str,
) -> str:
    """
    Busca texto representativo do evento referenciado pra usar como
    excerto. Limitado a 200 chars.

    Formato de refs:
      - 'fr_<id>'  -> financial_records
      - 'c_<id>'   -> classifications (texto deriva de source_type)
    """
    if not event_ref:
        return ""
    ref = event_ref.strip()
    text = ""

    if ref.startswith("fr_") and event_source == "financial_record":
        try:
            fr_id = int(ref[3:])
        except ValueError:
            fr_id = -1
        if fr_id > 0:
            row = conn.execute(
                "SELECT valor_centavos, descricao, data_transacao "
                "FROM financial_records WHERE obra = ? AND id = ?",
                (corpus_id, fr_id),
            ).fetchone()
            if row:
                valor = (row["valor_centavos"] or 0) / 100
                desc = row["descricao"] or ""
                text = f"R$ {valor:.2f} · {row['data_transacao']}"
                if desc:
                    text += f" · {desc}"

    elif ref.startswith("c_") and event_source == "classification":
        try:
            cls_id = int(ref[2:])
        except ValueError:
            cls_id = -1
        if cls_id > 0:
            text = _classification_excerpt(conn, corpus_id, cls_id)

    if not text:
        text = f"{ref} ({event_source or '—'})"

    text = text.strip()
    if len(text) > 200:
        text = text[:197].rsplit(" ", 1)[0] + "…"
    return text


def _classification_excerpt(
    conn: sqlite3.Connection, corpus_id: str, cls_id: int,
) -> str:
    """Busca texto de uma classification especifica."""
    row = conn.execute(
        """
        SELECT
            c.source_type,
            c.human_corrected_text,
            c.reasoning,
            t.text AS transcription_text,
            m_direct.content AS text_message_content
        FROM classifications c
        LEFT JOIN transcriptions t
            ON t.obra = c.obra AND t.file_id = c.source_file_id
        LEFT JOIN messages m_direct
            ON m_direct.message_id = c.source_message_id
        WHERE c.obra = ? AND c.id = ?
        """,
        (corpus_id, cls_id),
    ).fetchone()
    if not row:
        return ""
    return (
        row["human_corrected_text"]
        or row["text_message_content"]
        or row["transcription_text"]
        or row["reasoning"]
        or ""
    )


# =============================================================================
# Exports
# =============================================================================


__all__ = [
    "CORRELATIONS_TOP_N",
    "CORRELATION_MIN_CONFIDENCE",
    "CRONOLOGIA_MAX",
    "CorpusNotFoundError",
    "DEFAULT_OBJETO",
    "DEFAULT_OPERATOR",
    "rdo_to_vestigio_data",
]
