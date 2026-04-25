"""
Narrator hierárquico — Sessão 10 / dívida #51.

Cascata: ``day → week → month → obra_overview``. Cada nível
**resume** o nível inferior, preservando ``file_ids`` de evidência
(rastreabilidade forense não pode quebrar).

Para corpus piloto pequeno (EVERALDO, ~13 dias) os níveis intermediários
fazem pouca diferença — o ganho aparece em corpus longos onde
overview seria 1M+ tokens de input se concatenasse todas as days.

Este módulo entrega:

- ``VALID_SCOPES``: enum-like set canônico (single source of truth
  agora que o CHECK constraint foi removido — ADR-010).
- ``compute_buckets(corpus_id, scope, ...)``: gera lista de
  ``(scope_ref, start, end)`` do nível solicitado.
- ``compose_input_from_children(child_narratives, scope)``: monta
  string de input pra próximo nível, preservando file_ids.
- ``narrate_hierarchy(...)``: cascade end-to-end com cache
  implícito (skip se já existe).

A função ``narrate()`` existente em ``narrator.py`` continua sendo o
caminho canônico para 1 narrativa única (qualquer scope). Este módulo
é orquestrador de níveis.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)


# Single source of truth para scopes válidos (ADR-010).
# CHECK constraint do schema foi removido na Sessão 10 (migration
# `_migrate_sessao10_relax_narratives_scope_check`).
VALID_SCOPES: frozenset[str] = frozenset({
    "day", "week", "month", "quarter", "obra_overview", "adversarial",
})

# Hierarquia canônica de cascata. Cada índice maior consome o anterior.
HIERARCHY: tuple[str, ...] = (
    "day", "week", "month", "quarter", "obra_overview",
)

# file_id pattern para extracao de evidencias preservadas em cascata.
# Match conservador: m_/f_/c_ seguido de 4-12 chars hex/alfanumericos.
_FILE_ID_RE = re.compile(r"\b(?:m|f|c|fr)_[A-Za-z0-9]{4,12}\b")


@dataclass(frozen=True)
class TimeBucket:
    """Janela de tempo de um scope_ref."""

    scope: str
    scope_ref: str   # ex: "2026-W14" (week), "2026-04" (month)
    start: date
    end: date        # inclusive


# ---------------------------------------------------------------------------
# compute_buckets
# ---------------------------------------------------------------------------


def _isoweek_ref(d: date) -> str:
    """Retorna 'YYYY-WNN' (ISO 8601 week)."""
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _isoweek_start_end(year: int, week: int) -> tuple[date, date]:
    """Retorna (segunda, domingo) da semana ISO."""
    # Semana ISO: segunda-feira é o início
    monday = date.fromisocalendar(year, week, 1)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def compute_buckets(
    conn: sqlite3.Connection,
    obra: str,
    scope: str,
) -> list[TimeBucket]:
    """
    Gera buckets de tempo para o ``scope`` com base nas narrativas
    filhas existentes.

    Para ``scope='week'``, agrupa as ``day`` narratives de ``obra``
    por semana ISO. Para ``month``, por mês. Para ``quarter``, por
    trimestre. Para ``obra_overview``, retorna 1 bucket cobrindo
    span total.

    Não cria buckets vazios — só retorna janelas que tem ao menos 1
    child narrative.
    """
    if scope not in VALID_SCOPES:
        raise ValueError(f"scope invalido: {scope}; esperado em {VALID_SCOPES}")

    # Determina nível filho (scope - 1 na hierarquia)
    try:
        idx = HIERARCHY.index(scope)
    except ValueError as e:
        # ex: "adversarial" não está na hierarquia
        raise ValueError(f"scope {scope} fora da hierarquia") from e
    if idx == 0:
        # day é folha: bucket = cada day já existente em messages/classifications
        return _compute_day_buckets(conn, obra)

    # Para obra_overview, busca o maior scope disponível (quarter →
    # month → week → day). Em corpus pequeno onde quarter foi
    # pulado, isso permite obra_overview consumir o nível mais alto
    # que existe.
    if scope == "obra_overview":
        for fallback in ("quarter", "month", "week", "day"):
            fb_rows = conn.execute(
                "SELECT scope_ref FROM forensic_narratives "
                "WHERE obra = ? AND scope = ? AND scope_ref IS NOT NULL "
                "ORDER BY scope_ref",
                (obra, fallback),
            ).fetchall()
            if fb_rows:
                first = _parse_scope_ref_to_date(fallback, fb_rows[0][0])
                last = _parse_scope_ref_to_date(fallback, fb_rows[-1][0])
                return [TimeBucket(scope, "all", first, last)]
        return []

    child_scope = HIERARCHY[idx - 1]
    rows = conn.execute(
        "SELECT scope_ref FROM forensic_narratives "
        "WHERE obra = ? AND scope = ? AND scope_ref IS NOT NULL "
        "ORDER BY scope_ref",
        (obra, child_scope),
    ).fetchall()

    if not rows:
        return []

    # (obra_overview tratado acima com fallback explícito.)

    # week/month/quarter: agrupa filhos por bucket
    buckets: dict[str, tuple[date, date]] = {}
    for r in rows:
        child_ref = r[0]
        child_date = _parse_scope_ref_to_date(child_scope, child_ref)
        bucket_ref, bstart, bend = _bucket_for(scope, child_date)
        if bucket_ref not in buckets:
            buckets[bucket_ref] = (bstart, bend)
    return [
        TimeBucket(scope=scope, scope_ref=ref, start=s, end=e)
        for ref, (s, e) in sorted(buckets.items())
    ]


def _compute_day_buckets(
    conn: sqlite3.Connection, obra: str,
) -> list[TimeBucket]:
    """Days extraídas das narratives day já existentes (ou de
    messages/classifications se ainda não há days)."""
    rows = conn.execute(
        "SELECT DISTINCT scope_ref FROM forensic_narratives "
        "WHERE obra = ? AND scope = 'day' AND scope_ref IS NOT NULL "
        "ORDER BY scope_ref",
        (obra,),
    ).fetchall()
    if not rows:
        return []
    out = []
    for r in rows:
        d = _parse_scope_ref_to_date("day", r[0])
        out.append(TimeBucket("day", r[0], d, d))
    return out


def _parse_scope_ref_to_date(scope: str, scope_ref: str) -> date:
    """
    Converte ``scope_ref`` em data canonical para comparação.

    - day:        "2026-04-08"            → date(2026, 4, 8)
    - week:       "2026-W14"               → date da segunda-feira
    - month:      "2026-04"                → primeiro dia do mês
    - quarter:    "2026-Q2"                → primeiro dia do trimestre
    - obra_overview: "all"                  → date.min (placeholder)
    """
    if scope == "day":
        return date.fromisoformat(scope_ref)
    if scope == "week":
        # "YYYY-WNN"
        m = re.match(r"^(\d{4})-W(\d{1,2})$", scope_ref)
        if not m:
            raise ValueError(f"week scope_ref invalido: {scope_ref}")
        y, w = int(m.group(1)), int(m.group(2))
        return date.fromisocalendar(y, w, 1)
    if scope == "month":
        # "YYYY-MM"
        return date.fromisoformat(f"{scope_ref}-01")
    if scope == "quarter":
        m = re.match(r"^(\d{4})-Q([1-4])$", scope_ref)
        if not m:
            raise ValueError(f"quarter scope_ref invalido: {scope_ref}")
        y, q = int(m.group(1)), int(m.group(2))
        first_month = (q - 1) * 3 + 1
        return date(y, first_month, 1)
    if scope == "obra_overview":
        return date.min
    raise ValueError(f"scope desconhecido: {scope}")


def _bucket_for(
    scope: str, d: date,
) -> tuple[str, date, date]:
    """
    Retorna (ref, start, end) do bucket de ``scope`` que contém ``d``.

    Ex: _bucket_for("month", date(2026, 4, 8)) →
        ("2026-04", date(2026, 4, 1), date(2026, 4, 30))
    """
    if scope == "week":
        ref = _isoweek_ref(d)
        iso_year, iso_week, _ = d.isocalendar()
        s, e = _isoweek_start_end(iso_year, iso_week)
        return ref, s, e
    if scope == "month":
        ref = f"{d.year:04d}-{d.month:02d}"
        s = date(d.year, d.month, 1)
        # Último dia do mês
        if d.month == 12:
            next_first = date(d.year + 1, 1, 1)
        else:
            next_first = date(d.year, d.month + 1, 1)
        e = next_first - timedelta(days=1)
        return ref, s, e
    if scope == "quarter":
        q = (d.month - 1) // 3 + 1
        ref = f"{d.year:04d}-Q{q}"
        first_month = (q - 1) * 3 + 1
        s = date(d.year, first_month, 1)
        last_month = first_month + 2
        if last_month == 12:
            next_first = date(d.year + 1, 1, 1)
        else:
            next_first = date(d.year, last_month + 1, 1)
        e = next_first - timedelta(days=1)
        return ref, s, e
    raise ValueError(f"_bucket_for: scope sem agrupamento: {scope}")


# ---------------------------------------------------------------------------
# compose_input_from_children
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChildNarrative:
    """Narrativa filha consumida para compor o input do nível superior."""

    scope: str
    scope_ref: str
    narrative_text: str
    file_ids: frozenset[str]


def extract_file_ids(narrative_text: str) -> frozenset[str]:
    """Extrai todos os file_ids citados na narrativa (m_/f_/c_/fr_)."""
    if not narrative_text:
        return frozenset()
    return frozenset(_FILE_ID_RE.findall(narrative_text))


def fetch_child_narratives(
    conn: sqlite3.Connection,
    obra: str,
    child_scope: str,
    *,
    bucket: TimeBucket | None = None,
) -> list[ChildNarrative]:
    """
    Busca narrativas filhas de ``child_scope`` em ``obra``,
    opcionalmente filtradas por ``bucket`` (start/end inclusive).

    Para cada child, extrai file_ids da narrativa para preservação
    forense.
    """
    sql = (
        "SELECT scope_ref, narrative_text FROM forensic_narratives "
        "WHERE obra = ? AND scope = ? AND scope_ref IS NOT NULL"
    )
    params: list = [obra, child_scope]
    if bucket is not None and child_scope == "day":
        # bucket.start/end são date inclusive
        sql += " AND scope_ref >= ? AND scope_ref <= ?"
        params.append(bucket.start.isoformat())
        params.append(bucket.end.isoformat())
    sql += " ORDER BY scope_ref"

    rows = conn.execute(sql, params).fetchall()
    return [
        ChildNarrative(
            scope=child_scope,
            scope_ref=r[0],
            narrative_text=r[1] or "",
            file_ids=extract_file_ids(r[1] or ""),
        )
        for r in rows
    ]


def compose_input_from_children(
    children: list[ChildNarrative],
    *,
    parent_scope: str,
    bucket_label: str,
) -> str:
    """
    Compõe input markdown para o próximo nível da hierarquia.

    Estrutura:
        # Período: ``bucket_label`` (parent_scope)
        # Narrativas filhas (N de scope_filho):

        ## scope_ref_1
        [conteúdo da narrativa 1]

        ## scope_ref_2
        [conteúdo da narrativa 2]
        ...

        # Evidências citadas (file_ids):
        m_aaa, f_bbb, ...

    file_ids preservados são união dos file_ids dos children
    (rastreabilidade forense).
    """
    if not children:
        return f"# Período: {bucket_label} ({parent_scope})\n\n(sem narrativas filhas)\n"

    parts: list[str] = [
        f"# Período: {bucket_label} ({parent_scope})\n",
        f"# Narrativas filhas ({len(children)} de {children[0].scope}):\n",
    ]
    all_file_ids: set[str] = set()
    for child in children:
        parts.append(f"\n## {child.scope_ref}\n")
        parts.append(child.narrative_text.strip())
        parts.append("\n")
        all_file_ids.update(child.file_ids)

    if all_file_ids:
        sorted_ids = sorted(all_file_ids)
        parts.append("\n# Evidências citadas (file_ids):\n")
        # Wrap a cada 8 ids por linha para legibilidade
        for i in range(0, len(sorted_ids), 8):
            parts.append(", ".join(sorted_ids[i:i + 8]) + "\n")

    return "".join(parts)


# ---------------------------------------------------------------------------
# narrate_hierarchy
# ---------------------------------------------------------------------------


def narrate_hierarchy(
    conn: sqlite3.Connection,
    obra: str,
    *,
    end_scope: str = "obra_overview",
    skip_existing: bool = True,
    skip_quarter_below_days: int = 90,
    narrate_fn=None,
) -> dict[str, int]:
    """
    Cascade narrate de ``day`` até ``end_scope``.

    Args:
        conn: SQLite com day-narratives já populadas.
        obra: corpus_id.
        end_scope: alvo final (default ``obra_overview``).
        skip_existing: se True (default), pula buckets que já têm
            narrative persistida (cache implícito por scope_ref).
        skip_quarter_below_days: se span do corpus for menor, pula
            ``quarter`` (corpus piloto pequeno não justifica). Default
            90 dias.
        narrate_fn: injeção de função pra teste; default
            ``rdo_agent.forensic_agent.narrator.narrate``.

    Returns:
        dict ``{scope: count_narrated}`` por nível.
    """
    if end_scope not in HIERARCHY:
        raise ValueError(
            f"end_scope deve estar em {HIERARCHY}, recebi {end_scope}"
        )

    if narrate_fn is None:
        from rdo_agent.forensic_agent.narrator import narrate as narrate_fn

    counts: dict[str, int] = {}
    end_idx = HIERARCHY.index(end_scope)

    # Verifica se quarter deve ser skipped por ser corpus pequeno
    span_days = _corpus_span_days(conn, obra)
    skip_quarter = (
        end_idx >= HIERARCHY.index("quarter")
        and span_days < skip_quarter_below_days
    )

    # Cascade: começa do scope IMEDIATAMENTE acima de day (week)
    for scope in HIERARCHY[1: end_idx + 1]:
        if scope == "quarter" and skip_quarter:
            log.info(
                "narrate_hierarchy: pulando quarter (corpus span=%d < %d dias)",
                span_days, skip_quarter_below_days,
            )
            counts[scope] = 0
            continue

        buckets = compute_buckets(conn, obra, scope)
        if not buckets:
            counts[scope] = 0
            continue

        narrated = 0
        for bucket in buckets:
            if skip_existing and _narrative_exists(
                conn, obra, scope, bucket.scope_ref,
            ):
                continue

            child_scope = HIERARCHY[HIERARCHY.index(scope) - 1]
            children = fetch_child_narratives(
                conn, obra, child_scope, bucket=bucket,
            )
            # Para obra_overview, fallback para o maior scope com
            # rows quando o imediato (quarter) foi skipped.
            if not children and scope == "obra_overview":
                for fallback in ("month", "week", "day"):
                    children = fetch_child_narratives(
                        conn, obra, fallback, bucket=None,
                    )
                    if children:
                        child_scope = fallback
                        break
            if not children:
                continue

            input_text = compose_input_from_children(
                children, parent_scope=scope, bucket_label=bucket.scope_ref,
            )
            dossier = {
                "obra": obra,
                "scope": scope,
                "scope_ref": bucket.scope_ref,
                "input_from_children": input_text,
                "child_scope": child_scope,
                "child_count": len(children),
            }
            try:
                narrate_fn(dossier, conn)
                narrated += 1
            except Exception as e:
                log.warning(
                    "narrate falhou em %s %s: %s",
                    scope, bucket.scope_ref, e,
                )
        counts[scope] = narrated

    return counts


def _narrative_exists(
    conn: sqlite3.Connection, obra: str, scope: str, scope_ref: str,
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM forensic_narratives "
        "WHERE obra = ? AND scope = ? AND scope_ref = ? LIMIT 1",
        (obra, scope, scope_ref),
    ).fetchone()
    return row is not None


def _corpus_span_days(conn: sqlite3.Connection, obra: str) -> int:
    """Span de dias entre a primeira e última day-narrative."""
    row = conn.execute(
        "SELECT MIN(scope_ref), MAX(scope_ref) "
        "FROM forensic_narratives "
        "WHERE obra = ? AND scope = 'day' AND scope_ref IS NOT NULL",
        (obra,),
    ).fetchone()
    if not row or row[0] is None or row[1] is None:
        return 0
    try:
        first = date.fromisoformat(row[0])
        last = date.fromisoformat(row[1])
        return (last - first).days + 1
    except (ValueError, TypeError):
        return 0


__all__ = [
    "HIERARCHY",
    "VALID_SCOPES",
    "ChildNarrative",
    "TimeBucket",
    "compose_input_from_children",
    "compute_buckets",
    "extract_file_ids",
    "fetch_child_narratives",
    "narrate_hierarchy",
]
