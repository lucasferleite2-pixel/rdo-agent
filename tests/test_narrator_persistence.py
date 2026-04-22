"""Testes persistence narrativas — Sprint 5 Fase A F5."""

from __future__ import annotations

import sqlite3

import pytest

from rdo_agent.forensic_agent.narrator import NarrationResult
from rdo_agent.forensic_agent.persistence import (
    _compute_filename,
    _find_existing_narrative,
    save_narrative,
)
from rdo_agent.orchestrator import init_db


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path)


def _sample_narration() -> NarrationResult:
    return NarrationResult(
        markdown_text="# Narrativa: X\n\nconteudo.\n\n---\n\n```json\n"
                      '{"self_assessment": {"confidence": 0.85}}\n```',
        markdown_body="# Narrativa: X\n\nconteudo.\n\n---",
        self_assessment={"confidence": 0.85},
        model="claude-sonnet-4-6",
        prompt_version="narrator_v1",
        api_call_id=None,
        cost_usd=0.01,
        prompt_tokens=500,
        completion_tokens=800,
    )


def _sample_validation() -> dict:
    return {
        "passed": True,
        "checks": {"valores_preservados": True},
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# _compute_filename
# ---------------------------------------------------------------------------


def test_compute_filename_day_with_ref():
    assert _compute_filename("day", "2026-04-06") == "day_2026-04-06.md"


def test_compute_filename_obra_overview():
    assert _compute_filename("obra_overview", None) == "obra_overview.md"


def test_compute_filename_fallback():
    assert _compute_filename("unknown_scope", None) == "unknown_scope_unknown.md"


# ---------------------------------------------------------------------------
# save_narrative — happy path
# ---------------------------------------------------------------------------


def test_save_creates_db_row_and_file(db, tmp_path):
    reports = tmp_path / "reports"
    narrative_id, path, was_cached = save_narrative(
        db, obra="OBRA_P", scope="day", scope_ref="2026-04-06",
        dossier_hash="abc123", narration=_sample_narration(),
        validation=_sample_validation(), events_count=12,
        reports_root=reports,
    )
    assert narrative_id > 0
    assert was_cached is False
    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("# Narrativa: X")

    # DB row
    row = db.execute(
        "SELECT obra, scope, scope_ref, dossier_hash, model_used, "
        "events_count, confidence FROM forensic_narratives WHERE id=?",
        (narrative_id,),
    ).fetchone()
    assert row["obra"] == "OBRA_P"
    assert row["scope"] == "day"
    assert row["scope_ref"] == "2026-04-06"
    assert row["dossier_hash"] == "abc123"
    assert row["model_used"] == "claude-sonnet-4-6"
    assert row["events_count"] == 12
    assert row["confidence"] == 0.85


def test_save_obra_overview_with_null_scope_ref(db, tmp_path):
    narrative_id, path, was_cached = save_narrative(
        db, obra="OBRA_O", scope="obra_overview", scope_ref=None,
        dossier_hash="hash_obra", narration=_sample_narration(),
        validation=_sample_validation(), events_count=50,
        reports_root=tmp_path / "reports",
    )
    assert was_cached is False
    assert path.name == "obra_overview.md"

    row = db.execute(
        "SELECT scope_ref FROM forensic_narratives WHERE id=?",
        (narrative_id,),
    ).fetchone()
    assert row["scope_ref"] is None


# ---------------------------------------------------------------------------
# Idempotencia via cache hit
# ---------------------------------------------------------------------------


def test_save_idempotent_via_cache_hit(db, tmp_path):
    """Mesma combinacao (obra, scope, scope_ref, dossier_hash) ja existe
    -> retorna id existente + was_cached=True sem criar nova row."""
    reports = tmp_path / "reports"
    id1, path1, cached1 = save_narrative(
        db, obra="OBRA_C", scope="day", scope_ref="2026-04-10",
        dossier_hash="h_equal", narration=_sample_narration(),
        validation=_sample_validation(), events_count=5,
        reports_root=reports,
    )
    id2, path2, cached2 = save_narrative(
        db, obra="OBRA_C", scope="day", scope_ref="2026-04-10",
        dossier_hash="h_equal", narration=_sample_narration(),
        validation=_sample_validation(), events_count=5,
        reports_root=reports,
    )
    assert cached1 is False
    assert cached2 is True
    assert id1 == id2
    # so 1 row em DB
    total = db.execute(
        "SELECT COUNT(*) FROM forensic_narratives WHERE obra='OBRA_C'"
    ).fetchone()[0]
    assert total == 1


def test_save_different_hashes_create_distinct_rows(db, tmp_path):
    reports = tmp_path / "reports"
    id1, _, c1 = save_narrative(
        db, obra="OBRA_H", scope="day", scope_ref="2026-04-06",
        dossier_hash="h1", narration=_sample_narration(),
        validation=_sample_validation(), events_count=3,
        reports_root=reports,
    )
    id2, _, c2 = save_narrative(
        db, obra="OBRA_H", scope="day", scope_ref="2026-04-06",
        dossier_hash="h2_different", narration=_sample_narration(),
        validation=_sample_validation(), events_count=3,
        reports_root=reports,
    )
    assert id1 != id2
    assert c1 is False and c2 is False


def test_find_existing_narrative_returns_none_when_absent(db):
    assert _find_existing_narrative(
        db, "OBRA_X", "day", "2026-04-06", "abc",
    ) is None


def test_find_existing_narrative_works_with_null_scope_ref(db, tmp_path):
    save_narrative(
        db, obra="OBRA_N", scope="obra_overview", scope_ref=None,
        dossier_hash="nulo", narration=_sample_narration(),
        validation=_sample_validation(), events_count=0,
        reports_root=tmp_path / "reports",
    )
    found = _find_existing_narrative(
        db, "OBRA_N", "obra_overview", None, "nulo",
    )
    assert found is not None


# ---------------------------------------------------------------------------
# Validation JSON persisted correctly
# ---------------------------------------------------------------------------


def test_save_persists_validation_checklist_json(db, tmp_path):
    import json as json_mod
    validation = {
        "passed": True,
        "checks": {"horarios_preservados": True, "valores_preservados": True},
        "warnings": ["nome X ausente"],
    }
    narrative_id, _, _ = save_narrative(
        db, obra="OBRA_V", scope="day", scope_ref="2026-04-06",
        dossier_hash="val", narration=_sample_narration(),
        validation=validation, events_count=5,
        reports_root=tmp_path / "reports",
    )
    row = db.execute(
        "SELECT validation_checklist_json FROM forensic_narratives WHERE id=?",
        (narrative_id,),
    ).fetchone()
    parsed = json_mod.loads(row["validation_checklist_json"])
    assert parsed["passed"] is True
    assert "nome X ausente" in parsed["warnings"]


# ---------------------------------------------------------------------------
# Dirs criados automaticamente
# ---------------------------------------------------------------------------


def test_save_creates_obra_dir_if_missing(db, tmp_path):
    reports = tmp_path / "brand_new_dir"
    assert not reports.exists()
    _, path, _ = save_narrative(
        db, obra="OBRA_D", scope="day", scope_ref="2026-04-06",
        dossier_hash="h", narration=_sample_narration(),
        validation=_sample_validation(), events_count=1,
        reports_root=reports,
    )
    assert reports.exists()
    assert (reports / "OBRA_D").is_dir()
    assert path.exists()
