"""Testes BatchClassifier — Sessao 8, divida #46 nivel 3.

Mocka cliente OpenAI inteiro (files.create, batches.create,
batches.retrieve, files.content) — sem chamadas reais.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from rdo_agent.classifier.batch import (
    BatchClassifier,
    BatchRequest,
    BatchResult,
    BatchStatusInfo,
    migrate_batches_table,
    parse_batch_output_jsonl,
    serialize_batch_jsonl,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeFile:
    id: str = "file_test_001"


@dataclass
class _FakeBatch:
    id: str = "batch_test_001"
    status: str = "validating"
    output_file_id: str | None = None
    error_file_id: str | None = None
    errors: Any | None = None


class _FakeFileResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeFiles:
    def __init__(self):
        self.created: list[Any] = []
        self.contents: dict[str, str] = {}

    def create(self, file, purpose: str):
        self.created.append((file, purpose))
        return _FakeFile()

    def content(self, file_id: str) -> _FakeFileResponse:
        return _FakeFileResponse(self.contents.get(file_id, ""))


class _FakeBatches:
    def __init__(self, *, status_progression: list[str] | None = None):
        self.status_progression = status_progression or ["validating", "completed"]
        self._idx = 0
        self.created: list[dict] = []
        self.last_batch = _FakeBatch()

    def create(self, **kwargs):
        self.created.append(kwargs)
        self.last_batch = _FakeBatch(
            id="batch_test_001",
            status=self.status_progression[0],
        )
        return self.last_batch

    def retrieve(self, batch_id: str):
        st = self.status_progression[
            min(self._idx, len(self.status_progression) - 1)
        ]
        self._idx += 1
        b = _FakeBatch(id=batch_id, status=st)
        if st == "completed":
            b.output_file_id = "file_out_001"
        return b


class _FakeOpenAI:
    def __init__(
        self, *, status_progression: list[str] | None = None,
        output_jsonl: str = "",
    ):
        self.files = _FakeFiles()
        self.batches = _FakeBatches(status_progression=status_progression)
        self.files.contents["file_out_001"] = output_jsonl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = sqlite3.connect(tmp_path / "batches.db")
    c.row_factory = sqlite3.Row
    migrate_batches_table(c)
    return c


def _sample_requests(n: int = 3) -> list[BatchRequest]:
    return [
        BatchRequest(
            custom_id=f"req_{i:03d}",
            text=f"texto da mensagem {i}",
            system_prompt="Classifique a mensagem em categorias.",
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# serialize / parse
# ---------------------------------------------------------------------------


def test_serialize_batch_jsonl_creates_one_line_per_request():
    reqs = _sample_requests(3)
    raw = serialize_batch_jsonl(reqs)
    lines = [line for line in raw.splitlines() if line]
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["custom_id"] == "req_000"
    assert first["method"] == "POST"
    assert first["url"] == "/v1/chat/completions"
    assert "messages" in first["body"]
    assert first["body"]["messages"][0]["role"] == "system"
    assert first["body"]["messages"][1]["role"] == "user"


def test_parse_batch_output_jsonl_extracts_results():
    raw = "\n".join([
        json.dumps({
            "id": "out_001",
            "custom_id": "req_001",
            "response": {
                "status_code": 200,
                "body": {
                    "choices": [{"message": {"content": "{}"}}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 10},
                },
            },
            "error": None,
        }),
        json.dumps({
            "id": "out_002",
            "custom_id": "req_002",
            "response": None,
            "error": "rate_limit",
        }),
    ])
    results = parse_batch_output_jsonl(raw)
    assert len(results) == 2
    assert results[0].custom_id == "req_001"
    assert results[0].tokens_in == 50
    assert results[0].tokens_out == 10
    assert results[0].error is None
    assert results[1].error == "rate_limit"


def test_parse_batch_output_jsonl_skips_invalid_lines():
    raw = "\n".join([
        '{"custom_id": "ok", "response": {"body": {}}, "error": null}',
        "not json at all",
        "",
    ])
    results = parse_batch_output_jsonl(raw)
    assert len(results) == 1
    assert results[0].custom_id == "ok"


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migration_creates_batches_table(conn):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='batches'",
    ).fetchone()
    assert row is not None


def test_migration_idempotent(conn):
    # 2x migrate sem erro
    migrate_batches_table(conn)
    migrate_batches_table(conn)


# ---------------------------------------------------------------------------
# submit_batch
# ---------------------------------------------------------------------------


def test_submit_batch_creates_jsonl_and_registers(conn, tmp_path):
    fake = _FakeOpenAI()
    bc = BatchClassifier(
        conn, corpus_id="OBRA_T",
        client=fake, scratch_dir=tmp_path / "scratch",
    )

    batch_id = bc.submit_batch(_sample_requests(5))
    assert batch_id == "batch_test_001"

    # Files.create foi chamado
    assert len(fake.files.created) == 1
    file_obj, purpose = fake.files.created[0]
    assert purpose == "batch"

    # JSONL file foi escrito em scratch_dir
    jsonl_files = list((tmp_path / "scratch").glob("*.jsonl"))
    assert len(jsonl_files) == 1
    raw = jsonl_files[0].read_text(encoding="utf-8")
    assert raw.count("\n") == 4  # 5 lines = 4 newlines (last sem \n)

    # Row em batches table
    row = conn.execute(
        "SELECT corpus_id, purpose, request_count, status, input_file_id "
        "FROM batches WHERE id = ?",
        (batch_id,),
    ).fetchone()
    assert row["corpus_id"] == "OBRA_T"
    assert row["purpose"] == "classify"
    assert row["request_count"] == 5
    assert row["input_file_id"] == "file_test_001"


def test_submit_batch_empty_raises(conn):
    fake = _FakeOpenAI()
    bc = BatchClassifier(conn, corpus_id="OBRA_T", client=fake)
    with pytest.raises(ValueError, match="vazia"):
        bc.submit_batch([])


def test_submit_batch_no_client_raises(conn):
    bc = BatchClassifier(conn, corpus_id="OBRA_T", client=None)
    with pytest.raises(RuntimeError, match="sem client"):
        bc.submit_batch(_sample_requests(2))


# ---------------------------------------------------------------------------
# poll_batch
# ---------------------------------------------------------------------------


def test_poll_batch_returns_status_and_updates_db(conn, tmp_path):
    fake = _FakeOpenAI(
        status_progression=["validating", "in_progress", "completed"],
    )
    bc = BatchClassifier(
        conn, corpus_id="X", client=fake, scratch_dir=tmp_path,
    )
    batch_id = bc.submit_batch(_sample_requests(2))

    info1 = bc.poll_batch(batch_id)
    assert info1.status in ("validating", "in_progress")

    info2 = bc.poll_batch(batch_id)
    info3 = bc.poll_batch(batch_id)
    assert info3.status == "completed"
    assert info3.output_file_id == "file_out_001"

    # DB foi atualizado
    row = conn.execute(
        "SELECT status, completed_at, output_file_id FROM batches WHERE id = ?",
        (batch_id,),
    ).fetchone()
    assert row["status"] == "completed"
    assert row["completed_at"] is not None
    assert row["output_file_id"] == "file_out_001"


# ---------------------------------------------------------------------------
# fetch_results
# ---------------------------------------------------------------------------


def test_fetch_results_parses_completed_batch(conn, tmp_path):
    output_jsonl = "\n".join([
        json.dumps({
            "custom_id": f"req_{i:03d}",
            "response": {
                "status_code": 200,
                "body": {
                    "choices": [{"message": {"content": "{}"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                },
            },
            "error": None,
        })
        for i in range(3)
    ])
    fake = _FakeOpenAI(
        status_progression=["validating", "completed"],
        output_jsonl=output_jsonl,
    )
    bc = BatchClassifier(
        conn, corpus_id="X", client=fake, scratch_dir=tmp_path,
    )
    batch_id = bc.submit_batch(_sample_requests(3))

    # Poll ate completed
    bc.poll_batch(batch_id)
    info = bc.poll_batch(batch_id)
    assert info.status == "completed"

    results = bc.fetch_results(batch_id)
    assert len(results) == 3
    assert {r.custom_id for r in results} == {"req_000", "req_001", "req_002"}
    assert all(r.tokens_in == 10 for r in results)


def test_fetch_results_raises_when_not_completed(conn, tmp_path):
    fake = _FakeOpenAI(status_progression=["validating", "validating"])
    bc = BatchClassifier(
        conn, corpus_id="X", client=fake, scratch_dir=tmp_path,
    )
    batch_id = bc.submit_batch(_sample_requests(1))
    with pytest.raises(RuntimeError, match="nao esta completed"):
        bc.fetch_results(batch_id)


def test_fetch_results_unknown_batch_raises(conn):
    fake = _FakeOpenAI()
    bc = BatchClassifier(conn, corpus_id="X", client=fake)
    with pytest.raises(ValueError, match="nao encontrado"):
        bc.fetch_results("inexistente")


# ---------------------------------------------------------------------------
# list_batches
# ---------------------------------------------------------------------------


def test_list_batches_filters_by_status(conn, tmp_path):
    fake = _FakeOpenAI()
    bc = BatchClassifier(
        conn, corpus_id="X", client=fake, scratch_dir=tmp_path,
    )
    bc.submit_batch(_sample_requests(1))

    # Adiciona segundo batch com status diferente direto no DB
    conn.execute(
        "INSERT INTO batches (id, corpus_id, purpose, submitted_at, "
        "status, request_count) VALUES (?, ?, ?, ?, ?, ?)",
        ("batch_b", "X", "classify", "2026-04-25T00:00:00Z", "completed", 5),
    )
    conn.commit()

    pending = bc.list_batches(status="validating")
    completed = bc.list_batches(status="completed")
    all_b = bc.list_batches()

    assert len(pending) == 1
    assert len(completed) == 1
    assert len(all_b) == 2
