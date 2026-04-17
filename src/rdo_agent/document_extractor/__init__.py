"""
Extrator de Texto de Documentos — Camada 1 (Sprint 2 §Fase 1).

Espelha o padrão do extractor de áudio: dois níveis públicos, persistência
de derivação em files + media_derivations, mais uma linha em documents
com o texto canônico.

Diferenças em relação ao extract_audio_handler:
  - NÃO enfileira task downstream. Sprint 3 (classificador) decide o que
    fazer com o texto extraído ao varrer files com semantic_status =
    "awaiting_classification".
  - Atualiza o semantic_status do PDF-fonte de "awaiting_document_processing"
    para "extracted" (fim de pipeline para o lado do PDF; o .txt continua).
  - Fora do escopo Sprint 2: docx/xlsx/odt — handler levanta
    UnsupportedDocumentFormatError, run_worker marca a task FAILED com
    mensagem clara apontando para SPRINT2_BACKLOG.md.

Fonte canônica do texto: tabela documents (pesquisável por SQL).
Cópia em arquivo .txt (em 20_transcriptions/) é redundância proposital
para auditoria humana sem precisar de SQLite.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from rdo_agent.orchestrator import Task
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_file
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

EXTRACTION_METHOD = "pdfplumber>=0.11 (page_text concat)"


class UnsupportedDocumentFormatError(RuntimeError):
    """Formato de documento sem handler em Sprint 2 (apenas PDF é suportado)."""


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def extract_text_from_document(
    doc_path: Path,
    output_path: Path,
) -> tuple[Path, str, int]:
    """
    Extrai texto de um documento via pdfplumber e grava em output_path.

    Args:
        doc_path: documento de origem. Apenas .pdf é suportado em Sprint 2.
        output_path: caminho final do .txt. Pais são criados se necessário.

    Returns:
        (output_path, extraction_method, page_count) — extraction_method é
        registrado em files.derivation_method e documents.extraction_method.

    Raises:
        FileNotFoundError: doc_path não existe.
        UnsupportedDocumentFormatError: extensão não-PDF (.docx, .xlsx, etc.).
        RuntimeError: pdfplumber falha ao parsear (PDF corrompido).
    """
    if not doc_path.exists():
        raise FileNotFoundError(f"documento não encontrado: {doc_path}")

    ext = doc_path.suffix.lower()
    if ext != ".pdf":
        raise UnsupportedDocumentFormatError(
            f"formato {ext} não suportado; ver SPRINT2_BACKLOG.md"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    import pdfplumber

    pages_text: list[str] = []
    try:
        with pdfplumber.open(doc_path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                pages_text.append(page.extract_text() or "")
    except Exception as e:  # pdfplumber pode levantar várias exceções diferentes
        raise RuntimeError(f"pdfplumber falhou em {doc_path.name}: {e}") from e

    full_text = "\n\n".join(pages_text).strip()
    output_path.write_text(full_text, encoding="utf-8")

    if not full_text:
        log.warning(
            "PDF escaneado/sem texto: %s (%d páginas, 0 chars extraídos)",
            doc_path.name,
            page_count,
        )

    return output_path, EXTRACTION_METHOD, page_count


def extract_document_handler(task: Task, conn: sqlite3.Connection) -> str | None:
    """
    Handler para tasks EXTRACT_DOCUMENT consumidas por run_worker.

    Pipeline:
        1. Resolve vault_path via config.get().vault_path(obra).
        2. Lê o registro do PDF-fonte em files (herda referenced_by_message,
           timestamp_resolved, timestamp_source).
        3. Chama extract_text_from_document. Falhas propagam:
           UnsupportedDocumentFormatError, FileNotFoundError ou RuntimeError
           — run_worker marca a task como FAILED com traceback.
        4. Hash do .txt → file_id determinístico → INSERT OR IGNORE em files
           com derived_from=PDF, semantic_status='awaiting_classification'.
        5. INSERT OR IGNORE em documents (UNIQUE em file_id).
        6. INSERT OR IGNORE em media_derivations.
        7. UPDATE files do PDF-fonte: semantic_status='extracted'.

    NÃO enfileira task downstream — Sprint 3 (classificador) decide.

    Returns:
        file_id do .txt criado (vai para tasks.result_ref).
    """
    payload = task.payload
    pdf_file_id = payload["file_id"]
    pdf_rel_path = payload["file_path"]

    obra = task.obra
    vault_path = config.get().vault_path(obra)
    pdf_path = vault_path / pdf_rel_path

    src_row = conn.execute(
        "SELECT file_id, referenced_by_message, timestamp_resolved, timestamp_source "
        "FROM files WHERE file_id = ?",
        (pdf_file_id,),
    ).fetchone()
    if src_row is None:
        raise RuntimeError(f"documento {pdf_file_id} não encontrado em files (obra={obra})")

    txt_filename = f"{pdf_path.name}.text.txt"
    txt_path = vault_path / "20_transcriptions" / txt_filename

    _, method, page_count = extract_text_from_document(pdf_path, txt_path)

    text = txt_path.read_text(encoding="utf-8")
    txt_sha = sha256_file(txt_path)
    txt_file_id = f"f_{txt_sha[:12]}"
    txt_rel_path = f"20_transcriptions/{txt_filename}"
    now = _now_iso_utc()

    conn.execute(
        """
        INSERT OR IGNORE INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            derived_from, derivation_method, referenced_by_message,
            timestamp_resolved, timestamp_source, semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            txt_file_id,
            obra,
            txt_rel_path,
            "text",
            txt_sha,
            txt_path.stat().st_size,
            pdf_file_id,
            method,
            src_row["referenced_by_message"],
            src_row["timestamp_resolved"],
            src_row["timestamp_source"],
            "awaiting_classification",
            now,
        ),
    )

    conn.execute(
        """
        INSERT OR IGNORE INTO documents (
            obra, file_id, text, page_count, extraction_method, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (obra, txt_file_id, text, page_count, method, now),
    )

    conn.execute(
        """
        INSERT OR IGNORE INTO media_derivations (
            obra, source_file_id, derived_file_id, derivation_method, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (obra, pdf_file_id, txt_file_id, method, now),
    )

    # Atualiza status do PDF-fonte: extração concluída. O .txt derivado
    # aguarda classificação (sinalizado em semantic_status acima).
    conn.execute(
        "UPDATE files SET semantic_status = 'extracted' WHERE file_id = ?",
        (pdf_file_id,),
    )

    conn.commit()
    return txt_file_id


__all__ = [
    "EXTRACTION_METHOD",
    "UnsupportedDocumentFormatError",
    "extract_document_handler",
    "extract_text_from_document",
]
