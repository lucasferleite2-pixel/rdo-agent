"""Testes do ocr_extractor — Sprint 4 Op8 Fase 3.

Cobre `extract_text_from_image` (nivel publico baixo). O handler
`ocr_first_handler` tem sua propria suite (test_ocr_first_handler.py).

Espelha estrutura de test_visual_analyzer.py: FakeClient + monkeypatch,
sem chamadas reais a OpenAI.

Casos:
  1. Sucesso com JSON valido (is_document=True, doc financeiro)
  2. Sucesso com is_document=False (foto de cena)
  3. Malformed JSON -> sentinel + is_malformed=True
  4. Schema invalido (falta campo) -> sentinel
  5. Confidence fora do range -> sentinel
  6. Retry 2x connection error + sucesso na 3a
  7. Auth error propaga imediatamente (sem retry)
  8. OCRResult.has_sufficient_text respeita OCR_TEXT_THRESHOLD
  9. OCRResult.is_financial_document detecta doc_type_hint financeiro
 10. API key ausente -> RuntimeError
 11. api_calls row populada com endpoint=chat.completions.create e model
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import openai
import pytest
from PIL import Image

from rdo_agent import ocr_extractor
from rdo_agent.ocr_extractor import (
    MODEL,
    OCR_TEXT_THRESHOLD,
    OCRResult,
    _get_openai_client,
    extract_text_from_image,
)
from rdo_agent.orchestrator import init_db
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_file

# ---------------------------------------------------------------------------
# openai exception factories
# ---------------------------------------------------------------------------


def _req() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _resp(code: int) -> httpx.Response:
    return httpx.Response(status_code=code, request=_req())


def make_connection_error():
    return openai.APIConnectionError(request=_req())


def make_auth_error():
    return openai.AuthenticationError("invalid key", response=_resp(401), body=None)


# ---------------------------------------------------------------------------
# FakeClient
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeUsage:
    def __init__(self, pt: int = 800, ct: int = 120):
        self.prompt_tokens = pt
        self.completion_tokens = ct
        self.total_tokens = pt + ct


class _FakeChatCompletion:
    def __init__(self, content: str, pt: int = 800, ct: int = 120):
        self._content = content
        self._usage = _FakeUsage(pt, ct)

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
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, queue: list):
        self.chat = _FakeChat(_FakeCompletions(queue))


# ---------------------------------------------------------------------------
# Fixtures
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
    monkeypatch.setattr(ocr_extractor, "RETRY_DELAYS_SEC", (0.0, 0.0))


@pytest.fixture
def seeded_image_vault(vaults_root: Path) -> dict:
    obra = "OBRA_OCR"
    vault = vaults_root / obra
    media_dir = vault / "10_media"
    media_dir.mkdir(parents=True)

    image_path = media_dir / "comprov.jpg"
    Image.new("RGB", (64, 64), (200, 200, 200)).save(image_path, "JPEG")

    conn = init_db(vault)
    image_sha = sha256_file(image_path)
    image_file_id = f"f_{image_sha[:12]}"
    conn.execute(
        """INSERT INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            timestamp_resolved, timestamp_source,
            semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (image_file_id, obra, "10_media/comprov.jpg", "image",
         image_sha, image_path.stat().st_size,
         "2026-04-06T11:13:24+00:00", "filename",
         "awaiting_classification", "2026-04-22T00:00:00Z"),
    )
    conn.commit()
    return {
        "obra": obra,
        "vault": vault,
        "conn": conn,
        "image_file_id": image_file_id,
        "image_path": image_path,
    }


def _install_fake(monkeypatch: pytest.MonkeyPatch, queue: list) -> _FakeClient:
    c = _FakeClient(queue)
    monkeypatch.setattr(ocr_extractor, "_get_openai_client", lambda: c)
    return c


def _ocr_valid_payload(
    text: str = "COMPROVANTE DE PIX\nValor: R$ 3.500,00\nPagador: Lucas\n"
               "Recebedor: Everaldo\nData: 06/04/2026\nHora: 11:13:24",
    word_count: int = 20,
    is_document: bool = True,
    doc_type_hint: str | None = "comprovante_pix",
    confidence: float = 0.92,
) -> dict:
    return {
        "text": text,
        "word_count": word_count,
        "char_count": len(text),
        "is_document": is_document,
        "doc_type_hint": doc_type_hint,
        "confidence": confidence,
    }


def _ocr_valid_response(
    payload: dict | None = None, pt: int = 800, ct: int = 120,
) -> _FakeChatCompletion:
    content = json.dumps(payload if payload is not None else _ocr_valid_payload())
    return _FakeChatCompletion(content, pt, ct)


# ---------------------------------------------------------------------------
# 1. Sucesso com JSON valido (comprovante financeiro)
# ---------------------------------------------------------------------------


def test_ocr_success_financial_document(seeded_image_vault, monkeypatch):
    _install_fake(monkeypatch, [_ocr_valid_response()])

    result, api_call_id = extract_text_from_image(
        seeded_image_vault["image_path"],
        seeded_image_vault["obra"],
        seeded_image_vault["conn"],
    )
    assert isinstance(result, OCRResult)
    assert result.is_document is True
    assert result.word_count == 20
    assert result.doc_type_hint == "comprovante_pix"
    assert result.confidence == pytest.approx(0.92)
    assert result.is_malformed is False
    assert result.has_sufficient_text is True
    assert result.is_financial_document is True
    assert result.cost_usd > 0

    row = seeded_image_vault["conn"].execute(
        "SELECT endpoint, model, error_type FROM api_calls WHERE id=?",
        (api_call_id,),
    ).fetchone()
    assert row["endpoint"] == "chat.completions.create"
    assert row["model"] == MODEL
    assert row["error_type"] is None


# ---------------------------------------------------------------------------
# 2. Sucesso com is_document=False (foto de cena real)
# ---------------------------------------------------------------------------


def test_ocr_photo_of_scene_not_document(seeded_image_vault, monkeypatch):
    payload = _ocr_valid_payload(
        text="", word_count=0, is_document=False, doc_type_hint=None, confidence=0.95,
    )
    _install_fake(monkeypatch, [_ocr_valid_response(payload)])

    result, _ = extract_text_from_image(
        seeded_image_vault["image_path"],
        seeded_image_vault["obra"],
        seeded_image_vault["conn"],
    )
    assert result.is_document is False
    assert result.has_sufficient_text is False
    assert result.is_financial_document is False
    assert result.is_malformed is False


# ---------------------------------------------------------------------------
# 3. Malformed JSON -> sentinel + is_malformed=True
# ---------------------------------------------------------------------------


def test_ocr_malformed_json_returns_sentinel(seeded_image_vault, monkeypatch, caplog):
    import logging
    bad = _FakeChatCompletion("isto { NAO ) eh JSON valido", 800, 50)
    _install_fake(monkeypatch, [bad])

    with caplog.at_level(logging.WARNING, logger="rdo_agent.ocr_extractor"):
        result, api_call_id = extract_text_from_image(
            seeded_image_vault["image_path"],
            seeded_image_vault["obra"],
            seeded_image_vault["conn"],
        )
    assert result.is_malformed is True
    assert "json_decode_error" in (result.malformed_reason or "")
    assert result.is_document is False
    assert result.word_count == 0
    assert any("JSON invalido" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 4. Schema invalido (campo faltando) -> sentinel
# ---------------------------------------------------------------------------


def test_ocr_schema_missing_field_returns_sentinel(seeded_image_vault, monkeypatch):
    incomplete = {
        "text": "some text",
        "word_count": 5,
        "char_count": 9,
        # is_document ausente
        "doc_type_hint": None,
        "confidence": 0.5,
    }
    _install_fake(monkeypatch, [_ocr_valid_response(incomplete)])

    result, _ = extract_text_from_image(
        seeded_image_vault["image_path"],
        seeded_image_vault["obra"],
        seeded_image_vault["conn"],
    )
    assert result.is_malformed is True
    assert "missing_fields:is_document" in (result.malformed_reason or "")


# ---------------------------------------------------------------------------
# 5. Confidence fora do range -> sentinel
# ---------------------------------------------------------------------------


def test_ocr_confidence_out_of_range(seeded_image_vault, monkeypatch):
    payload = _ocr_valid_payload(confidence=1.8)
    _install_fake(monkeypatch, [_ocr_valid_response(payload)])

    result, _ = extract_text_from_image(
        seeded_image_vault["image_path"],
        seeded_image_vault["obra"],
        seeded_image_vault["conn"],
    )
    assert result.is_malformed is True
    assert "confidence_out_of_range" in (result.malformed_reason or "")


# ---------------------------------------------------------------------------
# 6. Retry 2x connection error + sucesso na 3a
# ---------------------------------------------------------------------------


def test_ocr_retry_connection_error_recovers(
    seeded_image_vault, monkeypatch, no_sleep,
):
    _install_fake(
        monkeypatch,
        [make_connection_error(), make_connection_error(), _ocr_valid_response()],
    )
    result, _ = extract_text_from_image(
        seeded_image_vault["image_path"],
        seeded_image_vault["obra"],
        seeded_image_vault["conn"],
    )
    assert result.is_malformed is False

    rows = seeded_image_vault["conn"].execute(
        "SELECT error_type FROM api_calls ORDER BY id"
    ).fetchall()
    assert len(rows) == 3
    assert rows[0]["error_type"] == "connection"
    assert rows[1]["error_type"] == "connection"
    assert rows[2]["error_type"] is None


# ---------------------------------------------------------------------------
# 7. Auth error propaga imediatamente (sem retry)
# ---------------------------------------------------------------------------


def test_ocr_auth_error_does_not_retry(seeded_image_vault, monkeypatch, no_sleep):
    _install_fake(monkeypatch, [make_auth_error()])
    with pytest.raises(openai.AuthenticationError):
        extract_text_from_image(
            seeded_image_vault["image_path"],
            seeded_image_vault["obra"],
            seeded_image_vault["conn"],
        )
    rows = seeded_image_vault["conn"].execute(
        "SELECT error_type FROM api_calls"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["error_type"] == "auth_error"


# ---------------------------------------------------------------------------
# 8. OCRResult.has_sufficient_text respeita OCR_TEXT_THRESHOLD
# ---------------------------------------------------------------------------


def test_has_sufficient_text_boundary():
    """word_count exatamente no threshold -> has_sufficient_text=True."""
    r_below = OCRResult(
        text="", word_count=OCR_TEXT_THRESHOLD - 1, char_count=0,
        is_document=True, doc_type_hint=None, confidence=0.5,
        cost_usd=0.0,
    )
    r_at = OCRResult(
        text="", word_count=OCR_TEXT_THRESHOLD, char_count=0,
        is_document=True, doc_type_hint=None, confidence=0.5,
        cost_usd=0.0,
    )
    r_above = OCRResult(
        text="", word_count=OCR_TEXT_THRESHOLD + 10, char_count=0,
        is_document=True, doc_type_hint=None, confidence=0.5,
        cost_usd=0.0,
    )
    assert r_below.has_sufficient_text is False
    assert r_at.has_sufficient_text is True
    assert r_above.has_sufficient_text is True


# ---------------------------------------------------------------------------
# 9. is_financial_document detecta doc_type_hint financeiro
# ---------------------------------------------------------------------------


def test_is_financial_document_detection():
    base = dict(text="", word_count=20, char_count=0, is_document=True,
                confidence=0.9, cost_usd=0.0)
    assert OCRResult(**base, doc_type_hint="comprovante_pix").is_financial_document
    assert OCRResult(**base, doc_type_hint="boleto").is_financial_document
    assert OCRResult(**base, doc_type_hint="nota_fiscal").is_financial_document
    assert not OCRResult(**base, doc_type_hint="carta_oficial").is_financial_document
    assert not OCRResult(**base, doc_type_hint="protocolo").is_financial_document
    assert not OCRResult(**base, doc_type_hint=None).is_financial_document
    # is_document=False sempre -> False
    base2 = dict(base)
    base2["is_document"] = False
    assert not OCRResult(**base2, doc_type_hint="comprovante_pix").is_financial_document


# ---------------------------------------------------------------------------
# 10. API key ausente -> RuntimeError
# ---------------------------------------------------------------------------


def test_api_key_missing_raises(tmp_path, monkeypatch):
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
# Sprint 4 Op11 Divida #9 — timeout/retry nativos
# ---------------------------------------------------------------------------


def test_get_openai_client_has_timeout_configured(vaults_root):
    """Cliente retornado tem timeout=30s e max_retries=3 (nativos do SDK)."""
    from rdo_agent.ocr_extractor import (
        OPENAI_CLIENT_MAX_RETRIES,
        OPENAI_CLIENT_TIMEOUT_SEC,
    )
    # Constantes documentadas
    assert OPENAI_CLIENT_TIMEOUT_SEC == 30.0
    assert OPENAI_CLIENT_MAX_RETRIES == 3

    client = _get_openai_client()
    # SDK OpenAI expoe timeout como NotGiven / float / httpx.Timeout
    # dependendo da versao. Se float, comparamos direto. Se objeto httpx,
    # usa read attr. Cobre ambos.
    t = client.timeout
    try:
        t_val = float(t)
    except (TypeError, ValueError):
        t_val = getattr(t, "read", None) or getattr(t, "connect", None)
    assert t_val == 30.0, f"expected 30.0, got {t!r}"
    assert client.max_retries == 3
