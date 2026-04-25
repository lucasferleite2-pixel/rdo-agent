"""
PipelineStateManager — wrapper ergonômico sobre a tabela ``tasks``.

A state machine real do pipeline já vive na tabela ``tasks``
(orchestrator desde Sprint 1). Este módulo **não** cria tabela
nova — só expõe a state machine como API ergonômica + helpers de
recovery após crash. Ver ``docs/ADR-007-state-machine.md``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from rdo_agent.orchestrator import (
    Task,
    TaskStatus,
    TaskType,
    _row_to_task,
    enqueue,
    mark_done,
    mark_failed,
    mark_running,
    next_pending,
)
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class StatusReport:
    """
    Snapshot do estado de processamento de uma obra.

    Attributes:
        obra: identificador.
        counts: dict ``{task_type: {status: count}}`` denso (zeros
            preservados quando o type tem alguma task qualquer).
        totals_by_status: ``{status: total}`` agregando todos os types.
        resumable: lista de tasks ``running`` sem ``finished_at``
            (crash recovery candidates).
    """

    obra: str
    counts: dict[str, dict[str, int]]
    totals_by_status: dict[str, int]
    resumable: list[Task] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        return self.totals_by_status.get(TaskStatus.FAILED.value, 0) > 0

    @property
    def has_resumable(self) -> bool:
        return len(self.resumable) > 0

    @property
    def has_pending(self) -> bool:
        return self.totals_by_status.get(TaskStatus.PENDING.value, 0) > 0


class PipelineStateManager:
    """
    Operações de leitura/escrita sobre a state machine ``tasks`` em
    nome de um orchestrator/CLI/operador.

    A conexão SQLite é fornecida pelo caller (consistente com o resto
    do codebase). O caller é responsável por commit; este wrapper
    não gerencia transações implícitas além das chamadas wrapped do
    orchestrator (que já fazem commit).
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ------------------------------------------------------------------
    # Leitura — observabilidade
    # ------------------------------------------------------------------

    def status(self, obra: str) -> StatusReport:
        """
        Agrega contagem por (task_type, status) para a ``obra``,
        identifica tasks resumíveis e devolve um ``StatusReport``.
        """
        rows = self.conn.execute(
            """
            SELECT task_type, status, COUNT(*) AS n
              FROM tasks
             WHERE obra = ?
             GROUP BY task_type, status
            """,
            (obra,),
        ).fetchall()

        counts: dict[str, dict[str, int]] = {}
        totals_by_status: dict[str, int] = {}
        for row in rows:
            tt, st, n = row["task_type"], row["status"], row["n"]
            counts.setdefault(tt, {})[st] = n
            totals_by_status[st] = totals_by_status.get(st, 0) + n

        resumable = self._fetch_resumable(obra)
        return StatusReport(
            obra=obra,
            counts=counts,
            totals_by_status=totals_by_status,
            resumable=resumable,
        )

    def resumable_state(self, obra: str) -> list[Task]:
        """
        Tasks que estão ``running`` mas sem ``finished_at`` —
        candidatas a recovery após crash. Atalho de leitura
        independente de ``status()``.
        """
        return self._fetch_resumable(obra)

    def _fetch_resumable(self, obra: str) -> list[Task]:
        rows = self.conn.execute(
            """
            SELECT *
              FROM tasks
             WHERE obra = ?
               AND status = ?
               AND (finished_at IS NULL OR finished_at = '')
             ORDER BY started_at ASC, id ASC
            """,
            (obra, TaskStatus.RUNNING.value),
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    # ------------------------------------------------------------------
    # Escrita — claim/complete/fail / recovery
    # ------------------------------------------------------------------

    def claim(
        self, obra: str, *, task_type: TaskType | None = None,
    ) -> Task | None:
        """
        Retorna a próxima task ``pending`` (respeitando
        ``depends_on``) marcada como ``running``, ou ``None`` se não
        houver. Atômico em relação ao caller (mark_running aplicado
        antes do retorno).

        Se ``task_type`` for fornecido, restringe ao tipo. Útil para
        workers especializados.
        """
        candidate = next_pending(self.conn, obra)
        if candidate is None:
            return None
        if task_type is not None and candidate.task_type != task_type:
            # next_pending não filtra por type. Para workers
            # especializados, fazemos query direta.
            row = self.conn.execute(
                """
                SELECT *
                  FROM tasks
                 WHERE obra = ?
                   AND status = ?
                   AND task_type = ?
                 ORDER BY priority DESC, created_at ASC
                 LIMIT 1
                """,
                (obra, TaskStatus.PENDING.value, task_type.value),
            ).fetchone()
            if row is None:
                return None
            candidate = _row_to_task(row)

        mark_running(self.conn, candidate.id)  # type: ignore[arg-type]
        self.conn.commit()
        candidate_running = Task(
            id=candidate.id,
            task_type=candidate.task_type,
            payload=candidate.payload,
            status=TaskStatus.RUNNING,
            depends_on=candidate.depends_on,
            obra=candidate.obra,
            created_at=candidate.created_at,
            priority=candidate.priority,
            started_at=_now_iso(),
            finished_at=None,
            error_message=None,
            result_ref=None,
        )
        return candidate_running

    def complete(self, task_id: int, *, result_ref: str | None = None) -> None:
        """Marca task como ``done``. Wrapper sobre ``mark_done``."""
        mark_done(self.conn, task_id, result_ref=result_ref)
        self.conn.commit()

    def fail(self, task_id: int, error_msg: str) -> None:
        """Marca task como ``failed``. Wrapper sobre ``mark_failed``."""
        mark_failed(self.conn, task_id, error_msg)
        self.conn.commit()

    def reset_running(self, obra: str) -> int:
        """
        Devolve todas as tasks ``running`` sem ``finished_at`` para
        ``pending`` (cenário pós-crash). Limpa ``started_at``,
        preserva ``error_message`` se existia. Retorna o número de
        tasks afetadas.

        IMPORTANTE: chamada **manual** pelo operador. O recovery
        automático mid-process não é seguro — uma task pode estar
        rodando legitimamente em outro processo.
        """
        cur = self.conn.execute(
            """
            UPDATE tasks
               SET status = ?, started_at = NULL
             WHERE obra = ?
               AND status = ?
               AND (finished_at IS NULL OR finished_at = '')
            """,
            (TaskStatus.PENDING.value, obra, TaskStatus.RUNNING.value),
        )
        self.conn.commit()
        n = cur.rowcount or 0
        if n > 0:
            log.warning(
                "reset_running: %d task(s) %s -> %s para obra=%s",
                n, TaskStatus.RUNNING.value, TaskStatus.PENDING.value, obra,
            )
        return n

    def reset_failed(self, obra: str, *, task_type: TaskType | None = None) -> int:
        """
        Devolve tasks ``failed`` para ``pending`` (retry sob controle).
        Se ``task_type`` for fornecido, restringe ao tipo.

        Limpa ``started_at`` e ``finished_at``; preserva
        ``error_message`` para o histórico.
        """
        if task_type is None:
            cur = self.conn.execute(
                """
                UPDATE tasks
                   SET status = ?, started_at = NULL, finished_at = NULL
                 WHERE obra = ?
                   AND status = ?
                """,
                (TaskStatus.PENDING.value, obra, TaskStatus.FAILED.value),
            )
        else:
            cur = self.conn.execute(
                """
                UPDATE tasks
                   SET status = ?, started_at = NULL, finished_at = NULL
                 WHERE obra = ?
                   AND status = ?
                   AND task_type = ?
                """,
                (
                    TaskStatus.PENDING.value, obra,
                    TaskStatus.FAILED.value, task_type.value,
                ),
            )
        self.conn.commit()
        n = cur.rowcount or 0
        if n > 0:
            log.info(
                "reset_failed: %d task(s) %s -> %s (type=%s) obra=%s",
                n, TaskStatus.FAILED.value, TaskStatus.PENDING.value,
                task_type.value if task_type else "<any>", obra,
            )
        return n

    # ------------------------------------------------------------------
    # Conveniência — re-export do enqueue do orchestrator
    # ------------------------------------------------------------------

    def enqueue(self, task: Task) -> int:
        """Re-exporta ``orchestrator.enqueue`` para callers que só
        usam o manager."""
        return enqueue(self.conn, task)


__all__ = [
    "PipelineStateManager",
    "StatusReport",
]
