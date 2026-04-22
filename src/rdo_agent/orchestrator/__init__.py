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
    DETECT_QUALITY = "detect_quality"
    CLASSIFY = "classify"

    # Sprint 4
    ENGINEER_SYNTHESIZE = "engineer_synthesize"
    OCR_FIRST = "ocr_first"


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
    _migrate_classifications_sprint4(conn)
    _migrate_financial_records_sprint4_op8(conn)
    _migrate_visual_analyses_archive_sprint4_op9(conn)
    _migrate_superseded_by_sprint4_op11(conn)
    _migrate_sprint5_fase_a_b(conn)
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


def _migrate_classifications_sprint4(conn: sqlite3.Connection) -> None:
    """
    Sprint 4 Op1 — adiciona source_message_id à tabela classifications.

    Permite rastrear mensagens de texto puro WhatsApp (sem anexo) como
    fonte semantica, alem dos derivados de arquivos (transcricoes,
    visual_analyses, documents). Ver ADR-003 (pendente) para contexto.

    Idempotente: PRAGMA table_info + ALTER TABLE so se ausente.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(classifications)")}
    if "source_message_id" not in existing:
        conn.execute("ALTER TABLE classifications ADD COLUMN source_message_id TEXT")


def _migrate_financial_records_sprint4_op8(conn: sqlite3.Connection) -> None:
    """
    Sprint 4 Op8 — cria tabela financial_records se ausente.

    Pipeline OCR-first para imagens desacopla descricao visual (Vision)
    de extracao de texto em documentos fotografados (OCR). Quando o
    OCR detecta comprovante financeiro (PIX/TED/boleto/nota/recibo),
    o extrator estrutural popula esta tabela com valor em centavos,
    datas, partes envolvidas etc.

    Idempotente: `CREATE TABLE IF NOT EXISTS` ja eh aplicado via
    `executescript(schema.sql)` em init_db; esta funcao existe como
    ponto-de-extensao futuro (ex: adicionar colunas via ALTER) e
    documentacao do invariante.
    """
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "financial_records" not in tables:
        # Fallback para o caso schema.sql nao ter sido reexecutado
        # (improvavel, mas preserva idempotencia explicita).
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS financial_records (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                obra                TEXT NOT NULL,
                source_file_id      TEXT NOT NULL,
                doc_type            TEXT,
                valor_centavos      INTEGER,
                moeda               TEXT DEFAULT 'BRL',
                data_transacao      TEXT,
                hora_transacao      TEXT,
                pagador_nome        TEXT,
                pagador_doc         TEXT,
                recebedor_nome      TEXT,
                recebedor_doc       TEXT,
                chave_pix           TEXT,
                descricao           TEXT,
                instituicao_origem  TEXT,
                instituicao_destino TEXT,
                raw_ocr_text        TEXT,
                confidence          REAL,
                api_call_id         INTEGER,
                created_at          TEXT NOT NULL,
                FOREIGN KEY (source_file_id) REFERENCES files(file_id),
                FOREIGN KEY (api_call_id)    REFERENCES api_calls(id),
                UNIQUE (obra, source_file_id)
            );
            CREATE INDEX IF NOT EXISTS idx_financial_records_obra_data
                ON financial_records(obra, data_transacao);
            """
        )


def _migrate_visual_analyses_archive_sprint4_op9(conn: sqlite3.Connection) -> None:
    """
    Sprint 4 Op9 — cria tabela visual_analyses_archive se ausente.

    Arquiva rows de `visual_analyses` superseded por reprocessamento
    (ex: pipeline OCR-first retroativo Op9). Mirror do schema original
    + `archived_at` e `archive_reason`. Preserva forense: qualquer
    analise substituida continua auditavel pra prova de linhagem.

    Idempotente: `CREATE TABLE IF NOT EXISTS` ja eh aplicado via
    `executescript(schema.sql)` em init_db. Esta funcao existe como
    ponto-de-extensao futuro + documentacao do invariante.
    """
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "visual_analyses_archive" not in tables:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS visual_analyses_archive (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                original_id     INTEGER,
                obra            TEXT NOT NULL,
                file_id         TEXT NOT NULL,
                analysis_json   TEXT NOT NULL,
                confidence      REAL,
                api_call_id     INTEGER,
                created_at      TEXT NOT NULL,
                archived_at     TEXT NOT NULL,
                archive_reason  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_visual_analyses_archive_obra
                ON visual_analyses_archive(obra, archived_at);
            CREATE INDEX IF NOT EXISTS idx_visual_analyses_archive_fileid
                ON visual_analyses_archive(file_id);
            """
        )


def _migrate_superseded_by_sprint4_op11(conn: sqlite3.Connection) -> None:
    """
    Sprint 4 Op11 Divida #10 — archive move-style via superseded_by.

    Adiciona 2 colunas a visual_analyses:
      - superseded_by INTEGER: id da nova row que substituiu esta
        (NULL = row ativa)
      - superseded_at TEXT: ISO timestamp da substituicao

    Rows ativas: `WHERE superseded_by IS NULL`. A view
    `visual_analyses_active` (criada em schema.sql) encapsula isso.

    Idempotente: PRAGMA table_info + ALTER TABLE so se colunas ausentes.
    View ja eh CREATE VIEW IF NOT EXISTS via executescript.
    """
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(visual_analyses)")
    }
    if "superseded_by" not in existing:
        conn.execute(
            "ALTER TABLE visual_analyses ADD COLUMN superseded_by INTEGER"
        )
    if "superseded_at" not in existing:
        conn.execute(
            "ALTER TABLE visual_analyses ADD COLUMN superseded_at TEXT"
        )


def _migrate_sprint5_fase_a_b(conn: sqlite3.Connection) -> None:
    """
    Sprint 5 Fase A/B — cria tabelas forensic_narratives + correlations.

    Fase A: forensic_narratives armazena narrativas geradas pelo agente
    forense (Sonnet 4.6) sobre dossiers cronologicos de obras/dias.
    UNIQUE(obra, scope, scope_ref, dossier_hash) funciona como cache key.

    Fase B: correlations (esqueleto — detectores virão em sessao futura)
    guarda relacoes temporais/semanticas entre eventos.

    Idempotente: CREATE TABLE IF NOT EXISTS ja eh aplicado via schema.sql;
    funcao existe como ponto-de-extensao e documentacao do invariante.
    """
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "forensic_narratives" not in tables:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS forensic_narratives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                obra TEXT NOT NULL,
                scope TEXT NOT NULL
                    CHECK (scope IN ('day', 'obra_overview')),
                scope_ref TEXT,
                narrative_text TEXT NOT NULL,
                dossier_hash TEXT NOT NULL,
                model_used TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                api_call_id INTEGER,
                events_count INTEGER,
                confidence REAL,
                validation_checklist_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (api_call_id) REFERENCES api_calls(id),
                UNIQUE (obra, scope, scope_ref, dossier_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_narratives_obra_scope
                ON forensic_narratives(obra, scope, scope_ref);
            """
        )
    if "correlations" not in tables:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS correlations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                obra TEXT NOT NULL,
                correlation_type TEXT NOT NULL,
                primary_event_ref TEXT NOT NULL,
                primary_event_source TEXT NOT NULL,
                related_event_ref TEXT NOT NULL,
                related_event_source TEXT NOT NULL,
                time_gap_seconds INTEGER,
                confidence REAL,
                rationale TEXT,
                detected_by TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_correlations_obra
                ON correlations(obra, correlation_type);
            """
        )


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
