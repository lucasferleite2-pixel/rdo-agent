"""Testes do PipelineStateManager — Sessao 6, divida #44.

Wrapper sobre a tabela ``tasks`` (orchestrator). Testes operam em DB
inicializado via ``init_db`` para reusar schema real.
"""

from __future__ import annotations

import sqlite3

import pytest

from rdo_agent.orchestrator import (
    Task,
    TaskStatus,
    TaskType,
    enqueue,
    init_db,
    mark_running,
)
from rdo_agent.pipeline_state import PipelineStateManager, StatusReport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "vault")


def _new_task(
    *, task_type: TaskType, obra: str = "OBRA_T",
    payload: dict | None = None, depends_on: list[int] | None = None,
    priority: int = 0,
) -> Task:
    from datetime import UTC, datetime
    return Task(
        id=None,
        task_type=task_type,
        payload=payload or {},
        status=TaskStatus.PENDING,
        depends_on=depends_on or [],
        obra=obra,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        priority=priority,
    )


def _seed_tasks(conn: sqlite3.Connection, obra: str = "OBRA_T") -> dict[str, int]:
    """Cria mistura de tasks em vários estados. Retorna ids por type."""
    ids: dict[str, int] = {}
    # 2 transcribe (1 done + 1 pending)
    ids["transcribe_done"] = enqueue(
        conn, _new_task(task_type=TaskType.TRANSCRIBE, obra=obra),
    )
    ids["transcribe_pending"] = enqueue(
        conn, _new_task(task_type=TaskType.TRANSCRIBE, obra=obra),
    )
    # 1 classify pending
    ids["classify_pending"] = enqueue(
        conn, _new_task(task_type=TaskType.CLASSIFY, obra=obra),
    )
    # 1 visual_analysis failed
    ids["vision_failed"] = enqueue(
        conn, _new_task(task_type=TaskType.VISUAL_ANALYSIS, obra=obra),
    )
    return ids


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------


def test_status_aggregates_by_type_and_status(conn):
    obra = "OBRA_AGG"
    ids = _seed_tasks(conn, obra=obra)
    # Mover transcribe_done -> done; vision_failed -> failed
    from rdo_agent.orchestrator import mark_done, mark_failed
    mark_running(conn, ids["transcribe_done"])
    mark_done(conn, ids["transcribe_done"])
    mark_running(conn, ids["vision_failed"])
    mark_failed(conn, ids["vision_failed"], "test failure")
    conn.commit()

    manager = PipelineStateManager(conn)
    report = manager.status(obra)

    assert isinstance(report, StatusReport)
    assert report.obra == obra
    assert report.counts["transcribe"]["done"] == 1
    assert report.counts["transcribe"]["pending"] == 1
    assert report.counts["classify"]["pending"] == 1
    assert report.counts["visual_analysis"]["failed"] == 1
    assert report.totals_by_status["pending"] == 2
    assert report.totals_by_status["done"] == 1
    assert report.totals_by_status["failed"] == 1


def test_status_isolates_by_obra(conn):
    _seed_tasks(conn, obra="OBRA_A")
    _seed_tasks(conn, obra="OBRA_B")

    manager = PipelineStateManager(conn)
    rep_a = manager.status("OBRA_A")
    rep_b = manager.status("OBRA_B")
    rep_c = manager.status("OBRA_INEXISTENTE")

    # Cada obra tem 4 tasks (todas pending no seed); nao se misturam
    assert rep_a.totals_by_status["pending"] == 4
    assert rep_b.totals_by_status["pending"] == 4
    assert rep_c.counts == {}
    assert rep_c.totals_by_status == {}


def test_status_has_helpers(conn):
    obra = "OBRA_HELPERS"
    ids = _seed_tasks(conn, obra=obra)
    from rdo_agent.orchestrator import mark_failed
    mark_running(conn, ids["vision_failed"])
    mark_failed(conn, ids["vision_failed"], "x")
    conn.commit()

    rep = PipelineStateManager(conn).status(obra)
    assert rep.has_pending is True
    assert rep.has_failures is True
    assert rep.has_resumable is False  # nenhuma running com finished_at NULL


# ---------------------------------------------------------------------------
# resumable_state() / reset_running()
# ---------------------------------------------------------------------------


def test_resumable_state_detects_crashed_running_tasks(conn):
    obra = "OBRA_CRASH"
    ids = _seed_tasks(conn, obra=obra)
    # Simular crash: marca running mas nao finalize
    mark_running(conn, ids["transcribe_pending"])
    conn.commit()

    manager = PipelineStateManager(conn)
    resumable = manager.resumable_state(obra)
    assert len(resumable) == 1
    assert resumable[0].id == ids["transcribe_pending"]
    assert resumable[0].status is TaskStatus.RUNNING
    assert resumable[0].finished_at in (None, "")


def test_reset_running_returns_running_to_pending(conn):
    obra = "OBRA_RESET"
    ids = _seed_tasks(conn, obra=obra)
    # 2 tasks "rodando" sem finalizar
    mark_running(conn, ids["transcribe_pending"])
    mark_running(conn, ids["classify_pending"])
    conn.commit()

    manager = PipelineStateManager(conn)
    before = manager.status(obra)
    assert before.totals_by_status["running"] == 2

    n = manager.reset_running(obra)
    assert n == 2

    after = manager.status(obra)
    assert after.totals_by_status.get("running", 0) == 0
    # Voltam pra pending (eram 2 originais + 2 que estavam rodando = 4)
    assert after.totals_by_status["pending"] == 4
    # nao ha resumable depois do reset
    assert after.has_resumable is False


def test_reset_running_does_not_touch_done_or_failed(conn):
    obra = "OBRA_PRESERVE"
    ids = _seed_tasks(conn, obra=obra)
    from rdo_agent.orchestrator import mark_done, mark_failed
    mark_running(conn, ids["transcribe_done"])
    mark_done(conn, ids["transcribe_done"])
    mark_running(conn, ids["vision_failed"])
    mark_failed(conn, ids["vision_failed"], "x")
    # Uma task de fato "rodando" sem finalize
    mark_running(conn, ids["classify_pending"])
    conn.commit()

    manager = PipelineStateManager(conn)
    n = manager.reset_running(obra)
    assert n == 1  # so a "rodando" eh resetada

    after = manager.status(obra)
    assert after.counts["transcribe"]["done"] == 1
    assert after.counts["visual_analysis"]["failed"] == 1


# ---------------------------------------------------------------------------
# claim()
# ---------------------------------------------------------------------------


def test_claim_returns_pending_and_marks_running(conn):
    obra = "OBRA_CLAIM"
    _seed_tasks(conn, obra=obra)
    manager = PipelineStateManager(conn)

    claimed = manager.claim(obra)
    assert claimed is not None
    assert claimed.status is TaskStatus.RUNNING

    # No DB tambem deve estar running
    row = conn.execute(
        "SELECT status FROM tasks WHERE id = ?", (claimed.id,),
    ).fetchone()
    assert row["status"] == "running"


def test_claim_with_task_type_filters(conn):
    obra = "OBRA_TYPED"
    _seed_tasks(conn, obra=obra)
    manager = PipelineStateManager(conn)

    claimed = manager.claim(obra, task_type=TaskType.CLASSIFY)
    assert claimed is not None
    assert claimed.task_type is TaskType.CLASSIFY


def test_claim_returns_none_when_empty(conn):
    obra = "OBRA_EMPTY"
    manager = PipelineStateManager(conn)
    assert manager.claim(obra) is None


def test_claim_does_not_double_claim_same_task(conn):
    obra = "OBRA_ATOMIC"
    enqueue(conn, _new_task(task_type=TaskType.TRANSCRIBE, obra=obra))
    manager = PipelineStateManager(conn)

    first = manager.claim(obra)
    second = manager.claim(obra)
    assert first is not None
    assert second is None  # nao ha mais pending


# ---------------------------------------------------------------------------
# complete() / fail() / reset_failed()
# ---------------------------------------------------------------------------


def test_complete_marks_done(conn):
    obra = "OBRA_COMPLETE"
    enqueue(conn, _new_task(task_type=TaskType.TRANSCRIBE, obra=obra))
    manager = PipelineStateManager(conn)
    claimed = manager.claim(obra)
    assert claimed is not None

    manager.complete(claimed.id, result_ref="audio_42.wav")  # type: ignore[arg-type]
    row = conn.execute(
        "SELECT status, result_ref, finished_at FROM tasks WHERE id = ?",
        (claimed.id,),
    ).fetchone()
    assert row["status"] == "done"
    assert row["result_ref"] == "audio_42.wav"
    assert row["finished_at"] is not None


def test_fail_marks_failed_with_error_msg(conn):
    obra = "OBRA_FAIL"
    enqueue(conn, _new_task(task_type=TaskType.TRANSCRIBE, obra=obra))
    manager = PipelineStateManager(conn)
    claimed = manager.claim(obra)
    assert claimed is not None

    manager.fail(claimed.id, "API timeout 504")  # type: ignore[arg-type]
    row = conn.execute(
        "SELECT status, error_message FROM tasks WHERE id = ?",
        (claimed.id,),
    ).fetchone()
    assert row["status"] == "failed"
    assert "504" in row["error_message"]


def test_reset_failed_returns_failed_to_pending(conn):
    obra = "OBRA_RETRY"
    ids = _seed_tasks(conn, obra=obra)
    from rdo_agent.orchestrator import mark_failed
    mark_running(conn, ids["vision_failed"])
    mark_failed(conn, ids["vision_failed"], "transient")
    conn.commit()

    manager = PipelineStateManager(conn)
    n = manager.reset_failed(obra)
    assert n == 1
    row = conn.execute(
        "SELECT status, started_at, finished_at FROM tasks WHERE id = ?",
        (ids["vision_failed"],),
    ).fetchone()
    assert row["status"] == "pending"
    assert row["started_at"] is None
    assert row["finished_at"] is None


def test_reset_failed_with_task_type_filters(conn):
    obra = "OBRA_RETRY_TYPED"
    # 1 transcribe failed + 1 classify failed
    t_id = enqueue(conn, _new_task(task_type=TaskType.TRANSCRIBE, obra=obra))
    c_id = enqueue(conn, _new_task(task_type=TaskType.CLASSIFY, obra=obra))
    from rdo_agent.orchestrator import mark_failed
    for tid in (t_id, c_id):
        mark_running(conn, tid)
        mark_failed(conn, tid, "boom")
    conn.commit()

    manager = PipelineStateManager(conn)
    n = manager.reset_failed(obra, task_type=TaskType.TRANSCRIBE)
    assert n == 1

    row_t = conn.execute(
        "SELECT status FROM tasks WHERE id = ?", (t_id,),
    ).fetchone()
    row_c = conn.execute(
        "SELECT status FROM tasks WHERE id = ?", (c_id,),
    ).fetchone()
    assert row_t["status"] == "pending"   # resetado
    assert row_c["status"] == "failed"    # nao tocado
