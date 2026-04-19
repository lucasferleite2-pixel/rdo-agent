"""Testes do visual_analyzer — GPT-4 Vision com retry + sentinel (Sprint 2 §Fase 3).

Cobre os mesmos casos da Fase 2 (decisões 1-6 da retrospectiva):
    1. Sucesso com JSON estruturado válido
    2. JSON mal-formado → sentinel + confidence=0
    3. JSON válido mas sem todos os campos obrigatórios → sentinel
    4. Retry em connection error (2 falhas + sucesso na 3ª)
    5. Retry em rate limit
    6. Auth error NÃO retry — propaga imediatamente
    7. Bad request NÃO retry
    8. API key ausente → RuntimeError claro
    9. Idempotência do handler (2 invocações → 1 row em files/visual_analyses)
   10. api_calls registra latency_ms + model + error_type=NULL em sucesso

Mocks only — nenhuma chamada real à API. Paralelo estrutural a
tests/test_transcriber.py para facilitar leitura comparada.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import openai
import pytest
from PIL import Image

from rdo_agent import visual_analyzer
from rdo_agent.orchestrator import Task, TaskStatus, TaskType, init_db
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_file
from rdo_agent.visual_analyzer import (
    MODEL,
    PRICING_USD_PER_TOKEN,
    REQUIRED_FIELDS,
    _get_openai_client,
    visual_analysis_handler,
)


# ---------------------------------------------------------------------------
# Construtores de exceções openai (precisam httpx.Request/Response reais)
# ---------------------------------------------------------------------------


def _req() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _resp(status_code: int) -> httpx.Response:
    return httpx.Response(status_code=status_code, request=_req())


def make_connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=_req())


def make_rate_limit_error() -> openai.RateLimitError:
    return openai.RateLimitError("rate limited", response=_resp(429), body=None)


def make_auth_error() -> openai.AuthenticationError:
    return openai.AuthenticationError("invalid key", response=_resp(401), body=None)


def make_bad_request_error() -> openai.BadRequestError:
    return openai.BadRequestError("bad image", response=_resp(400), body=None)


# ---------------------------------------------------------------------------
# FakeClient — imita client.chat.completions.create com queue
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeUsage:
    def __init__(self, prompt_tokens: int = 1200, completion_tokens: int = 300) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class _FakeChatCompletion:
    """Imita objeto retornado pelo SDK openai — tem .model_dump()."""

    def __init__(
        self,
        content: str,
        prompt_tokens: int = 1200,
        completion_tokens: int = 300,
    ) -> None:
        self._content = content
        self._usage = _FakeUsage(prompt_tokens, completion_tokens)

    def model_dump(self) -> dict:
        return {
            "choices": [{"message": {"content": self._content, "role": "assistant"}}],
            "usage": {
                "prompt_tokens": self._usage.prompt_tokens,
                "completion_tokens": self._usage.completion_tokens,
                "total_tokens": self._usage.total_tokens,
            },
        }


class _FakeCompletions:
    def __init__(self, queue: list) -> None:
        self._queue = list(queue)
        self.calls: list[dict] = []

    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, queue: list) -> None:
        self.chat = _FakeChat(_FakeCompletions(queue))


# ---------------------------------------------------------------------------
# Fixtures locais
# ---------------------------------------------------------------------------


@pytest.fixture
def vaults_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vaults"
    settings = config.Settings(
        openai_api_key="sk-test-dummy",
        anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=root,
        log_level="WARNING",
        dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)
    return root


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zera delays de retry para testes não travarem 4s."""
    monkeypatch.setattr(visual_analyzer, "RETRY_DELAYS_SEC", (0.0, 0.0))


@pytest.fixture
def seeded_image_vault(vaults_root: Path) -> dict:
    """Vault com imagem sintética 64x64 (via PIL) + row em files."""
    obra = "OBRA_VIS"
    vault = vaults_root / obra
    media_dir = vault / "10_media"
    media_dir.mkdir(parents=True)

    image_path = media_dir / "IMG-20260404-WA0001.jpg"
    img = Image.new("RGB", (64, 64), color=(120, 140, 90))
    img.save(image_path, "JPEG")

    conn = init_db(vault)
    image_sha = sha256_file(image_path)
    image_file_id = f"f_{image_sha[:12]}"
    conn.execute(
        """
        INSERT INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            timestamp_resolved, timestamp_source,
            semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            image_file_id, obra, f"10_media/{image_path.name}", "image", image_sha,
            image_path.stat().st_size,
            "2026-04-04T11:48:41+00:00", "filename",
            "awaiting_visual_analysis", "2026-04-17T00:00:00.000000Z",
        ),
    )
    conn.commit()
    return {
        "obra": obra,
        "vault": vault,
        "conn": conn,
        "image_file_id": image_file_id,
        "image_path": image_path,
    }


def _make_task(seeded: dict) -> Task:
    return Task(
        id=None,
        task_type=TaskType.VISUAL_ANALYSIS,
        payload={
            "file_id": seeded["image_file_id"],
            "file_path": f"10_media/{seeded['image_path'].name}",
        },
        status=TaskStatus.PENDING,
        depends_on=[],
        obra=seeded["obra"],
        created_at="",
    )


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, queue: list) -> _FakeClient:
    client = _FakeClient(queue)
    monkeypatch.setattr(visual_analyzer, "_get_openai_client", lambda: client)
    return client


def _valid_analysis_payload() -> dict:
    """JSON estruturado com os 4 campos obrigatórios preenchidos."""
    return {
        "elementos_construtivos": "Parede de alvenaria em bloco cerâmico, viga de concreto armado visível no canto superior, piso em contrapiso bruto.",
        "atividade_em_curso": "Assentamento de tijolos pela equipe de pedreiros; um trabalhador manipula argamassa em masseira.",
        "condicoes_ambiente": "Iluminação natural diurna, céu nublado, solo seco, canteiro organizado com materiais dispostos na lateral.",
        "observacoes_tecnicas": "Profissionais sem capacete visível — não-conformidade de EPI. Prumo aparenta estar sendo verificado.",
    }


def _valid_response(
    payload: dict | None = None,
    prompt_tokens: int = 1200,
    completion_tokens: int = 300,
) -> _FakeChatCompletion:
    content = json.dumps(payload if payload is not None else _valid_analysis_payload())
    return _FakeChatCompletion(content, prompt_tokens, completion_tokens)


# ---------------------------------------------------------------------------
# 1. Sucesso com JSON estruturado válido
# ---------------------------------------------------------------------------


def test_visual_handler_success_with_valid_json(
    seeded_image_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resposta JSON válida → visual_analyses populado, confidence=1.0."""
    _install_fake_client(monkeypatch, [_valid_response()])

    json_file_id = visual_analysis_handler(
        _make_task(seeded_image_vault), seeded_image_vault["conn"],
    )
    assert json_file_id and json_file_id.startswith("f_")

    va_row = seeded_image_vault["conn"].execute(
        "SELECT analysis_json, confidence, api_call_id FROM visual_analyses WHERE file_id = ?",
        (json_file_id,),
    ).fetchone()
    assert va_row is not None
    assert va_row["confidence"] == 1.0
    parsed = json.loads(va_row["analysis_json"])
    for field in REQUIRED_FIELDS:
        assert field in parsed and parsed[field], f"campo {field} ausente"
    assert "_sentinel" not in parsed

    # Arquivo .json em disco
    json_path = seeded_image_vault["vault"] / "30_visual" / f"{seeded_image_vault['image_path'].name}.analysis.json"
    assert json_path.exists()
    assert json.loads(json_path.read_text("utf-8"))["atividade_em_curso"]

    # Imagem-fonte marcada como analyzed
    src_status = seeded_image_vault["conn"].execute(
        "SELECT semantic_status FROM files WHERE file_id = ?",
        (seeded_image_vault["image_file_id"],),
    ).fetchone()["semantic_status"]
    assert src_status == "analyzed"


# ---------------------------------------------------------------------------
# 2. JSON mal-formado (decode error) → sentinel + confidence=0
# ---------------------------------------------------------------------------


def test_visual_handler_malformed_json_uses_sentinel(
    seeded_image_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Model devolve string que não é JSON válido → sentinel JSON em disco."""
    import logging

    bad = _FakeChatCompletion("isto { não é ) JSON válido", 1000, 50)
    _install_fake_client(monkeypatch, [bad])

    with caplog.at_level(logging.WARNING, logger="rdo_agent.visual_analyzer"):
        json_file_id = visual_analysis_handler(
            _make_task(seeded_image_vault), seeded_image_vault["conn"],
        )

    va_row = seeded_image_vault["conn"].execute(
        "SELECT analysis_json, confidence FROM visual_analyses WHERE file_id = ?",
        (json_file_id,),
    ).fetchone()
    assert va_row["confidence"] == 0.0
    parsed = json.loads(va_row["analysis_json"])
    assert parsed["_sentinel"] == "malformed_json_response"
    assert parsed["reason"].startswith("json_decode_error")
    assert parsed["source_sha256"]  # sentinel preserva unicidade de file_id
    # Campos obrigatórios preenchidos como "não identificado"
    for field in REQUIRED_FIELDS[:3]:
        assert parsed[field] == "não identificado"
    assert any("JSON inválido" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 3. JSON válido mas campos obrigatórios faltando → sentinel
# ---------------------------------------------------------------------------


def test_visual_handler_missing_required_fields_uses_sentinel(
    seeded_image_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON parseável mas sem 'observacoes_tecnicas' → schema inválido."""
    incomplete = {
        "elementos_construtivos": "x",
        "atividade_em_curso": "y",
        "condicoes_ambiente": "z",
        # observacoes_tecnicas ausente
    }
    _install_fake_client(monkeypatch, [_valid_response(incomplete)])

    json_file_id = visual_analysis_handler(
        _make_task(seeded_image_vault), seeded_image_vault["conn"],
    )

    va_row = seeded_image_vault["conn"].execute(
        "SELECT analysis_json, confidence FROM visual_analyses WHERE file_id = ?",
        (json_file_id,),
    ).fetchone()
    parsed = json.loads(va_row["analysis_json"])
    assert parsed["_sentinel"] == "malformed_json_response"
    assert "missing_fields:observacoes_tecnicas" in parsed["reason"]
    assert va_row["confidence"] == 0.0


# ---------------------------------------------------------------------------
# 4. Retry em connection error: 2 falhas + sucesso na 3ª
# ---------------------------------------------------------------------------


def test_retry_on_connection_error_succeeds_on_third(
    seeded_image_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    """APIConnectionError 2x → sucesso na 3ª. 3 rows em api_calls."""
    queue = [make_connection_error(), make_connection_error(), _valid_response()]
    _install_fake_client(monkeypatch, queue)

    json_file_id = visual_analysis_handler(
        _make_task(seeded_image_vault), seeded_image_vault["conn"],
    )
    assert json_file_id is not None

    rows = seeded_image_vault["conn"].execute(
        "SELECT error_type FROM api_calls ORDER BY id"
    ).fetchall()
    assert len(rows) == 3
    assert rows[0]["error_type"] == "connection"
    assert rows[1]["error_type"] == "connection"
    assert rows[2]["error_type"] is None  # sucesso


# ---------------------------------------------------------------------------
# 5. Retry em rate limit
# ---------------------------------------------------------------------------


def test_retry_on_rate_limit_succeeds_on_third(
    seeded_image_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    queue = [make_rate_limit_error(), make_rate_limit_error(), _valid_response()]
    _install_fake_client(monkeypatch, queue)

    visual_analysis_handler(_make_task(seeded_image_vault), seeded_image_vault["conn"])

    error_types = [
        r["error_type"]
        for r in seeded_image_vault["conn"].execute(
            "SELECT error_type FROM api_calls ORDER BY id"
        ).fetchall()
    ]
    assert error_types == ["rate_limit", "rate_limit", None]


# ---------------------------------------------------------------------------
# 6. Auth error NÃO retry — propaga imediatamente
# ---------------------------------------------------------------------------


def test_auth_error_does_not_retry(
    seeded_image_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    _install_fake_client(monkeypatch, [make_auth_error()])

    with pytest.raises(openai.AuthenticationError):
        visual_analysis_handler(
            _make_task(seeded_image_vault), seeded_image_vault["conn"],
        )

    rows = seeded_image_vault["conn"].execute(
        "SELECT error_type FROM api_calls"
    ).fetchall()
    assert len(rows) == 1  # não tentou de novo
    assert rows[0]["error_type"] == "auth_error"


# ---------------------------------------------------------------------------
# 7. Bad request NÃO retry
# ---------------------------------------------------------------------------


def test_bad_request_does_not_retry(
    seeded_image_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    _install_fake_client(monkeypatch, [make_bad_request_error()])

    with pytest.raises(openai.BadRequestError):
        visual_analysis_handler(
            _make_task(seeded_image_vault), seeded_image_vault["conn"],
        )

    rows = seeded_image_vault["conn"].execute(
        "SELECT error_type FROM api_calls"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["error_type"] == "bad_request"


# ---------------------------------------------------------------------------
# 8. API key ausente → RuntimeError claro
# ---------------------------------------------------------------------------


def test_api_key_missing_raises_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem OPENAI_API_KEY, _get_openai_client levanta RuntimeError orientativo."""
    settings = config.Settings(
        openai_api_key="",
        anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=tmp_path / "vaults",
        log_level="WARNING",
        dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY ausente"):
        _get_openai_client()


# ---------------------------------------------------------------------------
# 9. Idempotência do handler
# ---------------------------------------------------------------------------


def test_handler_is_idempotent(
    seeded_image_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2 invocações → 1 row em files/visual_analyses/media_derivations."""
    _install_fake_client(monkeypatch, [_valid_response(), _valid_response()])

    id_1 = visual_analysis_handler(
        _make_task(seeded_image_vault), seeded_image_vault["conn"],
    )
    id_2 = visual_analysis_handler(
        _make_task(seeded_image_vault), seeded_image_vault["conn"],
    )
    assert id_1 == id_2

    conn = seeded_image_vault["conn"]
    assert conn.execute(
        "SELECT COUNT(*) FROM files WHERE file_id = ?", (id_1,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM visual_analyses WHERE file_id = ?", (id_1,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM media_derivations WHERE derived_file_id = ?", (id_1,),
    ).fetchone()[0] == 1
    # api_calls tem 2 rows (cada call é evento de custo distinto)
    assert conn.execute("SELECT COUNT(*) FROM api_calls").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# 10. api_calls registra latency_ms + model + error_type=NULL em sucesso
# ---------------------------------------------------------------------------


def test_api_call_logs_latency_ms_and_model_and_cost(
    seeded_image_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sucesso com 1200 input + 300 output tokens → cost calculado por PRICING."""
    _install_fake_client(
        monkeypatch, [_valid_response(prompt_tokens=1200, completion_tokens=300)],
    )

    visual_analysis_handler(
        _make_task(seeded_image_vault), seeded_image_vault["conn"],
    )

    row = seeded_image_vault["conn"].execute(
        "SELECT latency_ms, model, error_type, cost_usd, response_hash, "
        "tokens_input, tokens_output FROM api_calls"
    ).fetchone()
    assert row["model"] == MODEL
    assert row["error_type"] is None
    assert row["latency_ms"] is not None
    assert row["latency_ms"] >= 0
    assert row["tokens_input"] == 1200
    assert row["tokens_output"] == 300
    # cost: 1200 * 0.15/1M + 300 * 0.60/1M
    pricing = PRICING_USD_PER_TOKEN[MODEL]
    expected = 1200 * pricing["input"] + 300 * pricing["output"]
    assert row["cost_usd"] == pytest.approx(expected, rel=1e-6)
    assert row["response_hash"] is not None
