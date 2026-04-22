"""
Persistence — Sprint 5 Fase A F5.

Salva narrativas em DB (forensic_narratives) + arquivo local.

Idempotencia: UNIQUE(obra, scope, scope_ref, dossier_hash) previne
duplicatas — se mesma combinacao ja existe, retorna id existente
(cache hit).

Arquivos: reports/narratives/{obra}/{scope}_{scope_ref_ou_overview}.md
Cria diretorios se necessario.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from rdo_agent.forensic_agent.narrator import NarrationResult
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_REPORTS_ROOT = Path("reports/narratives")


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _compute_filename(scope: str, scope_ref: str | None) -> str:
    """
    Convencao de nomeacao:
      day + 2026-04-06  -> day_2026-04-06.md
      obra_overview + None -> obra_overview.md
    """
    if scope == "day" and scope_ref:
        return f"day_{scope_ref}.md"
    if scope == "obra_overview":
        return "obra_overview.md"
    # fallback defensivo
    return f"{scope}_{scope_ref or 'unknown'}.md"


def _find_existing_narrative(
    conn: sqlite3.Connection,
    obra: str, scope: str, scope_ref: str | None, dossier_hash: str,
) -> int | None:
    """Retorna id se ja existe narrativa com mesmo cache key, senao None."""
    # SQLite UNIQUE trata NULL como valor distinto (NULLs nao sao iguais).
    # Precisamos tratar scope_ref NULL explicitamente no SELECT.
    if scope_ref is None:
        row = conn.execute(
            """SELECT id FROM forensic_narratives
               WHERE obra = ? AND scope = ? AND scope_ref IS NULL
                 AND dossier_hash = ?""",
            (obra, scope, dossier_hash),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT id FROM forensic_narratives
               WHERE obra = ? AND scope = ? AND scope_ref = ?
                 AND dossier_hash = ?""",
            (obra, scope, scope_ref, dossier_hash),
        ).fetchone()
    return row["id"] if row else None


def save_narrative(
    conn: sqlite3.Connection,
    *,
    obra: str,
    scope: str,
    scope_ref: str | None,
    dossier_hash: str,
    narration: NarrationResult,
    validation: dict,
    events_count: int,
    reports_root: Path = DEFAULT_REPORTS_ROOT,
) -> tuple[int, Path, bool]:
    """
    Salva narrativa em DB + arquivo local.

    Args:
        conn: conexao SQLite
        obra, scope, scope_ref, dossier_hash: chaves do cache
        narration: resultado de narrator.narrate()
        validation: resultado de validator.validate_narrative()
        events_count: numero de eventos no dossier (pra coluna
            forensic_narratives.events_count)
        reports_root: raiz onde salvar arquivo (default 'reports/narratives')

    Returns:
        (narrative_id, arquivo_path, was_cached)
        was_cached=True se ja existia (nao criou novo); False se criou.

    Levanta nada — errors de disk writes sao logados mas nao abortam.
    """
    # 1) Cache hit?
    existing_id = _find_existing_narrative(
        conn, obra, scope, scope_ref, dossier_hash,
    )
    if existing_id is not None:
        filename = _compute_filename(scope, scope_ref)
        file_path = reports_root / obra / filename
        log.info(
            "narrative cache hit for %s %s %s (id=%s)",
            obra, scope, scope_ref, existing_id,
        )
        return existing_id, file_path, True

    # 2) Insert em DB
    confidence = None
    if isinstance(narration.self_assessment, dict):
        try:
            confidence = float(narration.self_assessment.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = None

    validation_json = json.dumps(
        validation, ensure_ascii=False, sort_keys=True,
    )

    cur = conn.execute(
        """INSERT INTO forensic_narratives (
            obra, scope, scope_ref, narrative_text, dossier_hash,
            model_used, prompt_version, api_call_id, events_count,
            confidence, validation_checklist_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            obra, scope, scope_ref, narration.markdown_text, dossier_hash,
            narration.model, narration.prompt_version,
            narration.api_call_id, events_count,
            confidence, validation_json, _now_iso_utc(),
        ),
    )
    conn.commit()
    narrative_id = cur.lastrowid
    assert narrative_id is not None

    # 3) Dump em arquivo
    obra_dir = reports_root / obra
    obra_dir.mkdir(parents=True, exist_ok=True)
    filename = _compute_filename(scope, scope_ref)
    file_path = obra_dir / filename
    try:
        file_path.write_text(narration.markdown_text, encoding="utf-8")
    except OSError as exc:
        log.warning(
            "falha ao salvar narrativa em %s: %s", file_path, exc,
        )

    return narrative_id, file_path, False


__all__ = [
    "DEFAULT_REPORTS_ROOT",
    "save_narrative",
]
