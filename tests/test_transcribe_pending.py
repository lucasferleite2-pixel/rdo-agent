"""Testes do transcribe_pending — Sessao 8, divida #45.

Wrapper de orquestracao integrado com PipelineStateManager +
StructuredLogger + CostQuota + CircuitBreaker(openai_whisper).

Mocka transcribe_handler para isolar a logica de orquestracao da
chamada real a OpenAI Whisper.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from rdo_agent.orchestrator import (
    Task,
    TaskStatus,
    TaskType,
    enqueue,
    init_db,
)
from rdo_agent.transcriber import transcribe_pending


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_with_audio_task(tmp_path, monkeypatch) -> sqlite3.Connection:
    """DB com 1 audio file + 1 task TRANSCRIBE pending."""
    from rdo_agent.utils import config

    settings = config.Settings(
        openai_api_key="sk-fake-test", anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=tmp_path, log_level="WARNING", dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)

    conn = init_db(tmp_path / "vault")
    obra = "OBRA_T"

    # Cria 1 audio file fake
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type,
        sha256, size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, 'audio', ?, 100,
                'awaiting_transcription', '2026-04-25T00:00:00Z')""",
        ("f_audio_001", obra, "10_media/audio_001.opus",
         "a" * 64),
    )
    conn.commit()

    # Cria 1 task TRANSCRIBE
    enqueue(conn, Task(
        id=None, task_type=TaskType.TRANSCRIBE,
        payload={"file_id": "f_audio_001",
                 "file_path": "10_media/audio_001.opus"},
        status=TaskStatus.PENDING, depends_on=[], obra=obra,
        created_at="", priority=0,
    ))
    conn.commit()
    return conn


def _seed_existing_transcription(conn: sqlite3.Connection, file_id: str,
                                  text: str = "transcricao previa"):
    """Insere transcricao para simular run anterior bem sucedido."""
    conn.execute(
        """INSERT INTO transcriptions (obra, file_id, text, language,
        confidence, low_confidence, api_call_id, created_at)
        VALUES ('OBRA_T', ?, ?, 'pt', 0.9, 0, NULL,
                '2026-04-25T00:00:00Z')""",
        (file_id, text),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------


def test_transcribe_pending_skips_already_transcribed(db_with_audio_task):
    """Se transcriptions ja tem o file_id, skip sem chamar Whisper."""
    _seed_existing_transcription(db_with_audio_task, "f_audio_001")

    counts = transcribe_pending(db_with_audio_task, obra="OBRA_T")
    assert counts == {"processed": 0, "skipped": 1, "failed": 0}

    # Task foi marcada como done
    row = db_with_audio_task.execute(
        "SELECT status FROM tasks WHERE task_type='transcribe'",
    ).fetchone()
    assert row["status"] == "done"


def test_transcribe_pending_force_skips_dedup_check(
    db_with_audio_task, monkeypatch,
):
    """Com force=True, idempotencia eh ignorada (caller controla)."""
    _seed_existing_transcription(db_with_audio_task, "f_audio_001")

    called = {"n": 0}
    def fake_handler(task, conn):
        called["n"] += 1
        return "f_txt_001"

    from rdo_agent import transcriber
    monkeypatch.setattr(transcriber, "transcribe_handler", fake_handler)

    counts = transcribe_pending(
        db_with_audio_task, obra="OBRA_T", force=True,
    )
    assert called["n"] == 1
    assert counts["processed"] == 1


# ---------------------------------------------------------------------------
# Sucesso (mock handler)
# ---------------------------------------------------------------------------


def test_transcribe_pending_calls_handler_when_no_existing(
    db_with_audio_task, monkeypatch,
):
    handler_calls = []
    def fake_handler(task, conn):
        handler_calls.append(task.id)
        return "f_txt_001"

    from rdo_agent import transcriber
    monkeypatch.setattr(transcriber, "transcribe_handler", fake_handler)

    counts = transcribe_pending(db_with_audio_task, obra="OBRA_T")
    assert len(handler_calls) == 1
    assert counts == {"processed": 1, "skipped": 0, "failed": 0}

    # Task done com result_ref correto
    row = db_with_audio_task.execute(
        "SELECT status, result_ref FROM tasks WHERE task_type='transcribe'",
    ).fetchone()
    assert row["status"] == "done"
    assert row["result_ref"] == "f_txt_001"


def test_transcribe_pending_invokes_callbacks(
    db_with_audio_task, monkeypatch,
):
    """on_skip / on_done / on_fail recebem (file_id, ctx)."""
    _seed_existing_transcription(db_with_audio_task, "f_audio_001")

    skips = []
    transcribe_pending(
        db_with_audio_task, obra="OBRA_T",
        on_skip=lambda fid, ctx: skips.append((fid, ctx)),
    )
    assert len(skips) == 1
    assert skips[0][0] == "f_audio_001"
    assert "existing_id" in skips[0][1]


# ---------------------------------------------------------------------------
# Falha do handler -> task failed
# ---------------------------------------------------------------------------


def test_transcribe_pending_marks_failed_on_handler_error(
    db_with_audio_task, monkeypatch,
):
    def boom_handler(task, conn):
        raise RuntimeError("boom: api 503")

    from rdo_agent import transcriber
    monkeypatch.setattr(transcriber, "transcribe_handler", boom_handler)

    fails = []
    counts = transcribe_pending(
        db_with_audio_task, obra="OBRA_T",
        on_fail=lambda fid, ctx: fails.append((fid, ctx)),
    )
    assert counts == {"processed": 0, "skipped": 0, "failed": 1}
    assert len(fails) == 1

    row = db_with_audio_task.execute(
        "SELECT status, error_message FROM tasks WHERE task_type='transcribe'",
    ).fetchone()
    assert row["status"] == "failed"
    assert "503" in row["error_message"]


def test_transcribe_pending_continues_after_failure(
    db_with_audio_task, monkeypatch,
):
    """Falha em 1 task nao para o loop; proxima task eh tentada."""
    obra = "OBRA_T"
    # Adiciona segunda task
    db_with_audio_task.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type,
        sha256, size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, 'audio', ?, 100,
                'awaiting_transcription', '2026-04-25T00:00:00Z')""",
        ("f_audio_002", obra, "10_media/audio_002.opus", "b" * 64),
    )
    db_with_audio_task.commit()
    enqueue(db_with_audio_task, Task(
        id=None, task_type=TaskType.TRANSCRIBE,
        payload={"file_id": "f_audio_002",
                 "file_path": "10_media/audio_002.opus"},
        status=TaskStatus.PENDING, depends_on=[], obra=obra,
        created_at="", priority=0,
    ))

    n_calls = {"i": 0}
    def flaky(task, conn):
        n_calls["i"] += 1
        if n_calls["i"] == 1:
            raise RuntimeError("primeiro falha")
        return "f_txt_b"

    from rdo_agent import transcriber
    monkeypatch.setattr(transcriber, "transcribe_handler", flaky)

    counts = transcribe_pending(db_with_audio_task, obra=obra)
    assert counts == {"processed": 1, "skipped": 0, "failed": 1}


# ---------------------------------------------------------------------------
# max_audios cap
# ---------------------------------------------------------------------------


def test_transcribe_pending_respects_max_audios(
    db_with_audio_task, monkeypatch,
):
    """max_audios=1 com 3 tasks: processa 1, deixa 2 pending."""
    obra = "OBRA_T"
    for i in range(2, 4):
        db_with_audio_task.execute(
            """INSERT INTO files (file_id, obra, file_path, file_type,
            sha256, size_bytes, semantic_status, created_at)
            VALUES (?, ?, ?, 'audio', ?, 100,
                    'awaiting_transcription', '2026-04-25T00:00:00Z')""",
            (f"f_audio_{i:03d}", obra, f"10_media/audio_{i:03d}.opus",
             chr(96 + i) * 64),
        )
        db_with_audio_task.commit()
        enqueue(db_with_audio_task, Task(
            id=None, task_type=TaskType.TRANSCRIBE,
            payload={"file_id": f"f_audio_{i:03d}",
                     "file_path": f"10_media/audio_{i:03d}.opus"},
            status=TaskStatus.PENDING, depends_on=[], obra=obra,
            created_at="", priority=0,
        ))

    from rdo_agent import transcriber
    monkeypatch.setattr(
        transcriber, "transcribe_handler",
        lambda task, conn: "f_txt_x",
    )

    counts = transcribe_pending(
        db_with_audio_task, obra=obra, max_audios=1,
    )
    assert counts["processed"] == 1

    pending_n = db_with_audio_task.execute(
        "SELECT COUNT(*) FROM tasks WHERE status='pending' "
        "AND task_type='transcribe'",
    ).fetchone()[0]
    assert pending_n == 2


# ---------------------------------------------------------------------------
# Resumability (via PipelineStateManager.reset_running)
# ---------------------------------------------------------------------------


def test_transcribe_pending_resumes_after_reset_running(
    db_with_audio_task, monkeypatch,
):
    """Simula crash: task fica running sem finished_at, reset_running
    devolve para pending, proxima execucao processa."""
    from rdo_agent.orchestrator import mark_running
    from rdo_agent.pipeline_state import PipelineStateManager

    # Pega task_id e marca running (simula crash mid-execution)
    task_id = db_with_audio_task.execute(
        "SELECT id FROM tasks WHERE task_type='transcribe' LIMIT 1",
    ).fetchone()["id"]
    mark_running(db_with_audio_task, task_id)
    db_with_audio_task.commit()

    state = PipelineStateManager(db_with_audio_task)
    assert state.resumable_state("OBRA_T")  # detectou
    state.reset_running("OBRA_T")  # devolve para pending

    from rdo_agent import transcriber
    monkeypatch.setattr(
        transcriber, "transcribe_handler", lambda t, c: "f_txt",
    )
    counts = transcribe_pending(db_with_audio_task, obra="OBRA_T")
    assert counts["processed"] == 1


# ---------------------------------------------------------------------------
# Empty queue
# ---------------------------------------------------------------------------


def test_transcribe_pending_empty_queue_returns_zeros(tmp_path, monkeypatch):
    from rdo_agent.utils import config
    settings = config.Settings(
        openai_api_key="x", anthropic_api_key="",
        claude_model="x", vaults_root=tmp_path,
        log_level="WARNING", dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)
    conn = init_db(tmp_path / "vault_empty")

    counts = transcribe_pending(conn, obra="EMPTY")
    assert counts == {"processed": 0, "skipped": 0, "failed": 0}


# ---------------------------------------------------------------------------
# Circuit breaker integration
# ---------------------------------------------------------------------------


def test_transcribe_pending_breaks_when_circuit_opens(
    db_with_audio_task, monkeypatch,
):
    """5 falhas consecutivas abrem circuit; loop para em CircuitOpenError."""
    obra = "OBRA_T"
    # Cria 7 tasks para garantir que circuit abre antes de drenar tudo
    for i in range(2, 9):
        db_with_audio_task.execute(
            """INSERT INTO files (file_id, obra, file_path, file_type,
            sha256, size_bytes, semantic_status, created_at)
            VALUES (?, ?, ?, 'audio', ?, 100,
                    'awaiting_transcription', '2026-04-25T00:00:00Z')""",
            (f"f_audio_{i:03d}", obra, f"10_media/audio_{i:03d}.opus",
             chr(96 + i) * 64),
        )
        db_with_audio_task.commit()
        enqueue(db_with_audio_task, Task(
            id=None, task_type=TaskType.TRANSCRIBE,
            payload={"file_id": f"f_audio_{i:03d}",
                     "file_path": f"10_media/audio_{i:03d}.opus"},
            status=TaskStatus.PENDING, depends_on=[], obra=obra,
            created_at="", priority=0,
        ))

    from rdo_agent import transcriber
    from rdo_agent.observability.resilience import (
        get_openai_whisper_circuit, reset_singletons_for_test,
    )
    reset_singletons_for_test()  # circuit fresh

    monkeypatch.setattr(
        transcriber, "transcribe_handler",
        lambda t, c: (_ for _ in ()).throw(RuntimeError("503")),
    )

    counts = transcribe_pending(db_with_audio_task, obra=obra)
    # Algumas falham antes do circuit abrir; depois loop para
    assert counts["failed"] >= 5
    assert get_openai_whisper_circuit().state == "OPEN"
    # Sobram tasks pending (não foram processadas porque circuit abriu)
    pending_n = db_with_audio_task.execute(
        "SELECT COUNT(*) FROM tasks WHERE status='pending'",
    ).fetchone()[0]
    assert pending_n >= 1
