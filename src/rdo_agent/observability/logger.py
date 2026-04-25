"""
Structured JSONL logger — Sessão 6 (dívida #53).

Emite eventos como JSON 1-linha em
``~/.rdo-agent/logs/<corpus_id>/<YYYY-MM-DD>.jsonl``. Cada linha é um
record com schema previsível, fácil de parse via ``jq`` ou Python.

Eventos emitidos por ``StructuredLogger``:

- ``stage_start``      — início de uma etapa do pipeline
- ``stage_done``       — etapa concluída (com duration_ms)
- ``stage_failed``     — etapa falhou (com error_type/error_msg)
- ``cost``             — chamada de API com tokens/cost
- ``retry``            — tentativa de retry após falha transiente

Helpers de leitura:

- ``iter_log_records(corpus_id, log_root=...)`` — itera todos os
  records de um corpus, ordenados por timestamp.
- ``aggregate_logs(corpus_id, log_root=...)`` — sumariza counts por
  event_type, custo total, duração média por stage.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_LOG_ROOT: Path = Path.home() / ".rdo-agent" / "logs"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class StructuredLogger:
    """
    Emite eventos de pipeline como JSONL em disco.

    Args:
        corpus_id: identificador da obra/canal (escopo do log).
        log_root: raiz dos logs (default ``~/.rdo-agent/logs``).
            Override útil em testes (tmp_path).
    """

    def __init__(self, corpus_id: str, log_root: Path | None = None):
        if not corpus_id:
            raise ValueError("corpus_id obrigatório")
        self.corpus_id = corpus_id
        self.log_root = log_root or DEFAULT_LOG_ROOT
        self.corpus_dir = self.log_root / corpus_id
        self.corpus_dir.mkdir(parents=True, exist_ok=True)

    def _log_path(self) -> Path:
        return self.corpus_dir / f"{_today_utc()}.jsonl"

    def emit(self, event_type: str, **fields) -> None:
        """
        Emite 1 record JSONL. Append-only; nunca sobrescreve.

        Caracteres Unicode (acentos PT-BR, emojis em payload de
        debug) são preservados via ``ensure_ascii=False``.
        """
        record = {
            "timestamp": _now_iso(),
            "corpus_id": self.corpus_id,
            "event_type": event_type,
        }
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False, default=str)
        path = self._log_path()
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # Conveniência para os 5 event_types canônicos --------------------

    def stage_start(
        self, stage: str, source_id: str | int, **extra,
    ) -> None:
        self.emit(
            "stage_start", stage=stage, source_id=source_id, **extra,
        )

    def stage_done(
        self, stage: str, source_id: str | int, duration_ms: int, **extra,
    ) -> None:
        self.emit(
            "stage_done", stage=stage, source_id=source_id,
            duration_ms=duration_ms, **extra,
        )

    def stage_failed(
        self, stage: str, source_id: str | int,
        error_type: str, error_msg: str, **extra,
    ) -> None:
        self.emit(
            "stage_failed", stage=stage, source_id=source_id,
            error_type=error_type, error_msg=error_msg, **extra,
        )

    def cost_event(
        self, api: str, model: str,
        tokens_in: int, tokens_out: int, cost_usd: float, **extra,
    ) -> None:
        self.emit(
            "cost", api=api, model=model,
            tokens_in=tokens_in, tokens_out=tokens_out,
            cost_usd=cost_usd, **extra,
        )

    def retry(
        self, stage: str, source_id: str | int, attempt: int, reason: str,
    ) -> None:
        self.emit(
            "retry", stage=stage, source_id=source_id,
            attempt=attempt, reason=reason,
        )


# ----------------------------------------------------------------------
# Helpers de leitura
# ----------------------------------------------------------------------


def iter_log_records(
    corpus_id: str, log_root: Path | None = None,
) -> Iterator[dict]:
    """
    Itera todos os records JSONL do ``corpus_id`` ordenados por
    nome de arquivo (que é a data UTC), depois por linha. Linhas
    inválidas são silenciosamente ignoradas (resiliência).
    """
    root = log_root or DEFAULT_LOG_ROOT
    corpus_dir = root / corpus_id
    if not corpus_dir.exists():
        return
    for log_file in sorted(corpus_dir.glob("*.jsonl")):
        with log_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


@dataclass
class LogAggregate:
    """Sumário de logs de um corpus."""

    corpus_id: str
    event_counts: dict[str, int] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    cost_by_api: dict[str, float] = field(default_factory=dict)
    durations_by_stage_ms: dict[str, list[int]] = field(
        default_factory=dict,
    )
    failures_by_stage: dict[str, int] = field(default_factory=dict)
    error_types: dict[str, int] = field(default_factory=dict)
    total_records: int = 0


def aggregate_logs(
    corpus_id: str, log_root: Path | None = None,
) -> LogAggregate:
    """
    Lê todos os JSONL de um corpus e agrega métricas básicas:

    - contagem por event_type
    - custo total e por API
    - lista de durações por stage (caller pode tirar média/mediana)
    - falhas por stage e por error_type
    """
    agg = LogAggregate(corpus_id=corpus_id)
    for record in iter_log_records(corpus_id, log_root):
        agg.total_records += 1
        et = record.get("event_type", "unknown")
        agg.event_counts[et] = agg.event_counts.get(et, 0) + 1

        if et == "cost":
            cost = float(record.get("cost_usd") or 0)
            api = record.get("api") or "unknown"
            agg.total_cost_usd += cost
            agg.cost_by_api[api] = agg.cost_by_api.get(api, 0.0) + cost
        elif et == "stage_done":
            stage = record.get("stage") or "unknown"
            ms = int(record.get("duration_ms") or 0)
            agg.durations_by_stage_ms.setdefault(stage, []).append(ms)
        elif et == "stage_failed":
            stage = record.get("stage") or "unknown"
            err = record.get("error_type") or "unknown"
            agg.failures_by_stage[stage] = (
                agg.failures_by_stage.get(stage, 0) + 1
            )
            agg.error_types[err] = agg.error_types.get(err, 0) + 1

    return agg
