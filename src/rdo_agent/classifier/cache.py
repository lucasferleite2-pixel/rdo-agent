"""
ClassifyCache — exact-match cache para gpt-4o-mini classify (Sessão 8 / #46).

Strategy:

- **Exact-match (este módulo)**: hash de ``(normalize(text), prompt_version)``
  → resultado da classificação. Hit rate esperado em corpus longo:
  30-50% (mensagens curtas como "ok", "blz", "valeu", "rsrs"
  repetem muito).
- **Semântico (próximo módulo)**: Jaccard com janela rolante para
  pegar variações lexicais leves.
- **Batch API (próximo módulo)**: para o restante que precisa API.

Tabela ``classify_cache``:

```sql
CREATE TABLE classify_cache (
    text_hash       TEXT NOT NULL,    -- sha256 hex 16 chars
    prompt_version  TEXT NOT NULL,
    label_json      TEXT NOT NULL,    -- JSON {"categories":[...],"confidence":X,"reasoning":"..."}
    created_at      TEXT NOT NULL,
    hit_count       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (text_hash, prompt_version)
);
```

Versionado por `prompt_version`: troca de prompt invalida cache
automaticamente (queries com chave nova).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

# Whitespace + pontuação simples — não tenta NLP pesado, só reduzir
# variações triviais ("ok!", "ok.", "ok ") ao mesmo hash.
_PUNCT_RE = re.compile(r"[!?.,;:\"'()\[\]{}<>]+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """
    Normaliza texto pra cache exact-match:

    1. lower()
    2. remove pontuação trivial (mantém acentos PT-BR)
    3. colapsa whitespace múltiplo a 1 espaço
    4. strip

    Resultado eh determinístico — duas variantes triviais de uma
    mesma mensagem ("ok!", "ok.", "  ok  ") batem com mesmo hash.
    """
    if not text:
        return ""
    t = text.lower()
    t = _PUNCT_RE.sub(" ", t)
    t = _WHITESPACE_RE.sub(" ", t)
    return t.strip()


def hash_for_cache(text: str, prompt_version: str) -> str:
    """sha256(normalize(text) || '||' || prompt_version)[:16]."""
    digest_input = f"{normalize_text(text)}||{prompt_version}"
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# CacheEntry
# ---------------------------------------------------------------------------


@dataclass
class CachedLabel:
    """Resultado de classificação cacheado."""

    categories: list[str]
    confidence: float
    reasoning: str
    prompt_version: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "categories": self.categories,
                "confidence": self.confidence,
                "reasoning": self.reasoning,
                "prompt_version": self.prompt_version,
            },
            ensure_ascii=False, sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> "CachedLabel":
        d = json.loads(raw)
        return cls(
            categories=d.get("categories") or [],
            confidence=float(d.get("confidence") or 0.0),
            reasoning=d.get("reasoning") or "",
            prompt_version=d.get("prompt_version") or "",
        )


# ---------------------------------------------------------------------------
# ClassifyCache
# ---------------------------------------------------------------------------


def migrate_classify_cache(conn: sqlite3.Connection) -> None:
    """Cria tabela ``classify_cache`` (idempotente)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS classify_cache (
            text_hash      TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            label_json     TEXT NOT NULL,
            created_at     TEXT NOT NULL,
            hit_count      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (text_hash, prompt_version)
        )
        """
    )
    conn.commit()


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class ClassifyCache:
    """
    Cache exact-match para classify. Singleton-friendly por
    ``conn`` — o caller é dono da conexão.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        # Garante a tabela. Migration eh idempotente e barata.
        migrate_classify_cache(conn)

    def get(self, text: str, prompt_version: str) -> CachedLabel | None:
        """Retorna ``CachedLabel`` ou ``None`` se miss. Incrementa hit_count em hit."""
        if not text:
            return None
        h = hash_for_cache(text, prompt_version)
        row = self.conn.execute(
            "SELECT label_json FROM classify_cache "
            "WHERE text_hash = ? AND prompt_version = ?",
            (h, prompt_version),
        ).fetchone()
        if row is None:
            return None
        # Increment hit_count para analytics (qual prompt mais reaproveita)
        self.conn.execute(
            "UPDATE classify_cache SET hit_count = hit_count + 1 "
            "WHERE text_hash = ? AND prompt_version = ?",
            (h, prompt_version),
        )
        self.conn.commit()
        try:
            return CachedLabel.from_json(row[0])
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log.warning("cache row corrupta (%s): %s", h, e)
            return None

    def put(self, text: str, label: CachedLabel) -> None:
        """Insere/atualiza entrada. INSERT OR IGNORE — primeiro put vence."""
        if not text:
            return
        h = hash_for_cache(text, label.prompt_version)
        self.conn.execute(
            "INSERT OR IGNORE INTO classify_cache "
            "(text_hash, prompt_version, label_json, created_at, hit_count) "
            "VALUES (?, ?, ?, ?, 0)",
            (h, label.prompt_version, label.to_json(), _now_iso()),
        )
        self.conn.commit()

    def stats(self, prompt_version: str | None = None) -> dict:
        """
        Métricas pra o operador: total de entradas, hits acumulados,
        distribuição por prompt_version.
        """
        if prompt_version:
            row = self.conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(hit_count), 0) "
                "FROM classify_cache WHERE prompt_version = ?",
                (prompt_version,),
            ).fetchone()
            return {
                "prompt_version": prompt_version,
                "entries": int(row[0] or 0),
                "total_hits": int(row[1] or 0),
            }
        rows = self.conn.execute(
            "SELECT prompt_version, COUNT(*), COALESCE(SUM(hit_count), 0) "
            "FROM classify_cache GROUP BY prompt_version"
        ).fetchall()
        return {
            "by_prompt_version": [
                {"prompt_version": r[0], "entries": int(r[1] or 0),
                 "total_hits": int(r[2] or 0)}
                for r in rows
            ],
            "total_entries": sum(int(r[1] or 0) for r in rows),
            "total_hits": sum(int(r[2] or 0) for r in rows),
        }


__all__ = [
    "CachedLabel",
    "ClassifyCache",
    "hash_for_cache",
    "migrate_classify_cache",
    "normalize_text",
]
