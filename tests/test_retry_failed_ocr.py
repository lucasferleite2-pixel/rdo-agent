"""Testes do script retry_failed_ocr — Sprint 4 Op11 Divida #12."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from rdo_agent.orchestrator import init_db

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import retry_failed_ocr as retry_mod  # noqa: E402


@pytest.fixture
def db_with_sentinel(tmp_path) -> sqlite3.Connection:
    conn = init_db(tmp_path)
    now = "2026-04-22T00:00:00Z"
    # Imagem fonte
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type,
        sha256, size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f_img_1", "OBRA_R", "10_media/img.jpg", "image", "a"*64,
         1000, "analyzed", now),
    )
    # JSON derivado
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type,
        sha256, size_bytes, derived_from, derivation_method,
        semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f_json_1", "OBRA_R", "30_visual/a.json", "text", "b"*64,
         200, "f_img_1", "gpt-4o vision", "analyzed", now),
    )
    # Visual_analysis com sentinel (malformed JSON marker)
    sentinel_json = json.dumps({
        "_sentinel": "malformed_json_response",
        "reason": "json_decode_error:Unterminated string",
        "atividade_em_curso": "não identificado",
        "elementos_construtivos": "não identificado",
        "condicoes_ambiente": "não identificado",
        "observacoes_tecnicas": "sentinel: erro",
    })
    conn.execute(
        """INSERT INTO visual_analyses (obra, file_id, analysis_json,
        confidence, api_call_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        ("OBRA_R", "f_json_1", sentinel_json, 0.0, None, now),
    )
    conn.commit()
    return conn


@pytest.fixture
def db_with_valid_analysis(tmp_path) -> sqlite3.Connection:
    conn = init_db(tmp_path)
    now = "2026-04-22T00:00:00Z"
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type,
        sha256, size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f_img_ok", "OBRA_R", "10_media/ok.jpg", "image", "o"*64,
         1000, "analyzed", now),
    )
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type,
        sha256, size_bytes, derived_from, derivation_method,
        semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f_json_ok", "OBRA_R", "30_visual/ok.json", "text", "j"*64,
         200, "f_img_ok", "gpt-4o vision", "analyzed", now),
    )
    valid = json.dumps({
        "atividade_em_curso": "medicao",
        "elementos_construtivos": "tubo",
        "condicoes_ambiente": "ok",
        "observacoes_tecnicas": "ok",
    })
    conn.execute(
        """INSERT INTO visual_analyses (obra, file_id, analysis_json,
        confidence, api_call_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        ("OBRA_R", "f_json_ok", valid, 1.0, None, now),
    )
    conn.commit()
    return conn


def test_find_suspect_detects_sentinel(db_with_sentinel):
    suspects = retry_mod._find_suspect_analyses(db_with_sentinel, "OBRA_R")
    assert len(suspects) == 1
    assert suspects[0]["json_fid"] == "f_json_1"
    assert suspects[0]["source_fid"] == "f_img_1"


def test_find_suspect_ignores_valid_analyses(db_with_valid_analysis):
    suspects = retry_mod._find_suspect_analyses(
        db_with_valid_analysis, "OBRA_R",
    )
    assert suspects == []


def test_retry_dry_run_no_tasks_created(db_with_sentinel):
    result = retry_mod.retry(db_with_sentinel, "OBRA_R", dry_run=True)
    assert result["suspeitas"] == 1
    assert result["enqueued"] == 1  # counted mesmo em dry-run
    tasks = db_with_sentinel.execute(
        "SELECT COUNT(*) FROM tasks WHERE task_type='ocr_first'"
    ).fetchone()[0]
    assert tasks == 0  # nao criou


def test_retry_live_enqueues_ocr_first(db_with_sentinel):
    result = retry_mod.retry(db_with_sentinel, "OBRA_R", dry_run=False)
    assert result["enqueued"] == 1
    tasks = db_with_sentinel.execute(
        """SELECT payload FROM tasks WHERE task_type='ocr_first'
           AND status='pending'"""
    ).fetchone()
    assert tasks is not None
    payload = json.loads(tasks[0])
    assert payload["file_id"] == "f_img_1"


def test_retry_skips_if_already_pending(db_with_sentinel):
    """Se ja ha OCR_FIRST pending pra mesma imagem, pula."""
    from rdo_agent.orchestrator import Task, TaskStatus, TaskType, enqueue
    enqueue(
        db_with_sentinel,
        Task(id=None, task_type=TaskType.OCR_FIRST,
             payload={"file_id": "f_img_1", "file_path": "10_media/img.jpg"},
             status=TaskStatus.PENDING, depends_on=[],
             obra="OBRA_R", created_at=""),
    )
    result = retry_mod.retry(db_with_sentinel, "OBRA_R", dry_run=False)
    assert result["enqueued"] == 0
    assert result["skipped_already_pending"] == 1


def test_retry_skips_suspects_without_source(db_with_sentinel):
    """Se o JSON file eh orphan (sem derived_from), nao tem source_fid —
    nao da pra enfileirar retry."""
    # Insere outro sentinel sem files row pra o JSON file_id
    db_with_sentinel.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type,
        sha256, size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f_json_orphan", "OBRA_R", "30_visual/orphan.json", "text",
         "p"*64, 200, "analyzed", "2026-04-22T00:00:00Z"),
    )
    db_with_sentinel.execute(
        """INSERT INTO visual_analyses (obra, file_id, analysis_json,
        confidence, api_call_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        ("OBRA_R", "f_json_orphan",
         '{"_sentinel":"orphan","reason":"whatever"}',
         0.0, None, "2026-04-22T00:00:00Z"),
    )
    db_with_sentinel.commit()

    result = retry_mod.retry(db_with_sentinel, "OBRA_R", dry_run=False)
    # Um suspect eh o f_json_1 (OK), outro orphan (skipped)
    assert result["suspeitas"] == 2
    assert result["skipped_no_source"] == 1
    assert result["enqueued"] == 1
