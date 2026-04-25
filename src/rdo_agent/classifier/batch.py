"""
BatchClassifier — Sessão 8 / dívida #46 (nível 3).

Submete N requests via OpenAI Batch API (50% de desconto, completion
window 24h). Útil quando o pipeline pode tolerar latência alta em
troca de custo menor — ideal para corpus grande onde transcribe e
vision rodam em paralelo enquanto o batch de classify "marina".

Fluxo:

1. **submit_batch(messages, prompt_version)** — cria JSONL local,
   chama ``client.batches.create()``, registra em tabela ``batches``,
   retorna ``batch_id``.
2. **poll_batch(batch_id)** — consulta ``client.batches.retrieve()``,
   atualiza status na tabela.
3. **fetch_results(batch_id)** — quando ``status='completed'``, baixa
   ``output_file_id``, parseia JSONL de respostas, retorna lista
   de ``BatchResult``.

Tabela ``batches`` é criada via migration idempotente
``migrate_batches_table()``.

A integração com ``classify_pending`` orchestrator (próxima fase)
acumula mensagens não-cache-hit em buffer; quando enche
``BATCH_THRESHOLD`` (default 1000) ou ao fim do drenamento de tasks,
flush via ``submit_batch``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

BATCH_ENDPOINT = "/v1/chat/completions"
BATCH_COMPLETION_WINDOW = "24h"
BATCH_PURPOSE_CLASSIFY = "classify"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate_batches_table(conn: sqlite3.Connection) -> None:
    """Cria tabela ``batches`` (idempotente)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS batches (
            id              TEXT PRIMARY KEY,
            corpus_id       TEXT NOT NULL,
            purpose         TEXT NOT NULL,
            submitted_at    TEXT NOT NULL,
            status          TEXT NOT NULL,
            completed_at    TEXT,
            request_count   INTEGER NOT NULL,
            input_file_id   TEXT,
            output_file_id  TEXT,
            error_file_id   TEXT,
            completion_window TEXT,
            error_message   TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_batches_corpus_status "
        "ON batches(corpus_id, status)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass
class BatchRequest:
    """Uma unidade de classificação a submeter no batch."""

    custom_id: str  # ID único do caller para correlacionar resposta
    text: str       # Texto a classificar
    system_prompt: str
    model: str = "gpt-4o-mini"
    max_tokens: int = 256
    temperature: float = 0.0


@dataclass
class BatchResult:
    """Resultado parseado de uma request do batch."""

    custom_id: str
    response_body: dict | None
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class BatchStatusInfo:
    """Snapshot de status retornado por poll_batch."""

    batch_id: str
    status: str
    submitted_at: str
    completed_at: str | None = None
    request_count: int = 0
    output_file_id: str | None = None
    error_file_id: str | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Helpers de serialização (JSONL)
# ---------------------------------------------------------------------------


def serialize_batch_jsonl(requests: list[BatchRequest]) -> str:
    """
    Constrói o JSONL exigido pelo Batch API. 1 linha por request.

    Cada linha tem o shape:
        {"custom_id": "...", "method": "POST",
         "url": "/v1/chat/completions",
         "body": {"model": "...", "messages": [...], ...}}
    """
    lines = []
    for req in requests:
        line = {
            "custom_id": req.custom_id,
            "method": "POST",
            "url": BATCH_ENDPOINT,
            "body": {
                "model": req.model,
                "messages": [
                    {"role": "system", "content": req.system_prompt},
                    {"role": "user", "content": req.text},
                ],
                "max_tokens": req.max_tokens,
                "temperature": req.temperature,
            },
        }
        lines.append(json.dumps(line, ensure_ascii=False))
    return "\n".join(lines)


def parse_batch_output_jsonl(raw: str) -> list[BatchResult]:
    """
    Parseia JSONL de resposta do Batch API.

    Cada linha tem o shape:
        {"id": "...", "custom_id": "...",
         "response": {"status_code": 200, "body": {...}},
         "error": null}
    """
    results: list[BatchResult] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            log.warning("linha invalida no batch output: %r", line[:80])
            continue
        custom_id = d.get("custom_id", "")
        err = d.get("error")
        response = d.get("response") or {}
        body = response.get("body") if isinstance(response, dict) else None
        usage = (body or {}).get("usage") or {}
        results.append(BatchResult(
            custom_id=custom_id,
            response_body=body,
            error=str(err) if err else None,
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
        ))
    return results


# ---------------------------------------------------------------------------
# BatchClassifier
# ---------------------------------------------------------------------------


class BatchClassifier:
    """
    Wrapper sobre ``client.batches.*`` da OpenAI SDK.

    Args:
        conn: SQLite com tabela ``batches`` migrada.
        client: cliente OpenAI (passar ``None`` em testes para
            permitir injeção via ``submit_batch_with_client``).
        corpus_id: corpus pra registrar nas rows.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        corpus_id: str,
        *,
        client: object | None = None,
        scratch_dir: Path | None = None,
    ):
        self.conn = conn
        self.corpus_id = corpus_id
        self.client = client
        self.scratch_dir = scratch_dir or (
            Path.home() / ".rdo-agent" / "batches" / corpus_id
        )
        migrate_batches_table(conn)

    # ---- Submit ----

    def submit_batch(
        self, requests: list[BatchRequest],
        *, purpose: str = BATCH_PURPOSE_CLASSIFY,
    ) -> str:
        """
        Cria JSONL local, sobe via files.create, dispara
        batches.create. Retorna batch_id e registra em ``batches``.
        """
        if not requests:
            raise ValueError("submit_batch chamado com lista vazia")
        if self.client is None:
            raise RuntimeError(
                "BatchClassifier sem client; passe client= no construtor",
            )

        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = self.scratch_dir / f"batch_{_now_iso()}.jsonl"
        jsonl_path.write_text(
            serialize_batch_jsonl(requests), encoding="utf-8",
        )

        with jsonl_path.open("rb") as f:
            input_file = self.client.files.create(file=f, purpose="batch")

        batch = self.client.batches.create(
            input_file_id=input_file.id,
            endpoint=BATCH_ENDPOINT,
            completion_window=BATCH_COMPLETION_WINDOW,
            metadata={"corpus_id": self.corpus_id, "purpose": purpose},
        )

        self.conn.execute(
            """
            INSERT INTO batches
                (id, corpus_id, purpose, submitted_at, status,
                 request_count, input_file_id, completion_window)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch.id, self.corpus_id, purpose, _now_iso(),
                getattr(batch, "status", "validating"),
                len(requests), input_file.id, BATCH_COMPLETION_WINDOW,
            ),
        )
        self.conn.commit()
        log.info(
            "batch submitted: id=%s requests=%d corpus=%s",
            batch.id, len(requests), self.corpus_id,
        )
        return batch.id

    # ---- Poll ----

    def poll_batch(self, batch_id: str) -> BatchStatusInfo:
        """
        Consulta status; atualiza row em ``batches`` se mudou.
        """
        if self.client is None:
            raise RuntimeError("client ausente")

        batch = self.client.batches.retrieve(batch_id)
        status = getattr(batch, "status", "unknown")
        completed_at = (
            _now_iso() if status in ("completed", "failed", "cancelled")
            else None
        )
        output_file_id = getattr(batch, "output_file_id", None)
        error_file_id = getattr(batch, "error_file_id", None)
        error_msg = None
        errors_obj = getattr(batch, "errors", None)
        if errors_obj is not None:
            data = getattr(errors_obj, "data", None) or []
            if data:
                error_msg = str(data[0])

        self.conn.execute(
            """
            UPDATE batches
               SET status = ?, completed_at = COALESCE(?, completed_at),
                   output_file_id = COALESCE(?, output_file_id),
                   error_file_id = COALESCE(?, error_file_id),
                   error_message = COALESCE(?, error_message)
             WHERE id = ?
            """,
            (status, completed_at, output_file_id, error_file_id,
             error_msg, batch_id),
        )
        self.conn.commit()

        # Recupera valores atualizados
        row = self.conn.execute(
            "SELECT submitted_at, request_count FROM batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
        submitted_at = row[0] if row else _now_iso()
        request_count = int(row[1]) if row else 0

        return BatchStatusInfo(
            batch_id=batch_id, status=status,
            submitted_at=submitted_at, completed_at=completed_at,
            request_count=request_count,
            output_file_id=output_file_id, error_file_id=error_file_id,
            error_message=error_msg,
        )

    # ---- Fetch ----

    def fetch_results(self, batch_id: str) -> list[BatchResult]:
        """
        Quando ``status='completed'``, baixa output_file e parseia.
        """
        if self.client is None:
            raise RuntimeError("client ausente")

        row = self.conn.execute(
            "SELECT status, output_file_id FROM batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"batch {batch_id} nao encontrado em batches")
        status, output_file_id = row[0], row[1]
        if status != "completed":
            raise RuntimeError(
                f"batch {batch_id} nao esta completed (status={status})"
            )
        if not output_file_id:
            raise RuntimeError(f"batch {batch_id} sem output_file_id")

        # SDK retorna um HttpResponse-like; .text ou .read() -> bytes
        resp = self.client.files.content(output_file_id)
        if hasattr(resp, "text"):
            raw = resp.text
        elif hasattr(resp, "read"):
            raw = resp.read()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
        else:
            raw = str(resp)
        return parse_batch_output_jsonl(raw)

    # ---- Listagem (utilidade) ----

    def list_batches(self, *, status: str | None = None) -> list[BatchStatusInfo]:
        """Lista batches do corpus_id atual, opcionalmente filtrado."""
        sql = "SELECT id, status, submitted_at, completed_at, request_count, " \
              "output_file_id, error_file_id, error_message " \
              "FROM batches WHERE corpus_id = ?"
        params: list = [self.corpus_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY submitted_at DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [
            BatchStatusInfo(
                batch_id=r[0], status=r[1], submitted_at=r[2],
                completed_at=r[3], request_count=int(r[4] or 0),
                output_file_id=r[5], error_file_id=r[6], error_message=r[7],
            )
            for r in rows
        ]


__all__ = [
    "BATCH_COMPLETION_WINDOW",
    "BATCH_ENDPOINT",
    "BATCH_PURPOSE_CLASSIFY",
    "BatchClassifier",
    "BatchRequest",
    "BatchResult",
    "BatchStatusInfo",
    "migrate_batches_table",
    "parse_batch_output_jsonl",
    "serialize_batch_jsonl",
]
