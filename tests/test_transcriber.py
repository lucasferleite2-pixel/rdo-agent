"""Testes do transcriber — Whisper API com retry + sentinel (Sprint 2 §Fase 2)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import openai
import pytest

from rdo_agent import transcriber
from rdo_agent.orchestrator import Task, TaskStatus, TaskType, init_db
from rdo_agent.transcriber import (
    COST_USD_PER_MINUTE,
    LOW_CONFIDENCE_THRESHOLD,
    MODEL,
    _get_openai_client,
    transcribe_handler,
)
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_file

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_FIXTURE = FIXTURES_DIR / "whisper_golden_response.json"


# ---------------------------------------------------------------------------
# Construtores de exceções openai (precisam httpx.Request/Response reais)
# ---------------------------------------------------------------------------


def _req() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions")


def _resp(status_code: int) -> httpx.Response:
    return httpx.Response(status_code=status_code, request=_req())


def make_connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=_req())


def make_timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=_req())


def make_rate_limit_error() -> openai.RateLimitError:
    return openai.RateLimitError("rate limited", response=_resp(429), body=None)


def make_auth_error() -> openai.AuthenticationError:
    return openai.AuthenticationError("invalid key", response=_resp(401), body=None)


def make_bad_request_error() -> openai.BadRequestError:
    return openai.BadRequestError("bad audio", response=_resp(400), body=None)


# ---------------------------------------------------------------------------
# FakeClient — sem rede, queue de respostas/exceções
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Imita objeto retornado por openai — tem .model_dump()."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def model_dump(self) -> dict:
        return dict(self._data)


class _FakeTranscriptions:
    def __init__(self, queue: list) -> None:
        self._queue = list(queue)
        self.calls: list[dict] = []

    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


class _FakeAudio:
    def __init__(self, transcriptions: _FakeTranscriptions) -> None:
        self.transcriptions = transcriptions


class _FakeClient:
    def __init__(self, queue: list) -> None:
        self.audio = _FakeAudio(_FakeTranscriptions(queue))


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
    """Zera os delays de retry para tests não travarem 4s."""
    monkeypatch.setattr(transcriber, "RETRY_DELAYS_SEC", (0.0, 0.0))


@pytest.fixture
def seeded_audio_vault(vaults_root: Path) -> dict:
    """Vault com áudio sintético (bytes fake) + row em files."""
    obra = "OBRA_AUD"
    vault = vaults_root / obra
    media_dir = vault / "10_media"
    media_dir.mkdir(parents=True)

    audio_path = media_dir / "PTT-20260404-WA0003.opus"
    audio_path.write_bytes(b"fake opus bytes - mock doesnt need real audio")

    conn = init_db(vault)
    audio_sha = sha256_file(audio_path)
    audio_file_id = f"f_{audio_sha[:12]}"
    conn.execute(
        """
        INSERT INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            timestamp_resolved, timestamp_source,
            semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audio_file_id, obra, f"10_media/{audio_path.name}", "audio", audio_sha,
            audio_path.stat().st_size,
            "2026-04-04T11:48:41+00:00", "filename",
            "awaiting_transcription", "2026-04-17T00:00:00.000000Z",
        ),
    )
    conn.commit()
    return {
        "obra": obra,
        "vault": vault,
        "conn": conn,
        "audio_file_id": audio_file_id,
        "audio_path": audio_path,
    }


def _make_task(seeded: dict) -> Task:
    return Task(
        id=None,
        task_type=TaskType.TRANSCRIBE,
        payload={
            "file_id": seeded["audio_file_id"],
            "file_path": f"10_media/{seeded['audio_path'].name}",
        },
        status=TaskStatus.PENDING,
        depends_on=[],
        obra=seeded["obra"],
        created_at="",
    )


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, queue: list) -> _FakeClient:
    client = _FakeClient(queue)
    monkeypatch.setattr(transcriber, "_get_openai_client", lambda: client)
    return client


def _whisper_response_with_text(
    text: str = "Bom dia equipe, obra fluindo.",
    *,
    duration: float = 3.5,
    avg_logprobs: list[float] | None = None,
) -> dict:
    """Resposta verbose_json sintética. avg_logprob default = -0.2 → confidence ~0.82."""
    segments = []
    for i, lp in enumerate(avg_logprobs or [-0.2, -0.15]):
        segments.append({
            "id": i,
            "start": i * 1.0,
            "end": (i + 1) * 1.0,
            "text": text if i == 0 else "",
            "avg_logprob": lp,
            "compression_ratio": 1.2,
            "no_speech_prob": 0.01,
        })
    return {
        "text": text,
        "language": "portuguese",
        "duration": duration,
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# 1. Sucesso com golden fixture
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not GOLDEN_FIXTURE.exists(), reason="golden fixture não capturada")
def test_transcribe_handler_success_with_golden_fixture(
    seeded_audio_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    golden = json.loads(GOLDEN_FIXTURE.read_text(encoding="utf-8"))
    _install_fake_client(monkeypatch, [golden])

    txt_file_id = transcribe_handler(_make_task(seeded_audio_vault), seeded_audio_vault["conn"])
    assert txt_file_id and txt_file_id.startswith("f_")

    # transcriptions populado com golden
    tr_row = seeded_audio_vault["conn"].execute(
        "SELECT text, language, confidence, low_confidence FROM transcriptions WHERE file_id = ?",
        (txt_file_id,),
    ).fetchone()
    assert tr_row is not None
    assert tr_row["text"] == golden.get("text", "").strip() or tr_row["text"] == golden.get("text")
    # Golden tem duração real positiva → cost > 0
    api_row = seeded_audio_vault["conn"].execute(
        "SELECT cost_usd, error_type FROM api_calls"
    ).fetchone()
    assert api_row["error_type"] is None
    assert api_row["cost_usd"] > 0


# ---------------------------------------------------------------------------
# 2. Sentinel quando Whisper retorna text=""
# ---------------------------------------------------------------------------


def test_transcribe_handler_empty_text_uses_sentinel(
    seeded_audio_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Áudio silencioso → text="" → sentinel em disco + confidence=0, low_confidence=1."""
    import logging

    empty_response = {
        "text": "",
        "language": "portuguese",
        "duration": 0.5,
        "segments": [],
    }
    _install_fake_client(monkeypatch, [empty_response])

    with caplog.at_level(logging.WARNING, logger="rdo_agent.transcriber"):
        txt_file_id = transcribe_handler(_make_task(seeded_audio_vault), seeded_audio_vault["conn"])

    # transcriptions.text preservado como "" (contrato), confidence=0, low_confidence=1
    tr_row = seeded_audio_vault["conn"].execute(
        "SELECT text, confidence, low_confidence FROM transcriptions WHERE file_id = ?",
        (txt_file_id,),
    ).fetchone()
    assert tr_row["text"] == ""
    assert tr_row["confidence"] == 0.0
    assert tr_row["low_confidence"] == 1

    # .txt em disco contém sentinel com source_sha256
    txt_disk = (
        seeded_audio_vault["vault"] / "20_transcriptions"
        / f"{seeded_audio_vault['audio_path'].name}.transcription.txt"
    ).read_text(encoding="utf-8")
    assert "sem fala detectada" in txt_disk
    assert "source_sha256" in txt_disk
    assert "source_file_id" in txt_disk

    # log.warning emitido
    assert any("sem fala detectada" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 3. Low confidence flag quando avg_logprob baixo
# ---------------------------------------------------------------------------


def test_transcribe_handler_low_confidence_flag(
    seeded_audio_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """avg_logprob=-1.5 → exp(-1.5)≈0.22 → abaixo do threshold 0.5 → low_confidence=1."""
    resp = _whisper_response_with_text("texto ruidoso", avg_logprobs=[-1.5, -1.6])
    _install_fake_client(monkeypatch, [resp])

    txt_file_id = transcribe_handler(_make_task(seeded_audio_vault), seeded_audio_vault["conn"])
    tr_row = seeded_audio_vault["conn"].execute(
        "SELECT confidence, low_confidence FROM transcriptions WHERE file_id = ?",
        (txt_file_id,),
    ).fetchone()
    assert tr_row["confidence"] < LOW_CONFIDENCE_THRESHOLD
    assert tr_row["low_confidence"] == 1


# ---------------------------------------------------------------------------
# 4. Retry em connection error: 2 falhas + sucesso na 3ª
# ---------------------------------------------------------------------------


def test_retry_on_connection_error_succeeds_on_third(
    seeded_audio_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    """APIConnectionError 2x → sucesso na 3ª. 3 rows em api_calls."""
    queue = [make_connection_error(), make_connection_error(), _whisper_response_with_text()]
    _install_fake_client(monkeypatch, queue)

    txt_file_id = transcribe_handler(_make_task(seeded_audio_vault), seeded_audio_vault["conn"])
    assert txt_file_id is not None

    rows = seeded_audio_vault["conn"].execute(
        "SELECT error_type FROM api_calls ORDER BY id"
    ).fetchall()
    assert len(rows) == 3
    assert rows[0]["error_type"] == "connection"
    assert rows[1]["error_type"] == "connection"
    assert rows[2]["error_type"] is None  # sucesso limpa error_type


# ---------------------------------------------------------------------------
# 5. Retry em rate limit
# ---------------------------------------------------------------------------


def test_retry_on_rate_limit_succeeds_on_third(
    seeded_audio_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    queue = [make_rate_limit_error(), make_rate_limit_error(), _whisper_response_with_text()]
    _install_fake_client(monkeypatch, queue)

    transcribe_handler(_make_task(seeded_audio_vault), seeded_audio_vault["conn"])

    error_types = [
        r["error_type"]
        for r in seeded_audio_vault["conn"].execute(
            "SELECT error_type FROM api_calls ORDER BY id"
        ).fetchall()
    ]
    assert error_types == ["rate_limit", "rate_limit", None]


# ---------------------------------------------------------------------------
# 6. Auth error NÃO retry — propaga imediatamente
# ---------------------------------------------------------------------------


def test_auth_error_does_not_retry(
    seeded_audio_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    _install_fake_client(monkeypatch, [make_auth_error()])

    with pytest.raises(openai.AuthenticationError):
        transcribe_handler(_make_task(seeded_audio_vault), seeded_audio_vault["conn"])

    rows = seeded_audio_vault["conn"].execute(
        "SELECT error_type FROM api_calls"
    ).fetchall()
    assert len(rows) == 1  # não tentou de novo
    assert rows[0]["error_type"] == "auth_error"


# ---------------------------------------------------------------------------
# 7. BadRequest NÃO retry
# ---------------------------------------------------------------------------


def test_bad_request_does_not_retry(
    seeded_audio_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    _install_fake_client(monkeypatch, [make_bad_request_error()])

    with pytest.raises(openai.BadRequestError):
        transcribe_handler(_make_task(seeded_audio_vault), seeded_audio_vault["conn"])

    rows = seeded_audio_vault["conn"].execute(
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
        openai_api_key="",  # ausente
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
    seeded_audio_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2 invocações → 1 row em files/transcriptions/media_derivations."""
    resp = _whisper_response_with_text()
    _install_fake_client(monkeypatch, [resp, resp])

    id_1 = transcribe_handler(_make_task(seeded_audio_vault), seeded_audio_vault["conn"])
    id_2 = transcribe_handler(_make_task(seeded_audio_vault), seeded_audio_vault["conn"])
    assert id_1 == id_2

    conn = seeded_audio_vault["conn"]
    assert conn.execute(
        "SELECT COUNT(*) FROM files WHERE file_id = ?", (id_1,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM transcriptions WHERE file_id = ?", (id_1,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM media_derivations WHERE derived_file_id = ?", (id_1,),
    ).fetchone()[0] == 1
    # api_calls tem 2 rows (cada call é evento distinto, mesmo que idempotente em DB)
    assert conn.execute("SELECT COUNT(*) FROM api_calls").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# 10. api_calls registra latency_ms + model + error_type=NULL em sucesso
# ---------------------------------------------------------------------------


def test_api_call_logs_latency_ms_and_model(
    seeded_audio_vault: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_client(monkeypatch, [_whisper_response_with_text(duration=60.0)])

    transcribe_handler(_make_task(seeded_audio_vault), seeded_audio_vault["conn"])

    row = seeded_audio_vault["conn"].execute(
        "SELECT latency_ms, model, error_type, cost_usd, response_hash FROM api_calls"
    ).fetchone()
    assert row["model"] == MODEL
    assert row["error_type"] is None
    assert row["latency_ms"] is not None
    assert row["latency_ms"] >= 0
    # cost_usd = 60s / 60 * 0.006 = 0.006
    assert row["cost_usd"] == pytest.approx(COST_USD_PER_MINUTE, rel=0.01)
    assert row["response_hash"] is not None
