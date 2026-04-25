"""
OCRRouter — núcleo da dívida #49.

API:

- ``OCRRouter(conn)`` — instancia o roteador, garante migration de
  ``ocr_cache``.
- ``router.detect_text_presence(image_path)`` → ``True`` /
  ``False`` / ``None`` (None = fail-open semântico).
- ``router.route(file_path, *, file_type=None,
  hint=None, prior_decision=None)`` → ``OCRTarget``.
- ``router.get_cached(file_id)`` / ``router.put_cache(file_id, ...)``.

Fail-open: quando Tesseract está ausente ou idioma `por` não
instalado, ``detect_text_presence`` retorna ``None``. Caller pode
interpretar ``None`` como "tem texto" (não pula extractor) — preserva
mídia legítima.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Enum de destinos
# ---------------------------------------------------------------------------


class OCRTarget(str, Enum):
    """Destino de roteamento OCR."""

    FINANCIAL = "financial"   # comprovantes PIX/NF/boleto
    DOCUMENT = "document"     # PDF, contrato, plantas
    GENERIC = "generic"       # texto livre em imagem
    SKIP = "skip"             # sem texto detectado


OCR_TARGETS: tuple[str, ...] = tuple(t.value for t in OCRTarget)


@dataclass(frozen=True)
class _RouteResult:
    """Resultado interno (não exposto)."""

    target: OCRTarget
    reason: str


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate_ocr_cache(conn: sqlite3.Connection) -> None:
    """Cria tabela ``ocr_cache`` (idempotente)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ocr_cache (
            file_id          TEXT PRIMARY KEY,
            extracted_text   TEXT,
            extractor_used   TEXT NOT NULL,
            confidence       REAL,
            extracted_at     TEXT NOT NULL,
            error_message    TEXT
        )
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# OCRRouter
# ---------------------------------------------------------------------------


PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


class OCRRouter:
    """
    Coordena qual extractor OCR usar para um dado arquivo.

    Args:
        conn: SQLite com tabela ``ocr_cache`` (será migrada
            idempotentemente).
        tesseract_lang: idioma para Tesseract. Default ``por``;
            faz fallback para ``eng`` se ``por`` não instalado.
        text_threshold_chars: mínimo de chars detectados pelo
            Tesseract para considerar "tem texto" (default 20 —
            captura comprovantes mas dispensa imagens só com
            ruído).
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        tesseract_lang: str = "por",
        text_threshold_chars: int = 20,
    ):
        self.conn = conn
        self.tesseract_lang = tesseract_lang
        self.text_threshold_chars = text_threshold_chars
        self.has_tesseract = bool(shutil.which("tesseract"))
        self._supported_langs: set[str] | None = None
        self._warned_no_tesseract = False
        self._warned_no_lang = False
        migrate_ocr_cache(conn)

    # ---- Tesseract ----

    def _supports_lang(self, lang: str) -> bool:
        """Cacheia lookup de idiomas instalados."""
        if not self.has_tesseract:
            return False
        if self._supported_langs is None:
            try:
                out = subprocess.run(
                    ["tesseract", "--list-langs"],
                    capture_output=True, text=True, timeout=5,
                )
                self._supported_langs = {
                    line.strip() for line in out.stdout.splitlines()
                    if line.strip() and not line.startswith("List")
                }
            except Exception as e:
                log.warning("tesseract --list-langs falhou: %s", e)
                self._supported_langs = set()
        return lang in self._supported_langs

    def _resolve_lang(self) -> str | None:
        """
        Retorna idioma a usar: ``por`` se instalado, ``eng`` como
        fallback. ``None`` se nenhum disponível (Tesseract sem langs).
        """
        if not self.has_tesseract:
            if not self._warned_no_tesseract:
                log.warning(
                    "tesseract nao instalado — OCR router em modo "
                    "fail-open (detect_text_presence retorna None)",
                )
                self._warned_no_tesseract = True
            return None
        if self._supports_lang(self.tesseract_lang):
            return self.tesseract_lang
        if self._supports_lang("eng"):
            if not self._warned_no_lang:
                log.warning(
                    "tesseract sem idioma %r; usando 'eng' (suboptimal "
                    "para PT-BR). Instale 'tesseract-ocr-por' para "
                    "melhor deteccao.",
                    self.tesseract_lang,
                )
                self._warned_no_lang = True
            return "eng"
        return None

    def detect_text_presence(self, image_path: Path) -> bool | None:
        """
        Retorna ``True`` se Tesseract acha >= threshold_chars de texto;
        ``False`` se acha menos; ``None`` se Tesseract ou idioma
        ausente (fail-open semântico — caller decide).
        """
        lang = self._resolve_lang()
        if lang is None:
            return None
        try:
            out = subprocess.run(
                ["tesseract", str(image_path), "-",
                 "-l", lang, "--psm", "3"],
                capture_output=True, text=True, timeout=15,
            )
            if out.returncode != 0:
                log.warning(
                    "tesseract retornou %d em %s",
                    out.returncode, image_path,
                )
                return None
            text = (out.stdout or "").strip()
            return len(text) >= self.text_threshold_chars
        except subprocess.TimeoutExpired:
            log.warning("tesseract timeout em %s", image_path)
            return None
        except Exception as e:
            log.warning("tesseract falha em %s: %s", image_path, e)
            return None

    # ---- Routing ----

    def route(
        self,
        file_path: Path,
        *,
        file_type: str | None = None,
        hint: str | None = None,
        check_text_presence: bool = True,
    ) -> OCRTarget:
        """
        Decide ``OCRTarget`` para ``file_path``.

        Args:
            file_path: caminho local do arquivo (precisa existir
                para Tesseract; metadados de extensão usados como
                hint cedo).
            file_type: tipo declarado em ``files.file_type`` (ex:
                'image', 'video', 'document'). Se 'document' ou
                extensão PDF, roteia direto para DOCUMENT.
            hint: dica externa, ex: ``RoutingDecision.target`` da
                Camada 3 do vision cascade ('financial', 'document',
                'vision'). 'vision' é ignorado (router não tem essa
                opção; significa "default" aqui).
            check_text_presence: se ``False``, pula chamada Tesseract
                e usa apenas hints/extensão (acelera; útil em batch).

        Returns:
            ``OCRTarget``. ``SKIP`` apenas quando Tesseract retorna
            ``False`` (sem texto detectado e Tesseract disponível).
        """
        ext = file_path.suffix.lower()

        # 1) PDF → document direto
        if ext in PDF_EXTS or file_type == "document":
            return OCRTarget.DOCUMENT

        # 2) Hint externo decisivo
        if hint == "financial":
            return OCRTarget.FINANCIAL
        if hint == "document":
            return OCRTarget.DOCUMENT

        # 3) Tesseract: tem texto?
        if check_text_presence:
            tp = self.detect_text_presence(file_path)
            if tp is False:
                return OCRTarget.SKIP
            # tp is None → fail-open: continua

        # 4) Default
        return OCRTarget.GENERIC

    # ---- Cache ----

    def get_cached(self, file_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT extracted_text, extractor_used, confidence, "
            "extracted_at, error_message FROM ocr_cache "
            "WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "file_id": file_id,
            "extracted_text": row[0],
            "extractor_used": row[1],
            "confidence": row[2],
            "extracted_at": row[3],
            "error_message": row[4],
        }

    def put_cache(
        self, file_id: str, *,
        extractor_used: str,
        extracted_text: str | None = None,
        confidence: float | None = None,
        error_message: str | None = None,
    ) -> None:
        """INSERT OR REPLACE em ocr_cache."""
        self.conn.execute(
            "INSERT OR REPLACE INTO ocr_cache "
            "(file_id, extracted_text, extractor_used, confidence, "
            " extracted_at, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (file_id, extracted_text, extractor_used, confidence,
             _now_iso(), error_message),
        )
        self.conn.commit()


__all__ = [
    "OCR_TARGETS",
    "OCRRouter",
    "OCRTarget",
    "migrate_ocr_cache",
]
