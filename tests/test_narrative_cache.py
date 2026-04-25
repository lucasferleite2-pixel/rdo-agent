"""Testes do NarrativeCacheManager — Sessao 10 / #52."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rdo_agent.forensic_agent.narrative_cache import (
    CachedNarrative,
    CacheStats,
    NarrativeCacheManager,
    hash_prompt_template,
)
from rdo_agent.orchestrator import init_db


@pytest.fixture
def vault_db(tmp_path):
    return init_db(tmp_path / "vault")


def _seed_narrative(
    conn: sqlite3.Connection, *,
    obra: str, scope: str, scope_ref: str | None,
    text: str, dossier_hash: str,
    prompt_template_hash: str | None = None,
    prompt_version: str = "v1",
) -> int:
    cur = conn.execute(
        """INSERT INTO forensic_narratives
            (obra, scope, scope_ref, narrative_text, dossier_hash,
             model_used, prompt_version, prompt_template_hash,
             events_count, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, 'sonnet-4', ?, ?, 1, 0.9, ?)""",
        (obra, scope, scope_ref, text, dossier_hash, prompt_version,
         prompt_template_hash, "2026-04-25T00:00:00Z"),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# hash_prompt_template
# ---------------------------------------------------------------------------


def test_hash_prompt_deterministic():
    h1 = hash_prompt_template("system: be helpful\nuser: classify")
    h2 = hash_prompt_template("system: be helpful\nuser: classify")
    assert h1 == h2


def test_hash_prompt_changes_on_typo():
    h1 = hash_prompt_template("be helpful")
    h2 = hash_prompt_template("be helpfull")  # typo
    assert h1 != h2


def test_hash_prompt_16_chars():
    h = hash_prompt_template("teste")
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_prompt_empty_returns_empty():
    assert hash_prompt_template("") == ""


# ---------------------------------------------------------------------------
# Migration adiciona coluna
# ---------------------------------------------------------------------------


def test_migration_adds_prompt_template_hash_column(vault_db):
    cols = {
        row["name"]
        for row in vault_db.execute("PRAGMA table_info(forensic_narratives)")
    }
    assert "prompt_template_hash" in cols


def test_migration_creates_cache_index(vault_db):
    rows = vault_db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name = 'idx_narratives_cache'",
    ).fetchall()
    assert len(rows) == 1


def test_migration_idempotent(vault_db):
    from rdo_agent.orchestrator import (
        _migrate_sessao10_narrative_cache_columns,
    )
    # 3 chamadas seguidas não quebram
    _migrate_sessao10_narrative_cache_columns(vault_db)
    _migrate_sessao10_narrative_cache_columns(vault_db)
    _migrate_sessao10_narrative_cache_columns(vault_db)


# ---------------------------------------------------------------------------
# Cache miss / hit
# ---------------------------------------------------------------------------


def test_cache_miss_no_existing(vault_db):
    cache = NarrativeCacheManager(vault_db)
    result = cache.get(
        obra="X", scope="day", scope_ref="2026-04-08",
        prompt_template="abc", dossier_hash="h_dossier",
    )
    assert result is None


def test_cache_hit_full_match(vault_db):
    obra = "X"
    template = "system: classify\nuser: ..."
    pt_hash = hash_prompt_template(template)
    _seed_narrative(
        vault_db, obra=obra, scope="day", scope_ref="2026-04-08",
        text="narrativa cached", dossier_hash="h_dossier",
        prompt_template_hash=pt_hash,
    )

    cache = NarrativeCacheManager(vault_db)
    result = cache.get(
        obra=obra, scope="day", scope_ref="2026-04-08",
        prompt_template=template, dossier_hash="h_dossier",
    )
    assert result is not None
    assert isinstance(result, CachedNarrative)
    assert result.narrative_text == "narrativa cached"
    assert result.prompt_template_hash == pt_hash


def test_cache_miss_on_dossier_change(vault_db):
    """Mudança em dossier_hash invalida cache."""
    obra = "X"
    template = "abc"
    _seed_narrative(
        vault_db, obra=obra, scope="day", scope_ref="2026-04-08",
        text="cached", dossier_hash="h_v1",
        prompt_template_hash=hash_prompt_template(template),
    )

    cache = NarrativeCacheManager(vault_db)
    miss = cache.get(
        obra=obra, scope="day", scope_ref="2026-04-08",
        prompt_template=template, dossier_hash="h_v2",  # mudou
    )
    assert miss is None


def test_cache_miss_on_prompt_typo(vault_db):
    """Typo no prompt invalida cache (binário, sem fuzzy)."""
    obra = "X"
    _seed_narrative(
        vault_db, obra=obra, scope="day", scope_ref="2026-04-08",
        text="cached", dossier_hash="h",
        prompt_template_hash=hash_prompt_template("be helpful"),
    )

    cache = NarrativeCacheManager(vault_db)
    miss = cache.get(
        obra=obra, scope="day", scope_ref="2026-04-08",
        prompt_template="be helpfull", dossier_hash="h",  # typo
    )
    assert miss is None


def test_cache_legacy_narrative_without_hash_misses(vault_db):
    """Narrativa legada (sem hash) sempre dá miss até ser re-narrada."""
    _seed_narrative(
        vault_db, obra="X", scope="day", scope_ref="2026-04-08",
        text="legacy", dossier_hash="h",
        prompt_template_hash=None,  # legacy
    )

    cache = NarrativeCacheManager(vault_db)
    miss = cache.get(
        obra="X", scope="day", scope_ref="2026-04-08",
        prompt_template="abc", dossier_hash="h",
    )
    assert miss is None


def test_cache_isolates_by_obra(vault_db):
    template = "abc"
    pt_hash = hash_prompt_template(template)
    _seed_narrative(
        vault_db, obra="A", scope="day", scope_ref="2026-04-08",
        text="A texto", dossier_hash="h", prompt_template_hash=pt_hash,
    )
    cache = NarrativeCacheManager(vault_db)
    # B não tem narrativa nesse scope_ref
    assert cache.get(
        obra="B", scope="day", scope_ref="2026-04-08",
        prompt_template=template, dossier_hash="h",
    ) is None


def test_cache_handles_null_scope_ref(vault_db):
    """obra_overview pode ter scope_ref NULL — match com IS NULL."""
    template = "abc"
    pt_hash = hash_prompt_template(template)
    _seed_narrative(
        vault_db, obra="X", scope="obra_overview", scope_ref=None,
        text="overview", dossier_hash="h", prompt_template_hash=pt_hash,
    )
    cache = NarrativeCacheManager(vault_db)
    hit = cache.get(
        obra="X", scope="obra_overview", scope_ref=None,
        prompt_template=template, dossier_hash="h",
    )
    assert hit is not None
    assert hit.narrative_text == "overview"


# ---------------------------------------------------------------------------
# annotate_hash + is_cached
# ---------------------------------------------------------------------------


def test_annotate_hash_makes_legacy_cacheable(vault_db):
    """Pegar narrativa legacy + annotate_hash + cache hit funciona."""
    nid = _seed_narrative(
        vault_db, obra="X", scope="day", scope_ref="2026-04-08",
        text="legacy", dossier_hash="h", prompt_template_hash=None,
    )

    cache = NarrativeCacheManager(vault_db)
    template = "the prompt"
    cache.annotate_hash(nid, template)

    hit = cache.get(
        obra="X", scope="day", scope_ref="2026-04-08",
        prompt_template=template, dossier_hash="h",
    )
    assert hit is not None
    assert hit.id == nid


def test_is_cached_returns_bool(vault_db):
    template = "abc"
    pt_hash = hash_prompt_template(template)
    _seed_narrative(
        vault_db, obra="X", scope="day", scope_ref="2026-04-08",
        text="t", dossier_hash="h", prompt_template_hash=pt_hash,
    )
    cache = NarrativeCacheManager(vault_db)
    assert cache.is_cached(
        obra="X", scope="day", scope_ref="2026-04-08",
        prompt_template=template, dossier_hash="h",
    ) is True
    assert cache.is_cached(
        obra="X", scope="day", scope_ref="2026-04-09",
        prompt_template=template, dossier_hash="h",
    ) is False


# ---------------------------------------------------------------------------
# Stats / invalidate
# ---------------------------------------------------------------------------


def test_stats_aggregates_correctly(vault_db):
    obra = "X"
    pt_hash = hash_prompt_template("p")
    _seed_narrative(
        vault_db, obra=obra, scope="day", scope_ref="2026-04-08",
        text="t1", dossier_hash="h1", prompt_template_hash=pt_hash,
    )
    _seed_narrative(
        vault_db, obra=obra, scope="day", scope_ref="2026-04-09",
        text="t2", dossier_hash="h2", prompt_template_hash=None,  # legacy
    )
    _seed_narrative(
        vault_db, obra=obra, scope="week", scope_ref="2026-W15",
        text="w", dossier_hash="hw", prompt_template_hash=pt_hash,
    )

    cache = NarrativeCacheManager(vault_db)
    stats = cache.stats(obra=obra)
    assert isinstance(stats, CacheStats)
    assert stats.total_narratives == 3
    assert stats.with_hash == 2
    assert stats.legacy == 1
    assert stats.by_scope["day"] == 2
    assert stats.by_scope["week"] == 1


def test_invalidate_removes_hash_force_miss_next(vault_db):
    obra = "X"
    template = "abc"
    pt_hash = hash_prompt_template(template)
    _seed_narrative(
        vault_db, obra=obra, scope="day", scope_ref="2026-04-08",
        text="t", dossier_hash="h", prompt_template_hash=pt_hash,
    )

    cache = NarrativeCacheManager(vault_db)
    # Antes: hit
    assert cache.is_cached(
        obra=obra, scope="day", scope_ref="2026-04-08",
        prompt_template=template, dossier_hash="h",
    )
    # Invalidate
    n = cache.invalidate(obra=obra, scope="day")
    assert n == 1
    # Depois: miss
    assert not cache.is_cached(
        obra=obra, scope="day", scope_ref="2026-04-08",
        prompt_template=template, dossier_hash="h",
    )
    # Mas a narrativa em si continua na tabela
    n_rows = vault_db.execute(
        "SELECT COUNT(*) FROM forensic_narratives WHERE obra = ?",
        (obra,),
    ).fetchone()[0]
    assert n_rows == 1


def test_invalidate_filters_by_scope_and_ref(vault_db):
    obra = "X"
    pt_hash = hash_prompt_template("p")
    _seed_narrative(
        vault_db, obra=obra, scope="day", scope_ref="2026-04-08",
        text="t1", dossier_hash="h1", prompt_template_hash=pt_hash,
    )
    _seed_narrative(
        vault_db, obra=obra, scope="day", scope_ref="2026-04-09",
        text="t2", dossier_hash="h2", prompt_template_hash=pt_hash,
    )
    _seed_narrative(
        vault_db, obra=obra, scope="week", scope_ref="2026-W15",
        text="w", dossier_hash="hw", prompt_template_hash=pt_hash,
    )

    cache = NarrativeCacheManager(vault_db)
    n = cache.invalidate(obra=obra, scope="day", scope_ref="2026-04-08")
    assert n == 1
    # Os outros 2 mantém o hash
    n_with_hash = vault_db.execute(
        "SELECT COUNT(*) FROM forensic_narratives "
        "WHERE prompt_template_hash IS NOT NULL",
    ).fetchone()[0]
    assert n_with_hash == 2
