"""Testes do ClassifyCache (#46 nivel 1) e JaccardDedup (#46 nivel 2).

Sessao 8. SEM dependencias externas (zero sentence-transformers).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rdo_agent.classifier.cache import (
    CachedLabel,
    ClassifyCache,
    hash_for_cache,
    migrate_classify_cache,
    normalize_text,
)
from rdo_agent.classifier.jaccard_dedup import (
    DEFAULT_THRESHOLD,
    JaccardDedup,
    jaccard,
    tokenize,
)


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------


def test_normalize_text_lowercases():
    assert normalize_text("OK") == "ok"
    assert normalize_text("Açafrão") == "açafrão"  # acentos preservados


def test_normalize_text_strips_punctuation():
    assert normalize_text("ok!") == "ok"
    assert normalize_text("ok.") == "ok"
    assert normalize_text("ok!!") == "ok"
    assert normalize_text("ok?") == "ok"


def test_normalize_text_collapses_whitespace():
    assert normalize_text("  ok   blz  ") == "ok blz"
    assert normalize_text("ok\t\nblz") == "ok blz"


def test_normalize_text_handles_empty():
    assert normalize_text("") == ""
    assert normalize_text("   ") == ""


def test_normalize_text_variants_collapse_to_same_hash():
    """Variantes triviais batem com mesmo hash."""
    h_canon = hash_for_cache("ok", "v1")
    for variant in ("OK", "ok!", "ok.", "  ok  ", "ok\n"):
        assert hash_for_cache(variant, "v1") == h_canon


def test_hash_for_cache_differs_for_different_prompt_versions():
    """Troca de prompt invalida cache automaticamente."""
    h1 = hash_for_cache("texto", "v1")
    h2 = hash_for_cache("texto", "v2")
    assert h1 != h2


# ---------------------------------------------------------------------------
# ClassifyCache
# ---------------------------------------------------------------------------


@pytest.fixture
def cache_conn(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "cache.db")
    conn.row_factory = sqlite3.Row
    migrate_classify_cache(conn)
    return conn


def _make_label(cats: list[str], conf: float = 0.9, pv: str = "v1") -> CachedLabel:
    return CachedLabel(
        categories=cats, confidence=conf,
        reasoning="auto", prompt_version=pv,
    )


def test_cache_miss_returns_none(cache_conn):
    cache = ClassifyCache(cache_conn)
    assert cache.get("alguma coisa", "v1") is None


def test_cache_put_then_get_returns_label(cache_conn):
    cache = ClassifyCache(cache_conn)
    cache.put("ok", _make_label(["off_topic"]))
    cached = cache.get("ok", "v1")
    assert cached is not None
    assert cached.categories == ["off_topic"]
    assert cached.prompt_version == "v1"


def test_cache_normalization_collapses_variants(cache_conn):
    """put('OK!') + get('  ok  ') -> hit."""
    cache = ClassifyCache(cache_conn)
    cache.put("OK!", _make_label(["off_topic"]))
    cached = cache.get("  ok  ", "v1")
    assert cached is not None
    assert cached.categories == ["off_topic"]


def test_cache_invalidation_on_prompt_version_change(cache_conn):
    cache = ClassifyCache(cache_conn)
    cache.put("ok", _make_label(["off_topic"], pv="v1"))
    # Get com prompt_version diferente: miss
    assert cache.get("ok", "v2") is None
    # Get com prompt_version original: hit
    assert cache.get("ok", "v1") is not None


def test_cache_increments_hit_count(cache_conn):
    cache = ClassifyCache(cache_conn)
    cache.put("ok", _make_label(["off_topic"]))
    cache.get("ok", "v1")
    cache.get("ok", "v1")
    cache.get("ok", "v1")
    stats = cache.stats(prompt_version="v1")
    assert stats["entries"] == 1
    assert stats["total_hits"] == 3


def test_cache_stats_no_prompt_version_aggregates(cache_conn):
    cache = ClassifyCache(cache_conn)
    cache.put("ok", _make_label(["off_topic"], pv="v1"))
    cache.put("blz", _make_label(["off_topic"], pv="v1"))
    cache.put("eai", _make_label(["off_topic"], pv="v2"))
    cache.get("ok", "v1")
    stats = cache.stats()
    assert stats["total_entries"] == 3
    assert stats["total_hits"] == 1
    versions = {b["prompt_version"] for b in stats["by_prompt_version"]}
    assert versions == {"v1", "v2"}


def test_cache_put_empty_text_is_noop(cache_conn):
    cache = ClassifyCache(cache_conn)
    cache.put("", _make_label(["off_topic"]))
    assert cache.get("", "v1") is None
    n = cache_conn.execute("SELECT COUNT(*) FROM classify_cache").fetchone()[0]
    assert n == 0


# ---------------------------------------------------------------------------
# tokenize / jaccard
# ---------------------------------------------------------------------------


def test_tokenize_returns_set_of_tokens():
    assert tokenize("ok blz") == {"ok", "blz"}


def test_tokenize_filters_short_and_stopwords():
    # "a", "e", "que" sao stopwords; "ok" passa
    assert tokenize("a e ok que") == {"ok"}


def test_tokenize_lowercases_and_handles_punctuation():
    assert tokenize("OK! Blz?") == {"ok", "blz"}


def test_tokenize_empty_returns_empty_set():
    assert tokenize("") == set()
    assert tokenize("   ") == set()


def test_jaccard_identical_returns_one():
    assert jaccard("ok blz", "blz ok") == 1.0  # mesmo set


def test_jaccard_disjoint_returns_zero():
    assert jaccard("ok blz", "valeu obrigado") == 0.0


def test_jaccard_partial_overlap():
    # "ok" comum em ambos; pool de 2 tokens cada → 1/3 = 0.333
    sim = jaccard("ok blz", "ok valeu")
    assert sim == pytest.approx(1 / 3)


def test_jaccard_handles_empty():
    assert jaccard("", "ok") == 0.0
    assert jaccard("ok", "") == 0.0


# ---------------------------------------------------------------------------
# JaccardDedup
# ---------------------------------------------------------------------------


def test_jaccard_dedup_high_similarity_match():
    """Mensagens similares acima do threshold compartilham label."""
    dd = JaccardDedup(threshold=0.5)
    label_a = ("off_topic", 0.9)
    dd.add("ok blz valeu", label_a)
    similar = dd.find_similar("ok blz")  # 2/3 = 0.667 >= 0.5
    assert similar == label_a


def test_jaccard_dedup_low_similarity_no_match():
    dd = JaccardDedup(threshold=0.8)
    dd.add("ok blz valeu", ("off_topic", 0.9))
    # "valeu blz" tem 2/3 = 0.667 < 0.8 → no match
    assert dd.find_similar("totalmente diferente") is None


def test_jaccard_dedup_empty_pool_returns_none():
    dd = JaccardDedup()
    assert dd.find_similar("qualquer texto") is None
    assert dd.size() == 0


def test_jaccard_dedup_threshold_configurable():
    dd_strict = JaccardDedup(threshold=0.95)
    dd_loose = JaccardDedup(threshold=0.3)
    payload = ("cat", 0.8)
    for dd in (dd_strict, dd_loose):
        dd.add("ok blz valeu", payload)

    # 1/3 = 0.333 — passa em loose, falha em strict
    assert dd_strict.find_similar("ok") is None
    assert dd_loose.find_similar("ok") == payload


def test_jaccard_dedup_evicts_oldest_when_full():
    dd = JaccardDedup(max_pool=3)
    dd.add("alpha beta charlie", ("L1", 1))
    dd.add("delta echo foxtrot", ("L2", 1))
    dd.add("golf hotel india", ("L3", 1))
    dd.add("juliet kilo lima", ("L4", 1))  # evicta L1
    assert dd.size() == 3

    # Texto identico a L1 nao acha (foi evicto)
    assert dd.find_similar("alpha beta charlie") is None
    # Mas L4 (recem-adicionado) ainda matcha
    assert dd.find_similar("juliet kilo lima") == ("L4", 1)


def test_jaccard_dedup_invalid_threshold_raises():
    with pytest.raises(ValueError, match="threshold"):
        JaccardDedup(threshold=0.0)
    with pytest.raises(ValueError, match="threshold"):
        JaccardDedup(threshold=1.5)


def test_jaccard_dedup_invalid_max_pool_raises():
    with pytest.raises(ValueError, match="max_pool"):
        JaccardDedup(max_pool=0)


def test_jaccard_dedup_warm_from():
    dd = JaccardDedup()
    n = dd.warm_from([
        ("ok", ("L1", 1)),
        ("blz", ("L2", 1)),
        ("", ("L_empty", 1)),  # ignorado
    ])
    assert n == 2  # vazio nao conta
    assert dd.size() == 2


def test_jaccard_dedup_default_threshold_is_080():
    """Valor canonico documentado em DEFAULT_THRESHOLD."""
    assert DEFAULT_THRESHOLD == 0.80
    dd = JaccardDedup()
    assert dd.threshold == 0.80
