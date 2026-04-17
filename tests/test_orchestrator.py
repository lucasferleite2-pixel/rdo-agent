"""Testes do orchestrator — schema SQLite, CRUD da fila e loop do worker."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rdo_agent.orchestrator import (
    DB_FILENAME,
    Task,
    TaskStatus,
    TaskType,
    enqueue,
    init_db,
    mark_done,
    mark_failed,
    mark_running,
    next_pending,
    run_worker,
)

# Conjunto completo do Blueprint §7.2.
EXPECTED_TABLES = {
    "tasks",
    "messages",
    "files",
    "media_derivations",
    "api_calls",
    "transcriptions",
    "visual_analyses",
    "events",
    "clusters",
}


def _make_task(obra: str = "X", deps: list[int] | None = None, priority: int = 0) -> Task:
    return Task(
        id=None,
        task_type=TaskType.INGEST,
        payload={"foo": "bar"},
        status=TaskStatus.PENDING,
        depends_on=deps or [],
        obra=obra,
        created_at="",
        priority=priority,
    )


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


def test_init_db_creates_all_tables(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert EXPECTED_TABLES.issubset(tables)
    assert (tmp_path / DB_FILENAME).exists()


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    conn1 = init_db(tmp_path)
    enqueue(conn1, _make_task())
    conn1.close()

    # Segunda chamada não deve destruir dados nem falhar.
    conn2 = init_db(tmp_path)
    count = conn2.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert count == 1


def test_init_db_applies_pragmas(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


# ---------------------------------------------------------------------------
# enqueue + next_pending
# ---------------------------------------------------------------------------


def test_enqueue_returns_id_and_updates_task(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    task = _make_task()
    assert task.id is None

    task_id = enqueue(conn, task)
    assert task_id > 0
    assert task.id == task_id
    assert task.created_at  # preenchido por _now_iso


def test_next_pending_returns_first_eligible(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    id1 = enqueue(conn, _make_task())
    enqueue(conn, _make_task())

    nxt = next_pending(conn, "X")
    assert nxt is not None
    assert nxt.id == id1


def test_next_pending_blocks_on_unresolved_dependency(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    id1 = enqueue(conn, _make_task())
    id2 = enqueue(conn, _make_task(deps=[id1]))

    # id1 ainda pending → id2 bloqueada
    first = next_pending(conn, "X")
    assert first is not None and first.id == id1

    mark_running(conn, id1)
    assert next_pending(conn, "X") is None  # id2 espera done, não running

    mark_done(conn, id1)
    after = next_pending(conn, "X")
    assert after is not None and after.id == id2


def test_next_pending_respects_priority(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    # Enfileira priority=0 primeiro, depois priority=10 — ordem por priority vence.
    id_low = enqueue(conn, _make_task(priority=0))
    id_high = enqueue(conn, _make_task(priority=10))

    nxt = next_pending(conn, "X")
    assert nxt is not None
    assert nxt.id == id_high
    assert nxt.id != id_low


def test_next_pending_isolates_by_obra(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    enqueue(conn, _make_task(obra="A"))
    id_b = enqueue(conn, _make_task(obra="B"))

    nxt_b = next_pending(conn, "B")
    assert nxt_b is not None and nxt_b.id == id_b

    # "C" não tem nada
    assert next_pending(conn, "C") is None


# ---------------------------------------------------------------------------
# mark_* helpers
# ---------------------------------------------------------------------------


def test_mark_failed_preserves_error_message(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    task_id = enqueue(conn, _make_task())

    mark_failed(conn, task_id, "boom: arquivo corrompido")
    row = conn.execute(
        "SELECT status, error_message, finished_at FROM tasks WHERE id=?", (task_id,)
    ).fetchone()
    assert row["status"] == "failed"
    assert row["error_message"] == "boom: arquivo corrompido"
    assert row["finished_at"] is not None


def test_mark_done_sets_result_ref(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    task_id = enqueue(conn, _make_task())

    mark_done(conn, task_id, result_ref="f_abc123")
    row = conn.execute(
        "SELECT status, result_ref, finished_at FROM tasks WHERE id=?", (task_id,)
    ).fetchone()
    assert row["status"] == "done"
    assert row["result_ref"] == "f_abc123"
    assert row["finished_at"] is not None


# ---------------------------------------------------------------------------
# run_worker
# ---------------------------------------------------------------------------


def test_run_worker_processes_tasks_via_handler(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    id1 = enqueue(conn, _make_task())
    id2 = enqueue(conn, _make_task(deps=[id1]))
    conn.close()

    executed: list[int] = []

    def ingest_handler(task: Task, _conn: sqlite3.Connection) -> str:
        assert task.id is not None
        executed.append(task.id)
        return f"result_{task.id}"

    run_worker(
        tmp_path,
        obra="X",
        handlers={TaskType.INGEST: ingest_handler},
        stop_when_empty=True,
    )

    # Ordem respeitou dependência.
    assert executed == [id1, id2]

    # Ambas marcadas done com result_ref correto.
    conn = sqlite3.connect(tmp_path / DB_FILENAME)
    conn.row_factory = sqlite3.Row
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM tasks ORDER BY id")}
    assert rows[id1]["status"] == "done"
    assert rows[id1]["result_ref"] == f"result_{id1}"
    assert rows[id2]["status"] == "done"


def test_run_worker_marks_failed_on_handler_exception(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    task_id = enqueue(conn, _make_task())
    enqueue(conn, _make_task())  # segunda task — worker não pode travar
    conn.close()

    call_count = {"n": 0}

    def handler(task: Task, _conn: sqlite3.Connection) -> str | None:
        call_count["n"] += 1
        if task.id == task_id:
            raise RuntimeError("falha simulada")
        return None

    run_worker(
        tmp_path,
        obra="X",
        handlers={TaskType.INGEST: handler},
        stop_when_empty=True,
    )

    assert call_count["n"] == 2  # não interrompeu após a primeira falhar

    conn = sqlite3.connect(tmp_path / DB_FILENAME)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status, error_message FROM tasks WHERE id=?", (task_id,)).fetchone()
    assert row["status"] == "failed"
    assert "falha simulada" in row["error_message"]


def test_run_worker_marks_failed_when_no_handler_registered(tmp_path: Path) -> None:
    conn = init_db(tmp_path)
    task_id = enqueue(conn, _make_task())  # task_type = INGEST
    conn.close()

    # Handlers vazio: worker não tem como executar, deve marcar failed.
    run_worker(tmp_path, obra="X", handlers={}, stop_when_empty=True)

    conn = sqlite3.connect(tmp_path / DB_FILENAME)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status, error_message FROM tasks WHERE id=?", (task_id,)).fetchone()
    assert row["status"] == "failed"
    assert "sem handler" in row["error_message"]


def test_run_worker_stop_when_empty_returns_immediately(tmp_path: Path) -> None:
    # Não enfileirei nada — deve retornar sem bloquear.
    run_worker(tmp_path, obra="X", handlers={}, stop_when_empty=True)
