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
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DB_FILENAME = "index.sqlite"


def _now_iso() -> str:
    """ISO 8601 UTC com sufixo Z — usado para created_at/started_at/finished_at."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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
    conn.commit()
    return conn


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


def run_worker(vault_path: Path, obra: str, poll_interval_sec: int = 2) -> None:
    """
    Loop principal do worker.

    Enquanto houver tarefas PENDING com dependências resolvidas:
        1. Pega a próxima tarefa
        2. Marca como RUNNING
        3. Despacha para o handler correspondente ao task_type
        4. Marca como DONE ou FAILED conforme resultado
        5. Se FAILED, continua com próxima (não interrompe o loop)

    Se não houver tarefas pendentes, dorme poll_interval_sec e verifica de novo.
    Encerra com Ctrl+C.
    """
    # TODO Sprint 1
    raise NotImplementedError
