"""Testes do OCRRouter — Sessao 9, divida #49."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from rdo_agent.ocr_router import (
    OCR_TARGETS,
    OCRRouter,
    OCRTarget,
    migrate_ocr_cache,
)

HAS_TESSERACT = shutil.which("tesseract") is not None
SKIP_NO_TESSERACT = pytest.mark.skipif(
    not HAS_TESSERACT, reason="tesseract não instalado",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = sqlite3.connect(tmp_path / "router.db")
    c.row_factory = sqlite3.Row
    migrate_ocr_cache(c)
    return c


def _make_text_image(path: Path, *, text: str = "Hello World 12345 Comprovante") -> Path:
    """Cria PNG com texto via PIL (pra Tesseract detectar)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (600, 200), (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    d.text((20, 80), text, fill=(0, 0, 0), font=font)
    img.save(path, format="PNG")
    return path


def _make_blank_image(path: Path) -> Path:
    """Cria PNG branca (sem texto)."""
    from PIL import Image
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    img.save(path, format="PNG")
    return path


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migrate_ocr_cache_creates_table(conn):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ocr_cache'",
    ).fetchone()
    assert row is not None


def test_migrate_ocr_cache_idempotent(conn):
    migrate_ocr_cache(conn)
    migrate_ocr_cache(conn)
    migrate_ocr_cache(conn)


# ---------------------------------------------------------------------------
# Routing por extensão / hint (não exige Tesseract)
# ---------------------------------------------------------------------------


def test_route_pdf_extension_goes_to_document(conn, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    router = OCRRouter(conn)
    target = router.route(pdf, check_text_presence=False)
    assert target is OCRTarget.DOCUMENT


def test_route_file_type_document_goes_to_document(conn, tmp_path):
    img = tmp_path / "anything.jpg"
    img.write_bytes(b"\xff\xd8")  # invalid mas extensao img
    router = OCRRouter(conn)
    target = router.route(img, file_type="document", check_text_presence=False)
    assert target is OCRTarget.DOCUMENT


def test_route_hint_financial_takes_priority(conn, tmp_path):
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8")
    router = OCRRouter(conn)
    target = router.route(
        img, hint="financial", check_text_presence=False,
    )
    assert target is OCRTarget.FINANCIAL


def test_route_hint_document_routes_document(conn, tmp_path):
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8")
    router = OCRRouter(conn)
    target = router.route(
        img, hint="document", check_text_presence=False,
    )
    assert target is OCRTarget.DOCUMENT


def test_route_default_no_hint_no_tesseract_returns_generic(
    conn, tmp_path, monkeypatch,
):
    """Sem hint, sem Tesseract: fail-open → GENERIC (caller decide)."""
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8")

    # Forca has_tesseract=False
    monkeypatch.setattr(shutil, "which", lambda _: None)
    router = OCRRouter(conn)
    target = router.route(img, check_text_presence=True)
    assert target is OCRTarget.GENERIC


# ---------------------------------------------------------------------------
# detect_text_presence (com Tesseract real)
# ---------------------------------------------------------------------------


@SKIP_NO_TESSERACT
def test_detect_text_presence_blank_image_returns_false_or_none(
    conn, tmp_path,
):
    """Imagem branca: Tesseract acha pouco/nada. Retorna False ou
    None (fail-open) dependendo do idioma instalado."""
    blank = _make_blank_image(tmp_path / "blank.png")
    router = OCRRouter(conn, text_threshold_chars=20)
    result = router.detect_text_presence(blank)
    # Aceita False (sem texto) ou None (fail-open por lang ausente)
    assert result in (False, None)


@SKIP_NO_TESSERACT
def test_detect_text_presence_with_text_image(conn, tmp_path):
    """Imagem com texto óbvio → True (se idioma disponivel) ou
    None (se sem por/eng instalados)."""
    img = _make_text_image(
        tmp_path / "text.png",
        text="Comprovante Pix 1234 Hello World",
    )
    router = OCRRouter(conn)
    result = router.detect_text_presence(img)
    assert result in (True, None)


def test_detect_text_presence_fail_open_when_no_tesseract(
    conn, tmp_path, monkeypatch,
):
    img = tmp_path / "x.png"
    img.write_bytes(b"fake")
    monkeypatch.setattr(shutil, "which", lambda _: None)
    router = OCRRouter(conn)
    assert router.detect_text_presence(img) is None


# ---------------------------------------------------------------------------
# Tesseract sem idioma 'por' → fallback para 'eng'
# ---------------------------------------------------------------------------


@SKIP_NO_TESSERACT
def test_resolve_lang_falls_back_to_eng_when_por_missing(
    conn, monkeypatch,
):
    """Se 'por' não está instalado mas 'eng' está, retorna 'eng'."""
    router = OCRRouter(conn, tesseract_lang="por")

    # Forca _supported_langs sem 'por'
    router._supported_langs = {"eng", "osd"}
    lang = router._resolve_lang()
    assert lang == "eng"


def test_resolve_lang_returns_none_when_no_langs(conn, monkeypatch):
    """Tesseract presente mas sem langs → None."""
    router = OCRRouter(conn)
    if not router.has_tesseract:
        pytest.skip("Tesseract não instalado para esse setup")
    router._supported_langs = set()
    assert router._resolve_lang() is None


def test_resolve_lang_warns_only_once(conn, monkeypatch, caplog):
    """Warnings de 'no_lang' são emitidas só 1x (não spam)."""
    import logging
    router = OCRRouter(conn, tesseract_lang="por")
    router._supported_langs = {"eng"}

    with caplog.at_level(logging.WARNING, logger="rdo_agent.ocr_router.router"):
        router._resolve_lang()
        router._resolve_lang()
        router._resolve_lang()
    warnings = [r for r in caplog.records if "tesseract sem idioma" in r.message]
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_miss_returns_none(conn):
    router = OCRRouter(conn)
    assert router.get_cached("f_inexistente") is None


def test_cache_put_then_get_returns_dict(conn):
    router = OCRRouter(conn)
    router.put_cache(
        "f_001",
        extractor_used="financial",
        extracted_text="PIX 3500.00 - Lucas",
        confidence=0.9,
    )
    result = router.get_cached("f_001")
    assert result is not None
    assert result["file_id"] == "f_001"
    assert result["extractor_used"] == "financial"
    assert result["extracted_text"] == "PIX 3500.00 - Lucas"
    assert result["confidence"] == 0.9
    assert result["extracted_at"] is not None
    assert result["error_message"] is None


def test_cache_put_replaces_existing(conn):
    router = OCRRouter(conn)
    router.put_cache("f_x", extractor_used="generic", extracted_text="v1")
    router.put_cache("f_x", extractor_used="generic", extracted_text="v2")
    cached = router.get_cached("f_x")
    assert cached["extracted_text"] == "v2"


def test_cache_can_store_error(conn):
    router = OCRRouter(conn)
    router.put_cache(
        "f_err",
        extractor_used="financial",
        error_message="OCR failed: timeout",
    )
    cached = router.get_cached("f_err")
    assert cached["error_message"] == "OCR failed: timeout"
    assert cached["extracted_text"] is None


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


def test_ocr_targets_enum_complete():
    assert set(OCR_TARGETS) == {"financial", "document", "generic", "skip"}
    for t in OCRTarget:
        assert t.value in OCR_TARGETS
