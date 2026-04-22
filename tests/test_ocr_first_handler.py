"""Testes do ocr_first_handler — Sprint 4 Op8 Fase 5.

Valida o dispatcher documento-vs-foto do pipeline OCR-first:

  - DOC + texto suficiente (>= OCR_TEXT_THRESHOLD) -> persiste em
    documents + classifications (source_type='document'), NAO enfileira
    VISUAL_ANALYSIS
  - DOC financeiro -> acima + INSERT em financial_records
  - FOTO (is_document=False) -> enfileira VISUAL_ANALYSIS, NAO cria
    document
  - TEXT_COUNT abaixo do threshold -> FOTO route
  - OCR malformed -> FOTO route (fallback seguro)
  - Exato no threshold -> DOC route (boundary)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from rdo_agent import financial_ocr, ocr_extractor
from rdo_agent.ocr_extractor import ocr_first_handler
from rdo_agent.orchestrator import (
    Task,
    TaskStatus,
    TaskType,
    init_db,
)
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_file

# ---------------------------------------------------------------------------
# FakeClient (shared shape for OCR + financial)
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeUsage:
    def __init__(self, pt=500, ct=100):
        self.prompt_tokens = pt
        self.completion_tokens = ct
        self.total_tokens = pt + ct


class _FakeCompletionForOCR:
    """Shape compativel com ocr_extractor — usa model_dump()."""
    def __init__(self, content, pt=500, ct=100):
        self._content = content
        self._usage = _FakeUsage(pt, ct)

    def model_dump(self):
        return {
            "choices": [{"message": {"content": self._content, "role": "assistant"}}],
            "usage": {
                "prompt_tokens": self._usage.prompt_tokens,
                "completion_tokens": self._usage.completion_tokens,
                "total_tokens": self._usage.total_tokens,
            },
        }


class _FakeCompletionForFinancial:
    """Shape compativel com financial_ocr — usa .choices[0].message.content."""
    def __init__(self, content, pt=400, ct=80):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage(pt, ct)


class _FakeCompletions:
    def __init__(self, queue):
        self._queue = list(queue)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeChat:
    def __init__(self, queue):
        self.completions = _FakeCompletions(queue)


class _FakeClient:
    def __init__(self, queue):
        self.chat = _FakeChat(queue)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vaults_root(tmp_path, monkeypatch) -> Path:
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
def seeded_vault(vaults_root):
    obra = "OBRA_OCR_H"
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
        "obra": obra, "vault": vault, "conn": conn,
        "image_file_id": image_file_id, "image_path": image_path,
    }


def _make_task(seeded: dict) -> Task:
    return Task(
        id=None, task_type=TaskType.OCR_FIRST,
        payload={
            "file_id": seeded["image_file_id"],
            "file_path": f"10_media/{seeded['image_path'].name}",
        },
        status=TaskStatus.PENDING, depends_on=[],
        obra=seeded["obra"], created_at="",
    )


def _install_ocr_fake(monkeypatch, payload: dict):
    fake = _FakeClient([_FakeCompletionForOCR(json.dumps(payload))])
    monkeypatch.setattr(ocr_extractor, "_get_openai_client", lambda: fake)
    return fake


def _install_financial_fake(monkeypatch, payload: dict):
    fake = _FakeClient([_FakeCompletionForFinancial(json.dumps(payload))])
    monkeypatch.setattr(financial_ocr, "_get_openai_client", lambda: fake)
    return fake


def _ocr_payload(
    text: str = "COMPROVANTE DE PIX\nValor: R$ 3.500,00\n" * 5,
    word_count: int = 50,
    is_document: bool = True,
    doc_type_hint: str | None = "comprovante_pix",
    confidence: float = 0.9,
) -> dict:
    return {
        "text": text, "word_count": word_count,
        "char_count": len(text), "is_document": is_document,
        "doc_type_hint": doc_type_hint, "confidence": confidence,
    }


def _financial_payload(valor_centavos=350000, doc_type="pix"):
    return {
        "doc_type": doc_type,
        "valor_centavos": valor_centavos,
        "moeda": "BRL",
        "data_transacao": "2026-04-06",
        "hora_transacao": "11:13:24",
        "pagador_nome": "Lucas Ferreira",
        "pagador_doc": "***.393.776-**",
        "recebedor_nome": "Everaldo Santos",
        "recebedor_doc": "***.456.789-**",
        "chave_pix": "everaldo@example.com",
        "descricao": "50% sinal serralheria",
        "instituicao_origem": "BB",
        "instituicao_destino": "Itau",
        "confidence": 0.9,
    }


# ---------------------------------------------------------------------------
# Route: DOCUMENT (non-financial)
# ---------------------------------------------------------------------------


def test_document_route_creates_document_and_classification(
    seeded_vault, monkeypatch,
):
    """word_count >= threshold + is_document=True + non-financial hint
    -> documents + classifications; NAO enfileira VISUAL_ANALYSIS."""
    _install_ocr_fake(monkeypatch, _ocr_payload(
        text="OFICIO N. 123/2026\n" + "texto longo " * 30,
        word_count=50,
        doc_type_hint="carta_oficial",
    ))

    result = ocr_first_handler(_make_task(seeded_vault), seeded_vault["conn"])
    assert result.startswith("routed:document")

    conn = seeded_vault["conn"]
    # documents row
    doc_count = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE obra=?",
        (seeded_vault["obra"],),
    ).fetchone()[0]
    assert doc_count == 1

    # classifications row with source_type='document'
    cls_count = conn.execute(
        "SELECT COUNT(*) FROM classifications WHERE obra=? "
        "AND source_type='document'",
        (seeded_vault["obra"],),
    ).fetchone()[0]
    assert cls_count == 1

    # NO VISUAL_ANALYSIS task enqueued
    va_count = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE task_type='visual_analysis'"
    ).fetchone()[0]
    assert va_count == 0


def test_financial_document_route_also_creates_financial_record(
    seeded_vault, monkeypatch,
):
    """OCR retorna comprovante_pix -> financial_ocr eh invocado +
    financial_records row criada."""
    # 1a chamada (OCR) + 2a chamada (financial structure)
    fake = _FakeClient([
        _FakeCompletionForOCR(json.dumps(_ocr_payload())),  # OCR
        _FakeCompletionForFinancial(json.dumps(_financial_payload())),  # fin
    ])
    monkeypatch.setattr(ocr_extractor, "_get_openai_client", lambda: fake)
    monkeypatch.setattr(financial_ocr, "_get_openai_client", lambda: fake)

    ocr_first_handler(_make_task(seeded_vault), seeded_vault["conn"])

    conn = seeded_vault["conn"]
    fin_row = conn.execute(
        "SELECT doc_type, valor_centavos, descricao FROM financial_records "
        "WHERE source_file_id=?", (seeded_vault["image_file_id"],),
    ).fetchone()
    assert fin_row is not None
    assert fin_row["doc_type"] == "pix"
    assert fin_row["valor_centavos"] == 350000
    assert "serralheria" in fin_row["descricao"]


# ---------------------------------------------------------------------------
# Route: PHOTO (is_document=False)
# ---------------------------------------------------------------------------


def test_photo_route_enqueues_visual_analysis(seeded_vault, monkeypatch):
    """is_document=False -> enfileira VISUAL_ANALYSIS, NAO cria document."""
    _install_ocr_fake(monkeypatch, _ocr_payload(
        text="", word_count=0, is_document=False,
        doc_type_hint=None, confidence=0.8,
    ))

    result = ocr_first_handler(_make_task(seeded_vault), seeded_vault["conn"])
    assert result.startswith("routed:visual_analysis")

    conn = seeded_vault["conn"]
    va_tasks = conn.execute(
        "SELECT task_type, payload FROM tasks WHERE task_type='visual_analysis'"
    ).fetchall()
    assert len(va_tasks) == 1

    docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert docs == 0


def test_photo_route_when_word_count_below_threshold(seeded_vault, monkeypatch):
    """is_document=True mas word_count=5 (<15 threshold) -> FOTO."""
    _install_ocr_fake(monkeypatch, _ocr_payload(
        text="curto demais aqui ok sim",
        word_count=5,
        is_document=True,
        doc_type_hint="outro",
    ))

    result = ocr_first_handler(_make_task(seeded_vault), seeded_vault["conn"])
    assert result.startswith("routed:visual_analysis")


def test_photo_route_when_ocr_is_malformed(seeded_vault, monkeypatch):
    """OCR retorna JSON invalido -> sentinel + FOTO (fallback seguro)."""
    fake = _FakeClient([_FakeCompletionForOCR("NAO eh { JSON valido )")])
    monkeypatch.setattr(ocr_extractor, "_get_openai_client", lambda: fake)

    result = ocr_first_handler(_make_task(seeded_vault), seeded_vault["conn"])
    assert result.startswith("routed:visual_analysis")
    assert "malformed=True" in result


# ---------------------------------------------------------------------------
# Boundary: exact threshold
# ---------------------------------------------------------------------------


def test_document_route_at_exact_threshold(seeded_vault, monkeypatch):
    """word_count = OCR_TEXT_THRESHOLD (15) -> DOC route."""
    _install_ocr_fake(monkeypatch, _ocr_payload(
        text="texto " * ocr_extractor.OCR_TEXT_THRESHOLD,
        word_count=ocr_extractor.OCR_TEXT_THRESHOLD,
        is_document=True,
        doc_type_hint="protocolo",
    ))

    result = ocr_first_handler(_make_task(seeded_vault), seeded_vault["conn"])
    assert result.startswith("routed:document")


# ---------------------------------------------------------------------------
# Idempotencia (UNIQUE em classifications)
# ---------------------------------------------------------------------------


def test_document_route_is_idempotent_on_second_call(seeded_vault, monkeypatch):
    """Re-rodar handler sobre mesma imagem nao cria classifications
    duplicada (UNIQUE obra+source_file_id preveine)."""
    # 2 calls ao OCR (mesma resposta)
    fake = _FakeClient([
        _FakeCompletionForOCR(json.dumps(_ocr_payload(
            text="oficio longo " * 10, word_count=20,
            doc_type_hint="carta_oficial",
        ))),
        _FakeCompletionForOCR(json.dumps(_ocr_payload(
            text="oficio longo " * 10, word_count=20,
            doc_type_hint="carta_oficial",
        ))),
    ])
    monkeypatch.setattr(ocr_extractor, "_get_openai_client", lambda: fake)

    ocr_first_handler(_make_task(seeded_vault), seeded_vault["conn"])
    ocr_first_handler(_make_task(seeded_vault), seeded_vault["conn"])

    conn = seeded_vault["conn"]
    cls_count = conn.execute(
        "SELECT COUNT(*) FROM classifications WHERE source_type='document'"
    ).fetchone()[0]
    assert cls_count == 1  # UNIQUE preveniu duplicata


# ---------------------------------------------------------------------------
# Confidence baixa marca quality_flag='suspeita' + human_review_needed=1
# ---------------------------------------------------------------------------


def test_low_confidence_ocr_marks_review_needed(seeded_vault, monkeypatch):
    """OCR confidence < 0.3 -> pending_review + human_review_needed=1."""
    _install_ocr_fake(monkeypatch, _ocr_payload(
        text="texto ruim com muitos ? ? ?" + " palavra" * 15,
        word_count=20, confidence=0.2,
        doc_type_hint="outro",
    ))

    ocr_first_handler(_make_task(seeded_vault), seeded_vault["conn"])

    conn = seeded_vault["conn"]
    cls_row = conn.execute(
        "SELECT quality_flag, human_review_needed, semantic_status "
        "FROM classifications WHERE source_type='document'"
    ).fetchone()
    assert cls_row["quality_flag"] == "suspeita"
    assert cls_row["human_review_needed"] == 1
    assert cls_row["semantic_status"] == "pending_review"


def test_source_image_status_updated_on_doc_route(seeded_vault, monkeypatch):
    """Imagem-fonte recebe semantic_status='ocr_extracted' apos DOC route."""
    _install_ocr_fake(monkeypatch, _ocr_payload(
        text="documento longo " * 10, word_count=20,
        doc_type_hint="outro",
    ))

    ocr_first_handler(_make_task(seeded_vault), seeded_vault["conn"])
    status = seeded_vault["conn"].execute(
        "SELECT semantic_status FROM files WHERE file_id=?",
        (seeded_vault["image_file_id"],),
    ).fetchone()[0]
    assert status == "ocr_extracted"
