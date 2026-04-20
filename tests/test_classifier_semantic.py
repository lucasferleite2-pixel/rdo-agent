"""Testes do classificador semantico — Sprint 3 Camada 3.

Todos os casos usam mocks (FakeClient). NENHUMA chamada real a OpenAI.
Pattern espelha tests/test_classifier_quality_detector.py.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from rdo_agent.classifier import semantic_classifier
from rdo_agent.classifier.semantic_classifier import (
    MODEL,
    VALID_CATEGORIES,
    _classify_error_type,
    _compute_cost_usd,
    _is_retryable,
    _validate_response,
    classify_handler,
)
from rdo_agent.orchestrator import Task, TaskStatus, TaskType, init_db

# ---------------------------------------------------------------------------
# Exception factories
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
    def __init__(self, pt=120, ct=25):
        self.prompt_tokens = pt
        self.completion_tokens = ct


class _FakeChoice:
    def __init__(self, content: str):
        self.message = MagicMock()
        self.message.content = content


class _FakeCompletion:
    def __init__(self, content: str, pt=120, ct=25):
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


def _insert_classification(
    conn: sqlite3.Connection,
    *,
    cls_id: int = 1,
    source_file_id: str = "file_trans_01",
    semantic_status: str = "pending_classify",
    human_corrected_text: str | None = None,
    transcription_text: str = "texto original da transcricao",
) -> None:
    """Seed files (audio+txt), transcriptions, e classifications."""
    now = "2026-04-20T00:00:00Z"
    conn.execute(
        """INSERT OR IGNORE INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "file_audio_01", "EVERALDO", "10_media/audio01.opus", "audio",
            "a" * 64, 1000, "done", now,
        ),
    )
    conn.execute(
        """INSERT OR IGNORE INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            derived_from, derivation_method, semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            source_file_id, "EVERALDO", f"20_transcriptions/{source_file_id}.txt",
            "text", "b" * 64, 500, "file_audio_01",
            "whisper-1", "awaiting_classification", now,
        ),
    )
    conn.execute(
        """INSERT OR IGNORE INTO transcriptions (
            obra, file_id, text, language, confidence, low_confidence,
            api_call_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("EVERALDO", source_file_id, transcription_text, "portuguese", 0.6, 0, None, now),
    )
    conn.execute(
        """INSERT INTO classifications (
            id, obra, source_file_id, source_type,
            quality_flag, quality_reasoning, human_review_needed,
            human_reviewed, human_corrected_text,
            quality_api_call_id, quality_model,
            source_sha256, semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            cls_id, "EVERALDO", source_file_id, "transcription",
            "coerente", "ok", 0,
            1 if human_corrected_text else 0, human_corrected_text,
            None, "gpt-4o-mini-2024-07-18",
            "c" * 64, semantic_status, now,
        ),
    )
    conn.commit()


@pytest.fixture
def prepared_db(tmp_path, monkeypatch) -> sqlite3.Connection:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-key")
    from rdo_agent.utils import config as config_mod
    if hasattr(config_mod, "_cached"):
        config_mod._cached = None

    conn = init_db(tmp_path)
    _insert_classification(conn)
    return conn


@pytest.fixture
def make_task():
    def _m(classifications_id: int = 1) -> Task:
        return Task(
            id=1,
            task_type=TaskType.CLASSIFY,
            payload={"classifications_id": classifications_id},
            status=TaskStatus.RUNNING,
            depends_on=[],
            obra="EVERALDO",
            created_at="2026-04-20T00:00:00Z",
            priority=0,
        )
    return _m


# ---------------------------------------------------------------------------
# Pure helpers
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
    assert _compute_cost_usd(1000, 1000, MODEL) == pytest.approx(0.00075, rel=1e-6)


def test_validate_response_happy_single_label():
    cats, conf, reasoning = _validate_response(
        {"categories": ["pagamento"], "confidence": 0.9, "reasoning": "chave pix"},
    )
    assert cats == ["pagamento"]
    assert conf == 0.9
    assert reasoning == "chave pix"


def test_validate_response_rejects_unknown_category():
    with pytest.raises(RuntimeError, match="unknown category"):
        _validate_response(
            {"categories": ["xyzzy"], "confidence": 0.5, "reasoning": "x"},
        )


def test_validate_response_rejects_confidence_out_of_range():
    with pytest.raises(RuntimeError, match="out of range"):
        _validate_response(
            {"categories": ["cronograma"], "confidence": 1.5, "reasoning": "x"},
        )


def test_validate_response_rejects_more_than_two_categories():
    with pytest.raises(RuntimeError, match="invalid categories"):
        _validate_response(
            {"categories": ["pagamento", "cronograma", "material"],
             "confidence": 0.5, "reasoning": "x"},
        )


# ---------------------------------------------------------------------------
# Handler happy-paths (per category)
# ---------------------------------------------------------------------------


def test_handler_happy_negociacao_comercial(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([
        _FakeCompletion(json.dumps({
            "categories": ["negociacao_comercial"],
            "confidence": 0.9,
            "reasoning": "contraproposta de valor",
        })),
    ])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)

    result_ref = classify_handler(make_task(), prepared_db)
    assert result_ref.startswith("classifications:1")

    row = prepared_db.execute(
        "SELECT categories, confidence_model, reasoning, semantic_status, "
        "classifier_model FROM classifications WHERE id=1"
    ).fetchone()
    assert json.loads(row[0]) == ["negociacao_comercial"]
    assert row[1] == 0.9
    assert row[2] == "contraproposta de valor"
    assert row[3] == "classified"
    assert row[4] == MODEL


def test_handler_happy_pagamento(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([
        _FakeCompletion(json.dumps({
            "categories": ["pagamento"],
            "confidence": 0.85,
            "reasoning": "pedido de chave pix",
        })),
    ])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)
    classify_handler(make_task(), prepared_db)

    row = prepared_db.execute(
        "SELECT categories FROM classifications WHERE id=1"
    ).fetchone()
    assert json.loads(row[0]) == ["pagamento"]


def test_handler_happy_cronograma(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([
        _FakeCompletion(json.dumps({
            "categories": ["cronograma"],
            "confidence": 0.8,
            "reasoning": "combinando encontro",
        })),
    ])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)
    classify_handler(make_task(), prepared_db)

    row = prepared_db.execute(
        "SELECT categories FROM classifications WHERE id=1"
    ).fetchone()
    assert json.loads(row[0]) == ["cronograma"]


def test_handler_multi_label_pagamento_plus_negociacao(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([
        _FakeCompletion(json.dumps({
            "categories": ["pagamento", "negociacao_comercial"],
            "confidence": 0.75,
            "reasoning": "adiantamento dentro de negociacao",
        })),
    ])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)
    classify_handler(make_task(), prepared_db)

    row = prepared_db.execute(
        "SELECT categories FROM classifications WHERE id=1"
    ).fetchone()
    cats = json.loads(row[0])
    assert cats == ["pagamento", "negociacao_comercial"]
    assert len(cats) == 2


# ---------------------------------------------------------------------------
# Input selection (human_corrected_text vs transcription.text)
# ---------------------------------------------------------------------------


def test_handler_uses_human_corrected_when_present(tmp_path, monkeypatch, make_task):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-key")
    from rdo_agent.utils import config as config_mod
    if hasattr(config_mod, "_cached"):
        config_mod._cached = None

    conn = init_db(tmp_path)
    _insert_classification(
        conn,
        human_corrected_text="texto manualmente corrigido",
        transcription_text="texto original (que NAO deve ser enviado)",
    )

    fake = _FakeClient([
        _FakeCompletion(json.dumps({
            "categories": ["material"], "confidence": 0.7, "reasoning": "x",
        })),
    ])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)

    classify_handler(make_task(), conn)
    # inspecionar user-content enviado
    call_kwargs = fake.chat.completions.calls[0]
    user_msg = [m for m in call_kwargs["messages"] if m["role"] == "user"][0]
    assert user_msg["content"] == "texto manualmente corrigido"
    assert "NAO deve ser enviado" not in user_msg["content"]


def test_handler_uses_transcription_when_no_human_correction(
    prepared_db, make_task, monkeypatch,
):
    # fixture default: human_corrected_text=None, transcription_text='texto original...'
    fake = _FakeClient([
        _FakeCompletion(json.dumps({
            "categories": ["off_topic"], "confidence": 0.5, "reasoning": "x",
        })),
    ])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)
    classify_handler(make_task(), prepared_db)
    call_kwargs = fake.chat.completions.calls[0]
    user_msg = [m for m in call_kwargs["messages"] if m["role"] == "user"][0]
    assert user_msg["content"] == "texto original da transcricao"


# ---------------------------------------------------------------------------
# Skip rejected + idempotency
# ---------------------------------------------------------------------------


def test_handler_skips_rejected_without_api_call(tmp_path, monkeypatch, make_task):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-key")
    from rdo_agent.utils import config as config_mod
    if hasattr(config_mod, "_cached"):
        config_mod._cached = None

    conn = init_db(tmp_path)
    _insert_classification(conn, semantic_status="rejected")

    fake = _FakeClient([])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)

    result_ref = classify_handler(make_task(), conn)
    assert "skipped_rejected" in result_ref
    assert fake.chat.completions.calls == []
    # status nao mudou
    row = conn.execute(
        "SELECT semantic_status FROM classifications WHERE id=1"
    ).fetchone()
    assert row[0] == "rejected"


def test_handler_idempotency_skips_classified(tmp_path, monkeypatch, make_task):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-key")
    from rdo_agent.utils import config as config_mod
    if hasattr(config_mod, "_cached"):
        config_mod._cached = None

    conn = init_db(tmp_path)
    _insert_classification(conn, semantic_status="classified")
    # popula categories ja (como se Fase 3 ja tivesse rodado)
    conn.execute(
        "UPDATE classifications SET categories=?, confidence_model=?, "
        "reasoning=?, classifier_model=? WHERE id=1",
        (json.dumps(["pagamento"]), 0.9, "ja classificado", MODEL),
    )
    conn.commit()

    fake = _FakeClient([])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)

    result_ref = classify_handler(make_task(), conn)
    assert "skipped_classified" in result_ref
    assert fake.chat.completions.calls == []


# ---------------------------------------------------------------------------
# Retry + error handling
# ---------------------------------------------------------------------------


def test_retry_recovers_after_connection_error(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([
        make_connection_error(),
        _FakeCompletion(json.dumps({
            "categories": ["solicitacao_servico"], "confidence": 0.6, "reasoning": "x",
        })),
    ])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)
    monkeypatch.setattr(semantic_classifier, "RETRY_DELAYS_SEC", (0.0, 0.0))

    classify_handler(make_task(), prepared_db)
    api_rows = prepared_db.execute(
        "SELECT error_type FROM api_calls ORDER BY id"
    ).fetchall()
    assert len(api_rows) == 2
    assert api_rows[0][0] == "connection"
    assert api_rows[1][0] is None


def test_auth_error_propagates_immediately(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([make_auth_error()])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)
    monkeypatch.setattr(semantic_classifier, "RETRY_DELAYS_SEC", (0.0, 0.0))

    with pytest.raises(openai.AuthenticationError):
        classify_handler(make_task(), prepared_db)

    row = prepared_db.execute(
        "SELECT semantic_status FROM classifications WHERE id=1"
    ).fetchone()
    # status nao mudou em caso de auth failure
    assert row[0] == "pending_classify"


def test_invalid_json_raises(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([_FakeCompletion("nao eh json valido")])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)
    monkeypatch.setattr(semantic_classifier, "RETRY_DELAYS_SEC", (0.0, 0.0))

    with pytest.raises(RuntimeError, match="invalid JSON"):
        classify_handler(make_task(), prepared_db)


def test_unknown_category_in_response_raises(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([
        _FakeCompletion(json.dumps({
            "categories": ["fake_category"], "confidence": 0.5, "reasoning": "x",
        })),
    ])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)
    monkeypatch.setattr(semantic_classifier, "RETRY_DELAYS_SEC", (0.0, 0.0))

    with pytest.raises(RuntimeError, match="unknown category"):
        classify_handler(make_task(), prepared_db)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_payload_without_classifications_id_raises(prepared_db, monkeypatch):
    fake = _FakeClient([])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)

    bad_task = Task(
        id=1, task_type=TaskType.CLASSIFY, payload={},
        status=TaskStatus.RUNNING, depends_on=[], obra="EVERALDO",
        created_at="2026-04-20T00:00:00Z", priority=0,
    )
    with pytest.raises(ValueError, match="classifications_id"):
        classify_handler(bad_task, prepared_db)


def test_missing_classification_raises(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)

    with pytest.raises(RuntimeError, match="classification nao encontrada"):
        classify_handler(make_task(classifications_id=9999), prepared_db)


# ---------------------------------------------------------------------------
# api_calls integrity
# ---------------------------------------------------------------------------


def test_api_call_row_populated_correctly(prepared_db, make_task, monkeypatch):
    fake = _FakeClient([
        _FakeCompletion(
            json.dumps({
                "categories": ["material"], "confidence": 0.8, "reasoning": "x",
            }),
            pt=200, ct=50,
        ),
    ])
    monkeypatch.setattr(semantic_classifier, "_get_openai_client", lambda: fake)
    classify_handler(make_task(), prepared_db)

    row = prepared_db.execute(
        """SELECT provider, endpoint, tokens_input, tokens_output, model,
                  error_type, cost_usd FROM api_calls"""
    ).fetchone()
    assert row[0] == "openai"
    assert row[1] == "chat.completions"
    assert row[2] == 200
    assert row[3] == 50
    assert row[4] == MODEL
    assert row[5] is None
    assert row[6] == pytest.approx(
        200 / 1000 * 0.00015 + 50 / 1000 * 0.00060, rel=1e-6,
    )


# ---------------------------------------------------------------------------
# Categories constant matches ADR-002 exactly
# ---------------------------------------------------------------------------


def test_valid_categories_matches_adr002():
    assert VALID_CATEGORIES == (
        "negociacao_comercial",
        "pagamento",
        "cronograma",
        "especificacao_tecnica",
        "solicitacao_servico",
        "material",
        "reporte_execucao",
        "off_topic",
        "ilegivel",
    )
