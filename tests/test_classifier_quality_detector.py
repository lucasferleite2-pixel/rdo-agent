"""Testes do detector de qualidade — Sprint 3 Fase 1."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from rdo_agent.classifier import quality_detector
from rdo_agent.classifier.quality_detector import (
    MODEL,
    _classify_error_type,
    _compute_cost_usd,
    _is_retryable,
    detect_quality_handler,
)
from rdo_agent.orchestrator import Task, TaskStatus, TaskType, init_db

# ---------------------------------------------------------------------------
# Exception factories (mesmo padrao de test_transcriber.py)
# ---------------------------------------------------------------------------

def _req() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _resp(code: int) -> httpx.Response:
    return httpx.Response(status_code=code, request=_req())


def make_connection_error():
    return openai.APIConnectionError(request=_req())


def make_rate_limit_error():
    return openai.RateLimitError("rate", response=_resp(429), body=None)


def make_auth_error():
    return openai.AuthenticationError("invalid key", response=_resp(401), body=None)


# ---------------------------------------------------------------------------
# FakeClient
# ---------------------------------------------------------------------------

class _FakeUsage:
    def __init__(self, pt=100, ct=30):
        self.prompt_tokens = pt
        self.completion_tokens = ct


class _FakeChoice:
    def __init__(self, content: str):
        self.message = MagicMock()
        self.message.content = content


class _FakeCompletion:
    def __init__(self, content: str, pt=100, ct=30):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage(pt, ct)


class _FakeChatCompletions:
    def __init__(self, queue: list):
        self._queue = list(queue)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeChat:
    def __init__(self, queue: list):
        self.completions = _FakeChatCompletions(queue)


class _FakeClient:
    def __init__(self, queue: list):
        self.chat = _FakeChat(queue)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def prepared_db(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-key")
    from rdo_agent.utils import config as config_mod
    if hasattr(config_mod, "_cached"):
        config_mod._cached = None

    conn = init_db(tmp_path)
    conn.execute(
        """INSERT INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "file_audio_01", "EVERALDO", "10_media/audio01.opus", "audio",
            "a" * 64, 1000, "done", "2026-04-20T00:00:00Z",
        ),
    )
    conn.execute(
        """INSERT INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            derived_from, derivation_method, semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "file_trans_01", "EVERALDO", "20_transcriptions/audio01.txt",
            "text", "b" * 64, 500, "file_audio_01",
            "whisper-1", "awaiting_classification", "2026-04-20T00:00:00Z",
        ),
    )
    conn.execute(
        """INSERT INTO transcriptions (
            obra, file_id, text, language, confidence, low_confidence,
            api_call_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "EVERALDO", "file_trans_01",
            "O Lucas, por enquanto nao, eu comprei uma MIG nova, entendeu?",
            "portuguese", 0.6, 0, None, "2026-04-20T00:00:00Z",
        ),
    )
    conn.commit()
    return conn


@pytest.fixture
def make_task():
    def _m(file_id="file_trans_01"):
        return Task(
            id=1,
            task_type=TaskType.DETECT_QUALITY,
            payload={"transcription_file_id": file_id},
            status=TaskStatus.RUNNING,
            depends_on=[],
            obra="EVERALDO",
            created_at="2026-04-20T00:00:00Z",
            priority=0,
        )
    return _m


# ---------------------------------------------------------------------------
# Unit: pure helpers
# ---------------------------------------------------------------------------

def test_classify_error_type_mapping():
    assert _classify_error_type(make_connection_error()) == "connection"
    assert _classify_error_type(make_rate_limit_error()) == "rate_limit"
    assert _classify_error_type(make_auth_error()) == "auth_error"
    assert _classify_error_type(ValueError("generic")) == "api_error"


def test_is_retryable_only_transient():
    assert _is_retryable(make_connection_error()) is True
    assert _is_retryable(make_rate_limit_error()) is True
    assert _is_retryable(make_auth_error()) is False


def test_compute_cost_usd_matches_pricing_table():
    cost = _compute_cost_usd(1000, 1000, MODEL)
    assert cost == pytest.approx(0.00075, rel=1e-6)


# ---------------------------------------------------------------------------
# Handler: happy paths for each flag
# ---------------------------------------------------------------------------

def test_handler_flag_coerente_creates_pending_classify(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([
        _FakeCompletion(json.dumps({
            "flag": "coerente",
            "reasoning": "texto claro sobre maquina MIG",
        })),
    ])
    monkeypatch.setattr(quality_detector, "_get_openai_client", lambda: fake)

    result = detect_quality_handler(make_task(), prepared_db)
    assert result.startswith("classifications:")

    row = prepared_db.execute(
        "SELECT quality_flag, human_review_needed, semantic_status FROM classifications"
    ).fetchone()
    assert row[0] == "coerente"
    assert row[1] == 0
    assert row[2] == "pending_classify"


def test_handler_flag_suspeita_creates_pending_review(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([
        _FakeCompletion(json.dumps({
            "flag": "suspeita",
            "reasoning": "passagens incoerentes no meio",
        })),
    ])
    monkeypatch.setattr(quality_detector, "_get_openai_client", lambda: fake)

    detect_quality_handler(make_task(), prepared_db)
    row = prepared_db.execute(
        "SELECT quality_flag, human_review_needed, semantic_status FROM classifications"
    ).fetchone()
    assert row[0] == "suspeita"
    assert row[1] == 1
    assert row[2] == "pending_review"


def test_handler_flag_ilegivel_creates_pending_review(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([
        _FakeCompletion(json.dumps({
            "flag": "ilegivel",
            "reasoning": "loop de palavra repetida",
        })),
    ])
    monkeypatch.setattr(quality_detector, "_get_openai_client", lambda: fake)

    detect_quality_handler(make_task(), prepared_db)
    row = prepared_db.execute(
        "SELECT quality_flag, human_review_needed, semantic_status FROM classifications"
    ).fetchone()
    assert row[0] == "ilegivel"
    assert row[1] == 1
    assert row[2] == "pending_review"


# ---------------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------------

def test_retry_recovers_after_connection_error(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([
        make_connection_error(),
        _FakeCompletion(json.dumps({"flag": "coerente", "reasoning": "ok"})),
    ])
    monkeypatch.setattr(quality_detector, "_get_openai_client", lambda: fake)
    monkeypatch.setattr(quality_detector, "RETRY_DELAYS_SEC", (0.0, 0.0))

    detect_quality_handler(make_task(), prepared_db)
    api_rows = prepared_db.execute(
        "SELECT error_type FROM api_calls ORDER BY id"
    ).fetchall()
    assert len(api_rows) == 2
    assert api_rows[0][0] == "connection"
    assert api_rows[1][0] is None


def test_auth_error_propagates_immediately(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([make_auth_error()])
    monkeypatch.setattr(quality_detector, "_get_openai_client", lambda: fake)
    monkeypatch.setattr(quality_detector, "RETRY_DELAYS_SEC", (0.0, 0.0))

    with pytest.raises(openai.AuthenticationError):
        detect_quality_handler(make_task(), prepared_db)

    api_rows = prepared_db.execute(
        "SELECT error_type FROM api_calls ORDER BY id"
    ).fetchall()
    assert len(api_rows) == 1
    assert api_rows[0][0] == "auth_error"


def test_retry_exhaustion_raises(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([
        make_connection_error(),
        make_connection_error(),
        make_connection_error(),
    ])
    monkeypatch.setattr(quality_detector, "_get_openai_client", lambda: fake)
    monkeypatch.setattr(quality_detector, "RETRY_DELAYS_SEC", (0.0, 0.0))

    with pytest.raises(openai.APIConnectionError):
        detect_quality_handler(make_task(), prepared_db)

    api_count = prepared_db.execute(
        "SELECT COUNT(*) FROM api_calls"
    ).fetchone()[0]
    assert api_count == 3

    cls_count = prepared_db.execute(
        "SELECT COUNT(*) FROM classifications"
    ).fetchone()[0]
    assert cls_count == 0


# ---------------------------------------------------------------------------
# Invalid model outputs
# ---------------------------------------------------------------------------

def test_invalid_json_raises(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([_FakeCompletion("nao eh json")])
    monkeypatch.setattr(quality_detector, "_get_openai_client", lambda: fake)
    monkeypatch.setattr(quality_detector, "RETRY_DELAYS_SEC", (0.0, 0.0))

    with pytest.raises(RuntimeError, match="invalid JSON"):
        detect_quality_handler(make_task(), prepared_db)


def test_unexpected_flag_raises(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([
        _FakeCompletion(json.dumps({"flag": "desconhecido", "reasoning": "x"})),
    ])
    monkeypatch.setattr(quality_detector, "_get_openai_client", lambda: fake)

    with pytest.raises(RuntimeError, match="unexpected flag"):
        detect_quality_handler(make_task(), prepared_db)


# ---------------------------------------------------------------------------
# Idempotency + input validation
# ---------------------------------------------------------------------------

def test_idempotency_skips_existing_classification(prepared_db, make_task, monkeypatch):
    prepared_db.execute(
        """INSERT INTO classifications (
            obra, source_file_id, source_type,
            quality_flag, human_review_needed,
            source_sha256, semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "EVERALDO", "file_trans_01", "transcription",
            "coerente", 0, "x" * 64, "pending_classify",
            "2026-04-20T00:00:00Z",
        ),
    )
    prepared_db.commit()

    fake = _FakeClient([])
    monkeypatch.setattr(quality_detector, "_get_openai_client", lambda: fake)

    result = detect_quality_handler(make_task(), prepared_db)
    assert result.startswith("classifications:")
    assert fake.chat.completions.calls == []


def test_missing_transcription_raises(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([])
    monkeypatch.setattr(quality_detector, "_get_openai_client", lambda: fake)

    with pytest.raises(RuntimeError, match="transcription nao encontrada"):
        detect_quality_handler(make_task("file_inexistente"), prepared_db)


def test_payload_without_file_id_raises(prepared_db, monkeypatch):
    fake = _FakeClient([])
    monkeypatch.setattr(quality_detector, "_get_openai_client", lambda: fake)

    bad_task = Task(
        id=1, task_type=TaskType.DETECT_QUALITY, payload={},
        status=TaskStatus.RUNNING, depends_on=[], obra="EVERALDO",
        created_at="2026-04-20T00:00:00Z", priority=0,
    )
    with pytest.raises(ValueError, match="transcription_file_id"):
        detect_quality_handler(bad_task, prepared_db)


# ---------------------------------------------------------------------------
# api_calls row integrity
# ---------------------------------------------------------------------------

def test_api_call_row_populated_correctly(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([
        _FakeCompletion(
            json.dumps({"flag": "coerente", "reasoning": "ok"}),
            pt=150, ct=40,
        ),
    ])
    monkeypatch.setattr(quality_detector, "_get_openai_client", lambda: fake)

    detect_quality_handler(make_task(), prepared_db)
    row = prepared_db.execute(
        """SELECT provider, endpoint, tokens_input, tokens_output, model,
                  error_type, cost_usd FROM api_calls"""
    ).fetchone()
    assert row[0] == "openai"
    assert row[1] == "chat.completions"
    assert row[2] == 150
    assert row[3] == 40
    assert row[4] == MODEL
    assert row[5] is None
    assert row[6] == pytest.approx(
        150 / 1000 * 0.00015 + 40 / 1000 * 0.00060, rel=1e-6,
    )
