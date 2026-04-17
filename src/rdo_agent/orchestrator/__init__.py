"""
Orquestrador — Camada 1.

Coordena o pipeline através de uma fila de tarefas no SQLite.
Todos os componentes (ingestor, parser, extractor, agentes das Camadas 2-3)
escrevem tarefas e consomem tarefas desta fila.

Esta é uma implementação simples — não usa Redis, Celery, ou qualquer
outro broker externo. SQLite + polling leve é suficiente para o volume
esperado (um PC processa centenas de RDOs por dia).

Schema da tabela tasks:
    id            INTEGER PRIMARY KEY
    task_type     TEXT NOT NULL    -- ex: "ingest", "parse_txt", "transcribe", etc.
    payload       TEXT NOT NULL    -- JSON com parâmetros específicos da tarefa
    status        TEXT NOT NULL    -- pending | running | done | failed
    depends_on    TEXT             -- JSON array de task_ids das quais depende
    obra          TEXT NOT NULL    -- isolamento por obra
    created_at    TEXT NOT NULL    -- ISO 8601
    started_at    TEXT
    finished_at   TEXT
    error_message TEXT
    result_ref    TEXT             -- referência a output (file_id, event_id, etc.)
"""

from __future__ import annotations

import json
import sqlite3
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

TaskHandler = Callable[["Task", sqlite3.Connection], "str | None"]

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DB_FILENAME = "index.sqlite"


def _now_iso() -> str:
    """ISO 8601 UTC com sufixo Z — usado para created_at/started_at/finished_at."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class TaskType(str, Enum):
    # Sprint 1
    INGEST = "ingest"
    PARSE_TXT = "parse_txt"
    RESOLVE_TIME = "resolve_time"
    EXTRACT_AUDIO = "extract_audio"

    # Sprint 2
    TRANSCRIBE = "transcribe"
    VISUAL_ANALYSIS = "visual_analysis"
    EXTRACT_DOCUMENT = "extract_document"

    # Sprint 3
    CLASSIFY = "classify"

    # Sprint 4
    ENGINEER_SYNTHESIZE = "engineer_synthesize"


@dataclass
class Task:
    """Representação de uma tarefa na fila."""

    id: int | None
    task_type: TaskType
    payload: dict
    status: TaskStatus
    depends_on: list[int]
    obra: str
    created_at: str
    priority: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    error_message: str | None = None
    result_ref: str | None = None


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        task_type=TaskType(row["task_type"]),
        payload=json.loads(row["payload"]),
        status=TaskStatus(row["status"]),
        depends_on=json.loads(row["depends_on"]),
        obra=row["obra"],
        created_at=row["created_at"],
        priority=row["priority"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error_message=row["error_message"],
        result_ref=row["result_ref"],
    )


def init_db(vault_path: Path) -> sqlite3.Connection:
    """
    Inicializa o index.sqlite da vault se ainda não existir.

    Cria (se ausentes) todas as 9 tabelas do Blueprint §7.2 lendo o DDL
    de schema.sql. Idempotente: chamar sobre uma vault já inicializada
    não destrói dados.

    Aplica os PRAGMAs necessários:
      - foreign_keys=ON: FKs declaradas no schema são efetivamente validadas.
      - journal_mode=WAL: leituras concorrentes não bloqueiam escritas
        (workers podem ler status enquanto outro processo grava).

    Args:
        vault_path: diretório da vault da obra (ex.: rdo_vaults/CODESC_75817/).
            Será criado se não existir. O arquivo SQLite é gravado em
            vault_path/index.sqlite.

    Returns:
        Conexão SQLite pronta para uso, com PRAGMAs já aplicados.
    """
    vault_path.mkdir(parents=True, exist_ok=True)
    db_path = vault_path / DB_FILENAME

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate_api_calls_sprint2_phase2(conn)
    conn.commit()
    return conn


def _migrate_api_calls_sprint2_phase2(conn: sqlite3.Connection) -> None:
    """
    Sprint 2 §Fase 2 — adiciona latency_ms/model/error_type à tabela api_calls.

    Idempotente: consulta PRAGMA table_info e só aplica ALTER TABLE para
    colunas ainda ausentes. Necessária para vaults criadas antes desta sprint;
    vaults frescas já recebem as colunas via CREATE TABLE em schema.sql.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(api_calls)")}
    for col_name, col_def in (
        ("latency_ms", "INTEGER"),
        ("model", "TEXT"),
        ("error_type", "TEXT"),
    ):
        if col_name not in existing:
            conn.execute(f"ALTER TABLE api_calls ADD COLUMN {col_name} {col_def}")


def enqueue(conn: sqlite3.Connection, task: Task) -> int:
    """
    Adiciona tarefa à fila.

    O id do argumento é ignorado — o SQLite gera um novo via AUTOINCREMENT.
    O created_at do argumento também é ignorado se for falsy (vazio/None),
    sendo substituído por now(). Status inicial é respeitado (normalmente
    PENDING), bem como priority.

    Returns:
        id gerado pelo SQLite.
    """
    created_at = task.created_at or _now_iso()
    cur = conn.execute(
        """
        INSERT INTO tasks (
            task_type, payload, status, depends_on, obra,
            priority, created_at, started_at, finished_at,
            error_message, result_ref
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task.task_type.value,
            json.dumps(task.payload, ensure_ascii=False, sort_keys=True),
            task.status.value,
            json.dumps(task.depends_on),
            task.obra,
            task.priority,
            created_at,
            task.started_at,
            task.finished_at,
            task.error_message,
            task.result_ref,
        ),
    )
    conn.commit()
    task_id = cur.lastrowid
    assert task_id is not None  # AUTOINCREMENT garante não-nulo
    task.id = task_id
    task.created_at = created_at
    return task_id


def next_pending(conn: sqlite3.Connection, obra: str) -> Task | None:
    """
    Retorna a próxima tarefa PENDING cujas dependências estão DONE.

    Ordenação: priority DESC, created_at ASC. Tasks com depends_on vazio
    ([]) sempre elegíveis. Tasks que referenciam um task_id inexistente
    são bloqueadas por segurança (evita race com produtor/consumidor).

    Args:
        obra: isolamento por obra — nunca retorna tasks de outra obra.

    Returns:
        Task pronta para executar, ou None se a fila da obra estiver vazia
        ou só tiver tarefas bloqueadas por dependências.
    """
    row = conn.execute(
        """
        SELECT * FROM tasks t
        WHERE t.obra = ?
          AND t.status = 'pending'
          AND NOT EXISTS (
              SELECT 1
              FROM json_each(t.depends_on) j
              LEFT JOIN tasks dep ON dep.id = CAST(j.value AS INTEGER)
              WHERE dep.status IS NULL OR dep.status != 'done'
          )
        ORDER BY t.priority DESC, t.created_at ASC, t.id ASC
        LIMIT 1
        """,
        (obra,),
    ).fetchone()
    return _row_to_task(row) if row is not None else None


def mark_running(conn: sqlite3.Connection, task_id: int) -> None:
    """Marca tarefa como RUNNING e registra started_at."""
    conn.execute(
        "UPDATE tasks SET status = 'running', started_at = ? WHERE id = ?",
        (_now_iso(), task_id),
    )
    conn.commit()


def mark_done(conn: sqlite3.Connection, task_id: int, result_ref: str | None = None) -> None:
    """Marca tarefa como DONE e registra finished_at."""
    conn.execute(
        "UPDATE tasks SET status = 'done', finished_at = ?, result_ref = ? WHERE id = ?",
        (_now_iso(), result_ref, task_id),
    )
    conn.commit()


def mark_failed(conn: sqlite3.Connection, task_id: int, error: str) -> None:
    """Marca tarefa como FAILED e registra error_message."""
    conn.execute(
        "UPDATE tasks SET status = 'failed', finished_at = ?, error_message = ? WHERE id = ?",
        (_now_iso(), error, task_id),
    )
    conn.commit()


def run_worker(
    vault_path: Path,
    obra: str,
    handlers: dict[TaskType, TaskHandler],
    poll_interval_sec: float = 2.0,
    stop_when_empty: bool = False,
) -> None:
    """
    Loop principal do worker.

    A cada iteração:
        1. Busca próxima PENDING com dependências resolvidas (next_pending).
        2. Marca como RUNNING.
        3. Chama handlers[task.task_type](task, conn). O handler é
           responsável por toda a lógica específica da tarefa; deve
           retornar uma string (result_ref, ex.: file_id ou event_id) ou
           None. Se levantar qualquer exceção, task vira FAILED com
           traceback no error_message e o worker continua.
        4. Se não houver task_type registrado, marca como FAILED.
        5. Se a fila estiver vazia, dorme poll_interval_sec.

    A injeção de handlers via dict (ao invés de registry com decorator
    ou imports estáticos) é intencional: evita acoplamento estático
    entre orchestrator e módulos de agente. Um registry com decorator
    reintroduziria import circular assim que o ingestor/parser/etc.
    precisassem chamar utilidades do orchestrator.

    Args:
        vault_path: diretório da vault (init_db será chamado).
        obra: CODESC da obra — isolamento por obra.
        handlers: mapa TaskType → função que executa a tarefa. Um
            handler recebe (task, conn) e retorna result_ref (ou None).
        poll_interval_sec: tempo dormindo quando a fila está vazia.
        stop_when_empty: encerra o loop ao encontrar fila vazia em vez
            de dormir. Primarily for testing — em produção, o worker
            fica aguardando novas tarefas indefinidamente.
    """
    conn = init_db(vault_path)
    log.info("worker iniciado para obra=%s (stop_when_empty=%s)", obra, stop_when_empty)

    try:
        while True:
            task = next_pending(conn, obra)
            if task is None:
                if stop_when_empty:
                    log.info("fila vazia para obra=%s, encerrando (stop_when_empty)", obra)
                    return
                time.sleep(poll_interval_sec)
                continue

            assert task.id is not None
            mark_running(conn, task.id)
            log.info("executando task id=%s type=%s", task.id, task.task_type.value)

            handler = handlers.get(task.task_type)
            if handler is None:
                msg = f"sem handler registrado para task_type={task.task_type.value}"
                log.error("task id=%s falhou: %s", task.id, msg)
                mark_failed(conn, task.id, msg)
                continue

            try:
                result_ref = handler(task, conn)
            except Exception:
                err = traceback.format_exc()
                log.exception("task id=%s levantou exceção", task.id)
                mark_failed(conn, task.id, err)
                continue

            mark_done(conn, task.id, result_ref=result_ref)
            log.info("task id=%s concluída result_ref=%s", task.id, result_ref)
    except KeyboardInterrupt:
        log.info("worker interrompido (Ctrl+C), encerrando")
    finally:
        conn.close()
