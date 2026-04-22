"""Testes do script de reprocessamento OCR-first retroativo — Sprint 4 Op9 Fase 4.

Cobrem reprocess() sem rodar worker real:
  - dry-run nao escreve em DB
  - limit respeitado (processa so N)
  - archive row populada corretamente (archive_reason, archived_at)
  - tasks OCR_FIRST enfileiradas
  - idempotencia: re-rodar nao re-arquiva mesma row
  - visual_analyses sem files fonte sao puladas
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from rdo_agent.orchestrator import init_db

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import reprocess_visual_analyses_ocr_first as reprocess_mod  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture: DB com imagem original + visual_analyses JSON derivada
# ---------------------------------------------------------------------------


@pytest.fixture
def db_with_samples(tmp_path) -> sqlite3.Connection:
    conn = init_db(tmp_path)
    now = "2026-04-22T00:00:00Z"
    # 3 imagens + 3 visual_analyses associadas
    for i in range(1, 4):
        image_fid = f"f_img_{i:02d}"
        json_fid = f"f_json_{i:02d}"
        conn.execute(
            """INSERT INTO files (file_id, obra, file_path, file_type,
            sha256, size_bytes, semantic_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (image_fid, "OBRA_R", f"10_media/img{i}.jpg", "image",
             "a"*64, 1000, "analyzed", now),
        )
        conn.execute(
            """INSERT INTO files (file_id, obra, file_path, file_type,
            sha256, size_bytes, derived_from, derivation_method,
            semantic_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (json_fid, "OBRA_R", f"30_visual/img{i}.json", "text",
             ("b"+str(i))*32, 500, image_fid,
             "gpt-4o-mini vision", "analyzed", now),
        )
        conn.execute(
            """INSERT INTO visual_analyses (obra, file_id, analysis_json,
            confidence, api_call_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            ("OBRA_R", json_fid,
             json.dumps({"atividade_em_curso": f"analise {i}"}),
             1.0, None, now),
        )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_list_candidates_finds_all_three(db_with_samples):
    cands = reprocess_mod._list_candidates(db_with_samples, "OBRA_R")
    assert len(cands) == 3
    assert all(c["source_fid"].startswith("f_img_") for c in cands)


def test_dry_run_does_not_touch_db(db_with_samples):
    result = reprocess_mod.reprocess(
        db_with_samples, obra="OBRA_R", dry_run=True,
    )
    assert result["total_candidates"] == 3
    assert result["archived"] == 0
    assert result["tasks_enqueued"] == 0

    # Nada em archive
    arch_count = db_with_samples.execute(
        "SELECT COUNT(*) FROM visual_analyses_archive"
    ).fetchone()[0]
    assert arch_count == 0
    # Nada em tasks ocr_first
    task_count = db_with_samples.execute(
        "SELECT COUNT(*) FROM tasks WHERE task_type='ocr_first'"
    ).fetchone()[0]
    assert task_count == 0


def test_limit_processes_only_n(db_with_samples):
    result = reprocess_mod.reprocess(
        db_with_samples, obra="OBRA_R", dry_run=False, limit=2,
    )
    assert result["total_candidates"] == 2
    assert result["archived"] == 2
    assert result["tasks_enqueued"] == 2


def test_archive_row_populates_metadata(db_with_samples):
    reprocess_mod.reprocess(
        db_with_samples, obra="OBRA_R", dry_run=False, limit=1,
    )
    arch = db_with_samples.execute(
        """SELECT original_id, obra, file_id, analysis_json,
                  archived_at, archive_reason
           FROM visual_analyses_archive"""
    ).fetchone()
    assert arch is not None
    assert arch["obra"] == "OBRA_R"
    assert arch["file_id"].startswith("f_json_")
    assert arch["original_id"] >= 1
    assert arch["archive_reason"] == reprocess_mod.ARCHIVE_REASON
    assert arch["archived_at"].endswith("Z")
    assert "analise" in arch["analysis_json"]


def test_ocr_first_task_enqueued_with_correct_payload(db_with_samples):
    reprocess_mod.reprocess(
        db_with_samples, obra="OBRA_R", dry_run=False, limit=1,
    )
    task = db_with_samples.execute(
        "SELECT payload FROM tasks WHERE task_type='ocr_first' ORDER BY id LIMIT 1"
    ).fetchone()
    assert task is not None
    payload = json.loads(task["payload"])
    assert payload["file_id"].startswith("f_img_")
    assert payload["file_path"].endswith(".jpg")


def test_rerun_skips_already_archived(db_with_samples):
    """Segunda rodada NAO duplica archive (candidatos ja arquivados
    nao reaparecem na query)."""
    reprocess_mod.reprocess(
        db_with_samples, obra="OBRA_R", dry_run=False,
    )
    arch_count_1 = db_with_samples.execute(
        "SELECT COUNT(*) FROM visual_analyses_archive"
    ).fetchone()[0]

    r2 = reprocess_mod.reprocess(
        db_with_samples, obra="OBRA_R", dry_run=False,
    )
    arch_count_2 = db_with_samples.execute(
        "SELECT COUNT(*) FROM visual_analyses_archive"
    ).fetchone()[0]

    assert arch_count_2 == arch_count_1  # nao cresceu
    assert r2["total_candidates"] == 0  # nenhum candidato elegivel


def test_skips_analyses_without_source_files(db_with_samples):
    """visual_analyses cujo JSON files row nao tem derived_from eh pulada
    (query filtra f_src.file_id IS NOT NULL — sem imagem fonte definida).

    Cenario real: JSON em files sem derived_from preenchido (corrompido
    ou caminho excepcional). Inserimos 1 files row sem derived_from +
    visual_analyses associada. Como derived_from eh NULL, f_src join
    retorna NULL e a analyse eh filtrada.
    """
    # Files row sem derived_from (valido — coluna eh nullable)
    db_with_samples.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type,
        sha256, size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f_orphan_json", "OBRA_R", "30_visual/orphan.json", "text",
         "c"*64, 100, "analyzed", "2026-04-22T00:00:00Z"),
    )
    db_with_samples.execute(
        """INSERT INTO visual_analyses (obra, file_id, analysis_json,
        confidence, api_call_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        ("OBRA_R", "f_orphan_json",
         '{"atividade_em_curso": "orphan — sem fonte"}', 1.0, None,
         "2026-04-22T00:00:00Z"),
    )
    db_with_samples.commit()

    cands = reprocess_mod._list_candidates(db_with_samples, "OBRA_R")
    # Orphan foi filtrado (f_src.file_id IS NULL); os 3 originais permanecem
    analysis_fids = {c["analysis_fid"] for c in cands}
    assert "f_orphan_json" not in analysis_fids
    assert len(cands) == 3


def test_enqueue_idempotent_when_task_pending(db_with_samples):
    """Se task OCR_FIRST ja esta pending/running/done, nao re-enfileira."""
    from rdo_agent.orchestrator import Task, TaskStatus, TaskType, enqueue
    # Cria task pending manualmente
    enqueue(
        db_with_samples,
        Task(id=None, task_type=TaskType.OCR_FIRST,
             payload={"file_id": "f_img_01", "file_path": "x.jpg"},
             status=TaskStatus.PENDING, depends_on=[],
             obra="OBRA_R", created_at=""),
    )
    result = reprocess_mod.reprocess(
        db_with_samples, obra="OBRA_R", dry_run=False, limit=1,
    )
    # archived = 1 mas tasks_enqueued depende — a funcao retorna task id
    # se ja existe. Total de tasks ocr_first deve ser 1 (o que ja tinha).
    task_count = db_with_samples.execute(
        "SELECT COUNT(*) FROM tasks WHERE task_type='ocr_first'"
    ).fetchone()[0]
    assert task_count == 1


def test_report_rendering_contains_summary():
    mock_result = {
        "obra": "OBRA_TEST",
        "dry_run": True,
        "total_candidates": 44,
        "archived": 0,
        "tasks_enqueued": 0,
        "tasks_already_exist": 0,
        "errors": [],
        "candidates_summary": [
            {"original_id": 1, "analysis_fid": "f_json_1",
             "source_fid": "f_img_1", "source_path": "10_media/x.jpg",
             "created_at": "2026-04-22T00:00:00Z"},
        ],
        "cost_before": 0.5,
    }
    report = reprocess_mod.render_report(mock_result, None, cost_after=0.5)
    assert "# Op9 Reprocessing Report" in report
    assert "OBRA_TEST" in report
    assert "DRY-RUN" in report
    assert "Candidatos listados: **44**" in report
    assert "Custo: antes US$ 0.5000" in report
