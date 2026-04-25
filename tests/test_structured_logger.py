"""Testes do StructuredLogger e helpers de leitura — Sessao 6 / #53."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rdo_agent.observability import (
    StructuredLogger,
    aggregate_logs,
    iter_log_records,
)


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------------------
# Emissao basica
# ---------------------------------------------------------------------------


def test_logger_emits_jsonl(tmp_path):
    log_root = tmp_path / "logs"
    logger = StructuredLogger("CASE_X", log_root=log_root)
    logger.emit("custom_event", foo="bar", n=42)

    files = list((log_root / "CASE_X").glob("*.jsonl"))
    assert len(files) == 1
    records = _read_lines(files[0])
    assert len(records) == 1
    rec = records[0]
    assert rec["event_type"] == "custom_event"
    assert rec["corpus_id"] == "CASE_X"
    assert rec["foo"] == "bar"
    assert rec["n"] == 42
    assert "timestamp" in rec  # ISO8601


def test_logger_creates_directory_structure(tmp_path):
    """Logger cria log_root/<corpus_id>/ se não existir."""
    log_root = tmp_path / "nonexistent" / "deep" / "path"
    logger = StructuredLogger("CASE_NEW", log_root=log_root)
    assert (log_root / "CASE_NEW").is_dir()
    logger.emit("ping")
    assert any((log_root / "CASE_NEW").glob("*.jsonl"))


def test_logger_appends_not_overwrites(tmp_path):
    log_root = tmp_path / "logs"
    logger = StructuredLogger("CASE_APPEND", log_root=log_root)
    for i in range(5):
        logger.emit("tick", i=i)

    files = list((log_root / "CASE_APPEND").glob("*.jsonl"))
    records = _read_lines(files[0])
    assert len(records) == 5
    assert [r["i"] for r in records] == [0, 1, 2, 3, 4]


def test_logger_handles_unicode(tmp_path):
    """Acentos PT-BR + emoji em payload preservados (ensure_ascii=False)."""
    log_root = tmp_path / "logs"
    logger = StructuredLogger("CASE_UNICODE", log_root=log_root)
    logger.emit("note", text="Açafrão e ñoñoñó 🚧")
    files = list((log_root / "CASE_UNICODE").glob("*.jsonl"))
    raw = files[0].read_text(encoding="utf-8")
    # Caracteres preservados literalmente no JSON
    assert "Açafrão" in raw
    assert "🚧" in raw
    # E o parse de volta funciona
    rec = _read_lines(files[0])[0]
    assert rec["text"] == "Açafrão e ñoñoñó 🚧"


def test_logger_corpus_id_required(tmp_path):
    with pytest.raises(ValueError, match="corpus_id"):
        StructuredLogger("", log_root=tmp_path)


# ---------------------------------------------------------------------------
# Conveniencia para event_types canonicos
# ---------------------------------------------------------------------------


def test_logger_stage_lifecycle(tmp_path):
    log_root = tmp_path / "logs"
    logger = StructuredLogger("CASE_STAGE", log_root=log_root)
    logger.stage_start("transcribe", source_id=42)
    logger.stage_done("transcribe", source_id=42, duration_ms=12345)
    logger.stage_failed(
        "classify", source_id=43,
        error_type="rate_limit", error_msg="429 Too Many Requests",
    )
    logger.cost_event(
        "anthropic", "claude-sonnet-4-6",
        tokens_in=100, tokens_out=200, cost_usd=0.0035,
    )
    logger.retry("classify", source_id=43, attempt=1, reason="rate_limit")

    files = list((log_root / "CASE_STAGE").glob("*.jsonl"))
    records = _read_lines(files[0])
    types = [r["event_type"] for r in records]
    assert types == [
        "stage_start", "stage_done", "stage_failed", "cost", "retry",
    ]
    # Campos especificos
    assert records[1]["duration_ms"] == 12345
    assert records[2]["error_type"] == "rate_limit"
    assert records[3]["cost_usd"] == 0.0035


# ---------------------------------------------------------------------------
# iter_log_records
# ---------------------------------------------------------------------------


def test_iter_log_records_returns_empty_when_no_logs(tmp_path):
    log_root = tmp_path / "empty"
    out = list(iter_log_records("CASE_NONE", log_root=log_root))
    assert out == []


def test_iter_log_records_skips_invalid_lines(tmp_path):
    log_root = tmp_path / "logs"
    corpus_dir = log_root / "CASE_DIRTY"
    corpus_dir.mkdir(parents=True)
    log_file = corpus_dir / "2026-04-25.jsonl"
    log_file.write_text(
        '{"event_type":"valid","timestamp":"x"}\n'
        "this is garbage not json\n"
        "\n"
        '{"event_type":"valid2","timestamp":"y"}\n',
        encoding="utf-8",
    )
    out = list(iter_log_records("CASE_DIRTY", log_root=log_root))
    assert len(out) == 2
    assert out[0]["event_type"] == "valid"
    assert out[1]["event_type"] == "valid2"


# ---------------------------------------------------------------------------
# aggregate_logs
# ---------------------------------------------------------------------------


def test_aggregate_logs_counts_and_costs(tmp_path):
    log_root = tmp_path / "logs"
    logger = StructuredLogger("CASE_AGG", log_root=log_root)
    logger.stage_start("transcribe", source_id=1)
    logger.stage_done("transcribe", source_id=1, duration_ms=5000)
    logger.stage_done("transcribe", source_id=2, duration_ms=8000)
    logger.stage_failed(
        "classify", source_id=3,
        error_type="rate_limit", error_msg="429",
    )
    logger.cost_event("openai", "whisper-1", tokens_in=0, tokens_out=0, cost_usd=0.05)
    logger.cost_event(
        "anthropic", "claude-sonnet-4-6",
        tokens_in=100, tokens_out=200, cost_usd=0.0035,
    )
    logger.cost_event(
        "anthropic", "claude-sonnet-4-6",
        tokens_in=200, tokens_out=400, cost_usd=0.007,
    )

    agg = aggregate_logs("CASE_AGG", log_root=log_root)
    assert agg.total_records == 7
    assert agg.event_counts["stage_done"] == 2
    assert agg.event_counts["stage_failed"] == 1
    assert agg.event_counts["cost"] == 3

    assert agg.total_cost_usd == pytest.approx(0.05 + 0.0035 + 0.007)
    assert agg.cost_by_api["openai"] == pytest.approx(0.05)
    assert agg.cost_by_api["anthropic"] == pytest.approx(0.0035 + 0.007)

    assert sorted(agg.durations_by_stage_ms["transcribe"]) == [5000, 8000]
    assert agg.failures_by_stage["classify"] == 1
    assert agg.error_types["rate_limit"] == 1


def test_aggregate_logs_isolates_by_corpus(tmp_path):
    log_root = tmp_path / "logs"
    StructuredLogger("CASE_A", log_root=log_root).emit("ping")
    StructuredLogger("CASE_B", log_root=log_root).emit("ping")
    StructuredLogger("CASE_B", log_root=log_root).emit("ping")

    a = aggregate_logs("CASE_A", log_root=log_root)
    b = aggregate_logs("CASE_B", log_root=log_root)
    assert a.total_records == 1
    assert b.total_records == 2
