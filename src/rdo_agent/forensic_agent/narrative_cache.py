"""
NarrativeCacheManager — cache binário de narrativas (Sessão 10 / #52).

Cache hit explícito ANTES de chamar Sonnet API:

```
hit = (corpus_id, scope, scope_ref,
       prompt_template_hash, dossier_hash) bate exato
```

Mudança em qualquer um dos componentes → cache miss → narrativa
nova é gerada. **Sem similarity fuzzy nesta versão** — fica como
dívida #62 ativada por triggers de produção (operador reportar
re-pagamento por typo, custo agregado de typos > $5, etc).

A coluna ``prompt_template_hash`` é adicionada via migration
``_migrate_sessao10_narrative_cache_columns``. Para narrativas
**legadas** (sem hash), o cache trata como sempre miss até serem
re-narradas (uma narrativa legada não pode dar hit em prompt_hash
desconhecido).

Schema relevante (pós-migration):

```sql
ALTER TABLE forensic_narratives ADD COLUMN prompt_template_hash TEXT;
CREATE INDEX idx_narratives_cache
    ON forensic_narratives(obra, scope, scope_ref,
                            prompt_template_hash, dossier_hash);
```
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def hash_prompt_template(template: str) -> str:
    """
    Hash estável de um prompt template (system + user + tools +
    config). Retorna 16 hex chars (sha256 truncado).

    Caller deve passar a versão **canônica** do prompt — mesma
    string == mesmo hash. Espaços extras / quebras de linha
    diferentes geram hashes diferentes (binário, conforme ADR-012).
    """
    if not template:
        return ""
    return hashlib.sha256(template.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CachedNarrative:
    """Resultado completo de cache hit."""

    id: int
    obra: str
    scope: str
    scope_ref: str | None
    narrative_text: str
    dossier_hash: str
    prompt_template_hash: str | None
    prompt_version: str
    model_used: str
    confidence: float | None
    created_at: str


@dataclass(frozen=True)
class CacheStats:
    """Métricas agregadas de cache."""

    total_narratives: int
    with_hash: int     # rows com prompt_template_hash preenchido
    legacy: int        # rows sem hash (cache miss perpétuo até re-narrar)
    by_scope: dict[str, int]


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class NarrativeCacheManager:
    """
    Cache binário sobre ``forensic_narratives``. Caller fornece
    conn (não cria connection própria).

    Args:
        conn: SQLite com tabela e migration aplicadas.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- Lookup ----

    def get(
        self,
        *,
        obra: str,
        scope: str,
        scope_ref: str | None,
        prompt_template: str,
        dossier_hash: str,
    ) -> CachedNarrative | None:
        """
        Cache hit se houver row com **todos** os 5 componentes
        batendo exato. Caso contrário, ``None`` (miss).

        Para ``scope_ref=None`` (ex: obra_overview legacy), match
        por ``IS NULL`` no SQL.
        """
        prompt_hash = hash_prompt_template(prompt_template)
        if scope_ref is None:
            row = self.conn.execute(
                """
                SELECT id, obra, scope, scope_ref, narrative_text,
                       dossier_hash, prompt_template_hash,
                       prompt_version, model_used, confidence,
                       created_at
                  FROM forensic_narratives
                 WHERE obra = ? AND scope = ? AND scope_ref IS NULL
                   AND prompt_template_hash = ?
                   AND dossier_hash = ?
                 ORDER BY id DESC LIMIT 1
                """,
                (obra, scope, prompt_hash, dossier_hash),
            ).fetchone()
        else:
            row = self.conn.execute(
                """
                SELECT id, obra, scope, scope_ref, narrative_text,
                       dossier_hash, prompt_template_hash,
                       prompt_version, model_used, confidence,
                       created_at
                  FROM forensic_narratives
                 WHERE obra = ? AND scope = ? AND scope_ref = ?
                   AND prompt_template_hash = ?
                   AND dossier_hash = ?
                 ORDER BY id DESC LIMIT 1
                """,
                (obra, scope, scope_ref, prompt_hash, dossier_hash),
            ).fetchone()
        if row is None:
            return None
        return CachedNarrative(
            id=row["id"], obra=row["obra"], scope=row["scope"],
            scope_ref=row["scope_ref"], narrative_text=row["narrative_text"],
            dossier_hash=row["dossier_hash"],
            prompt_template_hash=row["prompt_template_hash"],
            prompt_version=row["prompt_version"],
            model_used=row["model_used"], confidence=row["confidence"],
            created_at=row["created_at"],
        )

    def is_cached(
        self, *,
        obra: str, scope: str, scope_ref: str | None,
        prompt_template: str, dossier_hash: str,
    ) -> bool:
        """``True`` se ``get(...)`` retornaria não-``None``."""
        return self.get(
            obra=obra, scope=scope, scope_ref=scope_ref,
            prompt_template=prompt_template, dossier_hash=dossier_hash,
        ) is not None

    # ---- Update on persist ----

    def annotate_hash(
        self, narrative_id: int, prompt_template: str,
    ) -> None:
        """
        Aplica ``prompt_template_hash`` em uma row recém-inserida.
        Use após ``save_narrative`` para tornar a narrativa
        cacheável em runs futuros.
        """
        h = hash_prompt_template(prompt_template)
        self.conn.execute(
            "UPDATE forensic_narratives "
            "SET prompt_template_hash = ? WHERE id = ?",
            (h, narrative_id),
        )
        self.conn.commit()

    # ---- Stats / invalidation ----

    def stats(self, *, obra: str | None = None) -> CacheStats:
        """
        Métricas: total, com hash, legacy (sem hash), por scope.
        """
        params: list = []
        where = ""
        if obra is not None:
            where = " WHERE obra = ?"
            params.append(obra)

        total = int(self.conn.execute(
            f"SELECT COUNT(*) FROM forensic_narratives{where}",
            params,
        ).fetchone()[0])
        with_hash = int(self.conn.execute(
            f"SELECT COUNT(*) FROM forensic_narratives{where}"
            f"{' AND' if where else ' WHERE'} "
            "prompt_template_hash IS NOT NULL "
            "AND prompt_template_hash != ''",
            params,
        ).fetchone()[0])
        by_scope_rows = self.conn.execute(
            f"SELECT scope, COUNT(*) FROM forensic_narratives{where} "
            "GROUP BY scope",
            params,
        ).fetchall()
        return CacheStats(
            total_narratives=total,
            with_hash=with_hash,
            legacy=total - with_hash,
            by_scope={r[0]: int(r[1]) for r in by_scope_rows},
        )

    def invalidate(
        self, *,
        obra: str,
        scope: str | None = None,
        scope_ref: str | None = None,
        before: str | None = None,
    ) -> int:
        """
        Remove ``prompt_template_hash`` (= força miss em runs
        futuros) de rows que casarem o filtro. **Não deleta** as
        narratives — a próxima chamada gera nova row.

        Args:
            obra: obrigatório (escopo de invalidação).
            scope: opcional. Se None, qualquer scope.
            scope_ref: opcional.
            before: ISO timestamp opcional. Invalida só rows
                criadas antes (cuidado: esse cutoff é em
                ``created_at``).

        Returns:
            número de rows afetadas.
        """
        sql = (
            "UPDATE forensic_narratives "
            "SET prompt_template_hash = NULL "
            "WHERE obra = ?"
        )
        params: list = [obra]
        if scope is not None:
            sql += " AND scope = ?"
            params.append(scope)
        if scope_ref is not None:
            sql += " AND scope_ref = ?"
            params.append(scope_ref)
        if before is not None:
            sql += " AND created_at < ?"
            params.append(before)
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        n = cur.rowcount or 0
        log.info(
            "narrative_cache invalidate: obra=%s scope=%s ref=%s "
            "before=%s → %d rows",
            obra, scope, scope_ref, before, n,
        )
        return n


__all__ = [
    "CachedNarrative",
    "CacheStats",
    "NarrativeCacheManager",
    "hash_prompt_template",
]
