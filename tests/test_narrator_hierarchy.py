"""Testes do narrator hierarquico — Sessao 10 / #51.

Cobre:
- VALID_SCOPES + HIERARCHY consistencia
- Migration relax CHECK (insert week/month nao falha)
- compute_buckets para day/week/month/quarter/obra_overview
- compose_input_from_children preserva file_ids
- narrate_hierarchy cascade com skip_existing + skip_quarter
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from rdo_agent.forensic_agent.hierarchy import (
    HIERARCHY,
    VALID_SCOPES,
    ChildNarrative,
    TimeBucket,
    compose_input_from_children,
    compute_buckets,
    extract_file_ids,
    fetch_child_narratives,
    narrate_hierarchy,
)
from rdo_agent.orchestrator import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_db(tmp_path):
    return init_db(tmp_path / "vault")


def _seed_day_narrative(
    conn: sqlite3.Connection, *,
    obra: str, day: str, narrative: str = "narrativa do dia",
    file_ids: tuple[str, ...] = (),
) -> int:
    """Insere narrativa day (file_ids opcionais inseridos no texto)."""
    text = narrative
    if file_ids:
        text += "\n\nEvidências: " + ", ".join(file_ids)
    cur = conn.execute(
        "INSERT INTO forensic_narratives "
        "(obra, scope, scope_ref, narrative_text, dossier_hash, "
        " model_used, prompt_version, events_count, confidence, "
        " created_at) "
        "VALUES (?, 'day', ?, ?, ?, 'sonnet-4', 'v1', 5, 0.9, ?)",
        (obra, day, text, f"hash_{day}", "2026-04-25T00:00:00Z"),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# VALID_SCOPES + HIERARCHY
# ---------------------------------------------------------------------------


def test_valid_scopes_canonical():
    assert "day" in VALID_SCOPES
    assert "week" in VALID_SCOPES
    assert "month" in VALID_SCOPES
    assert "quarter" in VALID_SCOPES
    assert "obra_overview" in VALID_SCOPES
    assert "adversarial" in VALID_SCOPES


def test_hierarchy_ordering():
    """day vem antes de week, week antes de month, ..."""
    assert HIERARCHY == ("day", "week", "month", "quarter", "obra_overview")
    for s in HIERARCHY:
        assert s in VALID_SCOPES


# ---------------------------------------------------------------------------
# Migration relax CHECK
# ---------------------------------------------------------------------------


def test_migration_allows_week_scope_insert(vault_db):
    """Apos migration relax CHECK, insert com scope='week' funciona."""
    vault_db.execute(
        "INSERT INTO forensic_narratives "
        "(obra, scope, scope_ref, narrative_text, dossier_hash, "
        " model_used, prompt_version, events_count, confidence, "
        " created_at) "
        "VALUES ('OBRA_T', 'week', '2026-W14', 'texto', 'h', 'sonnet', "
        "'v1', 0, 0.9, '2026-04-25T00:00:00Z')",
    )
    vault_db.commit()
    n = vault_db.execute(
        "SELECT COUNT(*) FROM forensic_narratives WHERE scope = 'week'",
    ).fetchone()[0]
    assert n == 1


def test_migration_allows_month_scope_insert(vault_db):
    vault_db.execute(
        "INSERT INTO forensic_narratives "
        "(obra, scope, scope_ref, narrative_text, dossier_hash, "
        " model_used, prompt_version, events_count, confidence, "
        " created_at) "
        "VALUES ('OBRA_T', 'month', '2026-04', 'm', 'h', 'sonnet', "
        "'v1', 0, 0.9, '2026-04-25T00:00:00Z')",
    )
    vault_db.commit()
    n = vault_db.execute(
        "SELECT COUNT(*) FROM forensic_narratives WHERE scope = 'month'",
    ).fetchone()[0]
    assert n == 1


def test_migration_idempotent(vault_db):
    """Re-rodar migration nao quebra nem perde dados."""
    from rdo_agent.orchestrator import (
        _migrate_sessao10_relax_narratives_scope_check,
    )
    _seed_day_narrative(vault_db, obra="X", day="2026-04-08")
    _migrate_sessao10_relax_narratives_scope_check(vault_db)
    _migrate_sessao10_relax_narratives_scope_check(vault_db)
    n = vault_db.execute(
        "SELECT COUNT(*) FROM forensic_narratives",
    ).fetchone()[0]
    assert n == 1


# ---------------------------------------------------------------------------
# extract_file_ids
# ---------------------------------------------------------------------------


def test_extract_file_ids_finds_all_patterns():
    text = (
        "PIX em f_abc12345 confirma valor. Mensagem m_def678 "
        "discutiu c_99 e renegociacao via fr_pix1234."
    )
    ids = extract_file_ids(text)
    assert "f_abc12345" in ids
    assert "m_def678" in ids
    assert "c_99" not in ids  # < 4 chars apos prefix
    assert "fr_pix1234" in ids


def test_extract_file_ids_empty_text():
    assert extract_file_ids("") == frozenset()
    assert extract_file_ids("texto sem evidencias") == frozenset()


# ---------------------------------------------------------------------------
# compute_buckets
# ---------------------------------------------------------------------------


def test_compute_buckets_day_returns_existing(vault_db):
    obra = "OBRA_BD"
    _seed_day_narrative(vault_db, obra=obra, day="2026-04-08")
    _seed_day_narrative(vault_db, obra=obra, day="2026-04-09")
    buckets = compute_buckets(vault_db, obra, "day")
    assert len(buckets) == 2
    assert {b.scope_ref for b in buckets} == {"2026-04-08", "2026-04-09"}


def test_compute_buckets_week_groups_days_correctly(vault_db):
    obra = "OBRA_BW"
    # 7-13 abril 2026 = todos na ISO week 2026-W15
    _seed_day_narrative(vault_db, obra=obra, day="2026-04-08")
    _seed_day_narrative(vault_db, obra=obra, day="2026-04-09")
    _seed_day_narrative(vault_db, obra=obra, day="2026-04-15")  # W16
    buckets = compute_buckets(vault_db, obra, "week")
    refs = {b.scope_ref for b in buckets}
    assert "2026-W15" in refs
    assert "2026-W16" in refs


def test_compute_buckets_obra_overview_single_bucket(vault_db):
    obra = "OBRA_OV"
    _seed_day_narrative(vault_db, obra=obra, day="2026-04-01")
    _seed_day_narrative(vault_db, obra=obra, day="2026-04-30")
    buckets = compute_buckets(vault_db, obra, "obra_overview")
    assert len(buckets) == 1
    assert buckets[0].scope_ref == "all"


def test_compute_buckets_invalid_scope_raises(vault_db):
    with pytest.raises(ValueError):
        compute_buckets(vault_db, "OBRA_T", "fake_scope")


def test_compute_buckets_no_children_returns_empty(vault_db):
    """obra sem nenhuma day narrative → week buckets vazio."""
    buckets = compute_buckets(vault_db, "OBRA_EMPTY", "week")
    assert buckets == []


# ---------------------------------------------------------------------------
# compose_input_from_children
# ---------------------------------------------------------------------------


def test_compose_preserves_file_ids():
    children = [
        ChildNarrative(
            scope="day", scope_ref="2026-04-08",
            narrative_text="dia 1 cita f_aaa12 e m_bbbb22",
            file_ids=frozenset({"f_aaa12", "m_bbbb22"}),
        ),
        ChildNarrative(
            scope="day", scope_ref="2026-04-09",
            narrative_text="dia 2 cita f_ccc34",
            file_ids=frozenset({"f_ccc34"}),
        ),
    ]
    input_text = compose_input_from_children(
        children, parent_scope="week", bucket_label="2026-W15",
    )
    assert "Período: 2026-W15 (week)" in input_text
    assert "Narrativas filhas (2 de day)" in input_text
    assert "## 2026-04-08" in input_text
    assert "## 2026-04-09" in input_text
    # file_ids são listados em "Evidências citadas"
    assert "f_aaa12" in input_text
    assert "m_bbbb22" in input_text
    assert "f_ccc34" in input_text


def test_compose_no_children_returns_placeholder():
    input_text = compose_input_from_children(
        [], parent_scope="week", bucket_label="2026-W14",
    )
    assert "sem narrativas filhas" in input_text


# ---------------------------------------------------------------------------
# fetch_child_narratives
# ---------------------------------------------------------------------------


def test_fetch_child_narratives_filters_by_bucket(vault_db):
    obra = "OBRA_F"
    _seed_day_narrative(
        vault_db, obra=obra, day="2026-04-08",
        narrative="dia 8", file_ids=("f_aa12345",),
    )
    _seed_day_narrative(
        vault_db, obra=obra, day="2026-04-15",
        narrative="dia 15", file_ids=("f_bb67890",),
    )

    bucket = TimeBucket(
        scope="week", scope_ref="2026-W15",
        start=date(2026, 4, 6), end=date(2026, 4, 12),
    )
    children = fetch_child_narratives(vault_db, obra, "day", bucket=bucket)
    assert len(children) == 1
    assert children[0].scope_ref == "2026-04-08"
    assert "f_aa12345" in children[0].file_ids


# ---------------------------------------------------------------------------
# narrate_hierarchy (cascade integrado com mock narrate_fn)
# ---------------------------------------------------------------------------


def test_narrate_hierarchy_cascade_calls_levels_in_order(vault_db):
    obra = "OBRA_CAS"
    # 3 days em semanas ISO diferentes
    _seed_day_narrative(vault_db, obra=obra, day="2026-04-08",
                        file_ids=("f_aa11111",))
    _seed_day_narrative(vault_db, obra=obra, day="2026-04-15",
                        file_ids=("f_bb22222",))
    _seed_day_narrative(vault_db, obra=obra, day="2026-04-22",
                        file_ids=("f_cc33333",))

    calls: list[tuple[str, str]] = []

    def fake_narrate(dossier, conn):
        # Persist como se a narrativa real tivesse sido feita
        conn.execute(
            "INSERT INTO forensic_narratives "
            "(obra, scope, scope_ref, narrative_text, dossier_hash, "
            " model_used, prompt_version, events_count, confidence, "
            " created_at) "
            "VALUES (?, ?, ?, ?, ?, 'sonnet-4', 'v1', 1, 0.9, ?)",
            (
                dossier["obra"], dossier["scope"], dossier["scope_ref"],
                dossier.get("input_from_children", "")[:200],
                f"h_{dossier['scope']}_{dossier['scope_ref']}",
                "2026-04-25T00:00:00Z",
            ),
        )
        conn.commit()
        calls.append((dossier["scope"], dossier["scope_ref"]))

    counts = narrate_hierarchy(
        vault_db, obra, end_scope="obra_overview",
        skip_existing=True, narrate_fn=fake_narrate,
    )

    # week, month e obra_overview foram criados (quarter pulado por
    # span pequeno: 14 dias < 90)
    assert counts.get("week", 0) >= 1
    assert counts.get("month", 0) >= 1
    assert counts.get("quarter", 0) == 0  # corpus pequeno
    assert counts.get("obra_overview", 0) == 1

    # Calls em ordem: week → month → obra_overview
    seen_scopes = [c[0] for c in calls]
    week_idx = seen_scopes.index("week") if "week" in seen_scopes else -1
    month_idx = seen_scopes.index("month") if "month" in seen_scopes else -1
    overview_idx = seen_scopes.index("obra_overview")
    assert week_idx < month_idx < overview_idx


def test_narrate_hierarchy_skip_existing(vault_db):
    obra = "OBRA_SKIP"
    _seed_day_narrative(vault_db, obra=obra, day="2026-04-08")
    # Pre-cria week narrative pra forçar skip
    vault_db.execute(
        "INSERT INTO forensic_narratives "
        "(obra, scope, scope_ref, narrative_text, dossier_hash, "
        " model_used, prompt_version, events_count, confidence, "
        " created_at) "
        "VALUES (?, 'week', '2026-W15', 'pre-existing', 'h_w', 'sonnet', "
        "'v1', 0, 0.9, '2026-04-25T00:00:00Z')",
        (obra,),
    )
    vault_db.commit()

    calls = []
    def fake(d, conn):
        calls.append((d["scope"], d["scope_ref"]))

    narrate_hierarchy(
        vault_db, obra, end_scope="week",
        skip_existing=True, narrate_fn=fake,
    )
    # Nada de week foi narrado (já existia)
    assert ("week", "2026-W15") not in calls


def test_narrate_hierarchy_invalid_end_scope(vault_db):
    with pytest.raises(ValueError, match="end_scope"):
        narrate_hierarchy(vault_db, "X", end_scope="adversarial")
