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

import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DB_FILENAME = "index.sqlite"


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
    started_at: str | None = None
    finished_at: str | None = None
    error_message: str | None = None
    result_ref: str | None = None


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
    """Adiciona tarefa à fila. Retorna o id gerado."""
    # TODO Sprint 1
    raise NotImplementedError


def next_pending(conn: sqlite3.Connection, obra: str) -> Task | None:
    """
    Retorna a próxima tarefa PENDING cujas dependências estão DONE.

    None se não houver tarefa pronta para executar.
    """
    # TODO Sprint 1
    raise NotImplementedError


def mark_running(conn: sqlite3.Connection, task_id: int) -> None:
    """Marca tarefa como RUNNING e registra started_at."""
    # TODO Sprint 1
    raise NotImplementedError


def mark_done(conn: sqlite3.Connection, task_id: int, result_ref: str | None = None) -> None:
    """Marca tarefa como DONE e registra finished_at."""
    # TODO Sprint 1
    raise NotImplementedError


def mark_failed(conn: sqlite3.Connection, task_id: int, error: str) -> None:
    """Marca tarefa como FAILED e registra error_message."""
    # TODO Sprint 1
    raise NotImplementedError


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
