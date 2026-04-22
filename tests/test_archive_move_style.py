"""Testes Sprint 4 Op11 Divida #10 — archive move-style com superseded_by.

Cobrem:
  - Migration idempotente adiciona colunas superseded_by + superseded_at
  - View visual_analyses_active filtra rows com superseded_by NULL
  - visual_analysis_handler: nova row V2 marca rows antigas como superseded
  - Backfill script: processamentos retroativos Op9 recebem superseded_by
  - Idempotencia: backfill 2x nao sobrescreve superseded_by ja preenchido
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from rdo_agent.orchestrator import (
    _migrate_superseded_by_sprint4_op11,
    init_db,
)

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import backfill_superseded_by as backfill_mod  # noqa: E402


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path)


# ---------------------------------------------------------------------------
# Migration + view
# ---------------------------------------------------------------------------


def test_migration_adds_superseded_columns(db):
    cols = {r["name"] for r in db.execute(
        "PRAGMA table_info(visual_analyses)"
    ).fetchall()}
    assert "superseded_by" in cols
    assert "superseded_at" in cols


def test_migration_idempotent_twice(db):
    _migrate_superseded_by_sprint4_op11(db)
    _migrate_superseded_by_sprint4_op11(db)
    cols = {r["name"] for r in db.execute(
        "PRAGMA table_info(visual_analyses)"
    ).fetchall()}
    assert "superseded_by" in cols


def test_view_active_filters_superseded(db):
    _seed_image_and_va(db, "OBRA_VA", "img_1", "json_v1",
                       analysis='{"x":1}', superseded_by=None)
    _seed_image_and_va(db, "OBRA_VA", None, "json_v2",
                       analysis='{"x":2}', superseded_by=None,
                       image_fid_override="img_1")
    # Mark v1 as superseded by v2 manually
    db.execute(
        "UPDATE visual_analyses SET superseded_by = ("
        "SELECT id FROM visual_analyses WHERE file_id='json_v2') "
        "WHERE file_id='json_v1'"
    )
    db.commit()

    active = db.execute(
        "SELECT file_id FROM visual_analyses_active WHERE obra='OBRA_VA'"
    ).fetchall()
    active_fids = {r["file_id"] for r in active}
    assert "json_v1" not in active_fids  # superseded, escondida
    assert "json_v2" in active_fids  # ativa

    all_rows = db.execute(
        "SELECT file_id FROM visual_analyses WHERE obra='OBRA_VA'"
    ).fetchall()
    assert len(all_rows) == 2  # ambas preservadas na tabela base


# ---------------------------------------------------------------------------
# Handler: insert V2 marca V1 como superseded
# ---------------------------------------------------------------------------


def _seed_image_and_va(
    conn: sqlite3.Connection,
    obra: str,
    image_fid: str | None,
    json_fid: str,
    *,
    analysis: str = '{"x":"y"}',
    superseded_by: int | None = None,
    image_fid_override: str | None = None,
    created_at: str = "2026-04-20T00:00:00Z",
):
    """
    Seed files + visual_analysis.
    Se image_fid_override passado, reusa imagem fonte existente.
    """
    actual_image = image_fid_override or image_fid
    if image_fid:  # insert image row
        conn.execute(
            """INSERT INTO files (file_id, obra, file_path, file_type,
            sha256, size_bytes, semantic_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (image_fid, obra, f"10_media/{image_fid}.jpg", "image",
             ("i"*63 + image_fid[-1]), 1000, "analyzed", created_at),
        )
    # Insert JSON derivado from image
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type,
        sha256, size_bytes, derived_from, derivation_method,
        semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (json_fid, obra, f"30_visual/{json_fid}.json", "text",
         ("j"*63 + json_fid[-1]), 500, actual_image, "gpt-4o vision",
         "analyzed", created_at),
    )
    conn.execute(
        """INSERT INTO visual_analyses (obra, file_id, analysis_json,
        confidence, api_call_id, created_at, superseded_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (obra, json_fid, analysis, 1.0, None, created_at, superseded_by),
    )
    conn.commit()


def test_visual_handler_insert_marks_old_rows_superseded(
    tmp_path, monkeypatch,
):
    """Quando handler cria nova row V2, rows antigas ganham superseded_by."""
    from PIL import Image

    from rdo_agent import visual_analyzer
    from rdo_agent.orchestrator import Task, TaskStatus, TaskType
    from rdo_agent.utils import config
    from rdo_agent.utils.hashing import sha256_file
    from rdo_agent.visual_analyzer import visual_analysis_handler

    root = tmp_path / "vaults"
    obra = "OBRA_HANDLER"
    vault = root / obra
    (vault / "10_media").mkdir(parents=True)
    img = vault / "10_media" / "img.jpg"
    Image.new("RGB", (64, 64), (100, 100, 100)).save(img, "JPEG")

    settings = config.Settings(
        openai_api_key="sk-test-dummy", anthropic_api_key="",
        claude_model="claude-sonnet-4-6", vaults_root=root,
        log_level="WARNING", dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)

    conn = init_db(vault)
    sha = sha256_file(img)
    image_fid = f"f_{sha[:12]}"
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, timestamp_resolved, timestamp_source, semantic_status,
        created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (image_fid, obra, "10_media/img.jpg", "image", sha,
         img.stat().st_size, "2026-04-15T00:00:00+00:00",
         "filename", "awaiting_visual_analysis", "2026-04-20T00:00:00Z"),
    )
    # Insere uma ANALISE OLD existente (simulando pre-reprocess)
    # cuja JSON file deriva da mesma imagem
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, derived_from, derivation_method, semantic_status,
        created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f_old_json", obra, "30_visual/old.json", "text",
         "o"*64, 100, image_fid, "gpt-4o-mini vision (V1)",
         "analyzed", "2026-04-20T00:00:00Z"),
    )
    conn.execute(
        """INSERT INTO visual_analyses (obra, file_id, analysis_json,
        confidence, api_call_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (obra, "f_old_json", '{"old": "v1"}', 1.0, None,
         "2026-04-20T00:00:00Z"),
    )
    conn.commit()

    # Installa FakeClient com response V2 valida
    class _FM:
        def __init__(self, c): self.content = c
    class _FC:
        def __init__(self, c): self.message = _FM(c)
    class _FU:
        def __init__(self):
            self.prompt_tokens = 1200
            self.completion_tokens = 300
    class _FCompl:
        def __init__(self, c):
            self._c = c; self._u = _FU()
        def model_dump(self):
            return {"choices":[{"message":{"content": self._c,"role":"assistant"}}],
                    "usage":{"prompt_tokens":1200,"completion_tokens":300,
                             "total_tokens":1500}}
    class _FCo:
        def __init__(self, q): self._q=list(q); self.calls=[]
        def create(self,**kw):
            self.calls.append(kw); return self._q.pop(0)
    class _FCh:
        def __init__(self, q): self.completions=_FCo(q)
    class _FCl:
        def __init__(self, q): self.chat=_FCh(q)

    v2_payload = {
        "elementos_construtivos": "estrutura metalica nova v2",
        "atividade_em_curso": "estrutura montada",
        "condicoes_ambiente": "canteiro externo",
        "observacoes_tecnicas": "reporte execucao valido",
        "categoria_sugerida": "reporte_execucao",
        "categorias_secundarias": [],
        "confidence": 0.9,
    }
    monkeypatch.setattr(
        visual_analyzer, "_get_openai_client",
        lambda: _FCl([_FCompl(json.dumps(v2_payload))]),
    )

    task = Task(
        id=None, task_type=TaskType.VISUAL_ANALYSIS,
        payload={"file_id": image_fid, "file_path": "10_media/img.jpg"},
        status=TaskStatus.PENDING, depends_on=[],
        obra=obra, created_at="",
    )
    new_json_fid = visual_analysis_handler(task, conn)

    # Row V1 antiga agora tem superseded_by preenchido
    v1_row = conn.execute(
        "SELECT superseded_by, superseded_at FROM visual_analyses "
        "WHERE file_id='f_old_json'"
    ).fetchone()
    assert v1_row["superseded_by"] is not None
    assert v1_row["superseded_at"] is not None

    # Row V2 nova eh ativa (superseded_by NULL)
    v2_row = conn.execute(
        "SELECT superseded_by FROM visual_analyses WHERE file_id = ?",
        (new_json_fid,),
    ).fetchone()
    assert v2_row["superseded_by"] is None

    # view active so retorna V2
    active = conn.execute(
        "SELECT file_id FROM visual_analyses_active WHERE obra=?",
        (obra,),
    ).fetchall()
    active_fids = {r["file_id"] for r in active}
    assert new_json_fid in active_fids
    assert "f_old_json" not in active_fids


# ---------------------------------------------------------------------------
# Backfill script
# ---------------------------------------------------------------------------


def test_backfill_marks_old_rows_superseded_by_newest(db):
    """Multiple analyses mesma imagem: mais antigas viram superseded
    pela mais nova."""
    _seed_image_and_va(db, "OBRA_BF", "img_1", "json_v1",
                       analysis='{"v":1}',
                       created_at="2026-04-20T00:00:00Z")
    _seed_image_and_va(db, "OBRA_BF", None, "json_v2",
                       analysis='{"v":2}',
                       image_fid_override="img_1",
                       created_at="2026-04-22T00:00:00Z")
    _seed_image_and_va(db, "OBRA_BF", None, "json_v3",
                       analysis='{"v":3}',
                       image_fid_override="img_1",
                       created_at="2026-04-23T00:00:00Z")

    result = backfill_mod.backfill(db, "OBRA_BF", dry_run=False)
    assert result["grupos_multi"] == 1
    assert result["rows_marcadas"] == 2  # v1 e v2 marcadas pela v3

    # Todas v1, v2 tem superseded_by == id da v3
    v3_id = db.execute(
        "SELECT id FROM visual_analyses WHERE file_id='json_v3'"
    ).fetchone()[0]
    v1 = db.execute(
        "SELECT superseded_by FROM visual_analyses WHERE file_id='json_v1'"
    ).fetchone()
    v2 = db.execute(
        "SELECT superseded_by FROM visual_analyses WHERE file_id='json_v2'"
    ).fetchone()
    assert v1["superseded_by"] == v3_id
    assert v2["superseded_by"] == v3_id


def test_backfill_idempotent_rerun(db):
    _seed_image_and_va(db, "OBRA_BF", "img_x", "json_x1",
                       analysis='{"v":1}',
                       created_at="2026-04-20T00:00:00Z")
    _seed_image_and_va(db, "OBRA_BF", None, "json_x2",
                       analysis='{"v":2}',
                       image_fid_override="img_x",
                       created_at="2026-04-22T00:00:00Z")

    r1 = backfill_mod.backfill(db, "OBRA_BF", dry_run=False)
    r2 = backfill_mod.backfill(db, "OBRA_BF", dry_run=False)
    assert r1["rows_marcadas"] == 1
    assert r2["rows_marcadas"] == 0  # ja marcada, pula


def test_backfill_dry_run_no_write(db):
    _seed_image_and_va(db, "OBRA_BF", "img_d", "json_d1",
                       analysis='{"v":1}',
                       created_at="2026-04-20T00:00:00Z")
    _seed_image_and_va(db, "OBRA_BF", None, "json_d2",
                       analysis='{"v":2}',
                       image_fid_override="img_d",
                       created_at="2026-04-22T00:00:00Z")

    result = backfill_mod.backfill(db, "OBRA_BF", dry_run=True)
    assert result["rows_marcadas"] == 1
    # DB nao foi modificado
    superseded = db.execute(
        "SELECT COUNT(*) FROM visual_analyses "
        "WHERE obra='OBRA_BF' AND superseded_by IS NOT NULL"
    ).fetchone()[0]
    assert superseded == 0


def test_backfill_skips_solo_analyses(db):
    """Imagens com so 1 analyse nao sao afetadas."""
    _seed_image_and_va(db, "OBRA_BF", "img_solo", "json_solo",
                       analysis='{"v":1}')
    result = backfill_mod.backfill(db, "OBRA_BF", dry_run=False)
    assert result["rows_marcadas"] == 0
