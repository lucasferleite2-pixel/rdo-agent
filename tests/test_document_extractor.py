"""Testes do document_extractor — extração de texto de PDF (Sprint 2 §Fase 1)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from rdo_agent.document_extractor import (
    EXTRACTION_METHOD,
    UnsupportedDocumentFormatError,
    extract_document_handler,
    extract_text_from_document,
)
from rdo_agent.orchestrator import Task, TaskStatus, TaskType, init_db
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_file
from tests.conftest import REPORTLAB_AVAILABLE

requires_reportlab = pytest.mark.skipif(
    not REPORTLAB_AVAILABLE,
    reason="reportlab não disponível (dev-dep)",
)


# ---------------------------------------------------------------------------
# Fixtures locais
# ---------------------------------------------------------------------------


@pytest.fixture
def vaults_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Monkeypatch do singleton config para apontar vaults_root → tmp_path."""
    root = tmp_path / "vaults"
    settings = config.Settings(
        openai_api_key="",
        anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=root,
        log_level="WARNING",
        dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)
    return root


@pytest.fixture
def seeded_vault(
    vaults_root: Path,
    make_synthetic_pdf: Callable[..., Path],
) -> dict:
    """
    Vault inicializada com:
        - 10_media/memorial.pdf (sintético via reportlab, com texto extraível)
        - linha em messages (FK em files.referenced_by_message)
        - linha em files referenciando o PDF
    Retorna dict com obra, vault, conn, pdf_file_id, pdf_path.
    """
    obra = "OBRA_DOC"
    vault = vaults_root / obra
    media_dir = vault / "10_media"
    media_dir.mkdir(parents=True)

    pdf_src = make_synthetic_pdf(
        "memorial.pdf",
        text="Memorial Descritivo da Obra\nTexto da segunda linha.",
    )
    pdf_dst = media_dir / pdf_src.name
    pdf_dst.write_bytes(pdf_src.read_bytes())

    conn = init_db(vault)
    conn.execute(
        """
        INSERT INTO messages (
            message_id, obra, timestamp_whatsapp, sender, content, media_ref,
            raw_line, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "msg_OBRA_DOC_L0010", obra, "2026-04-04T11:43:22+00:00",
            "Engenheiro", "memorial.pdf (arquivo anexado)", "memorial.pdf",
            "04/04/2026 11:43 - Engenheiro: memorial.pdf (arquivo anexado)",
            "2026-04-17T00:00:00.000000Z",
        ),
    )
    pdf_sha = sha256_file(pdf_dst)
    pdf_file_id = f"f_{pdf_sha[:12]}"
    conn.execute(
        """
        INSERT INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            referenced_by_message, timestamp_resolved, timestamp_source,
            semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pdf_file_id, obra, f"10_media/{pdf_dst.name}", "document", pdf_sha,
            pdf_dst.stat().st_size, "msg_OBRA_DOC_L0010",
            "2026-04-04T11:43:22+00:00", "whatsapp_txt",
            "awaiting_document_processing", "2026-04-17T00:00:00.000000Z",
        ),
    )
    conn.commit()
    return {
        "obra": obra,
        "vault": vault,
        "conn": conn,
        "pdf_file_id": pdf_file_id,
        "pdf_path": pdf_dst,
    }


def _make_task(seeded: dict) -> Task:
    return Task(
        id=None,
        task_type=TaskType.EXTRACT_DOCUMENT,
        payload={
            "file_id": seeded["pdf_file_id"],
            "file_path": f"10_media/{seeded['pdf_path'].name}",
        },
        status=TaskStatus.PENDING,
        depends_on=[],
        obra=seeded["obra"],
        created_at="",
    )


# ---------------------------------------------------------------------------
# extract_text_from_document — baixo nível
# ---------------------------------------------------------------------------


@requires_reportlab
def test_extract_text_from_document_creates_txt_with_content(
    tmp_path: Path,
    make_synthetic_pdf: Callable[..., Path],
) -> None:
    pdf = make_synthetic_pdf(text="Conteúdo extraído por pdfplumber")
    out = tmp_path / "out.txt"
    returned_path, method, page_count = extract_text_from_document(pdf, out)
    assert returned_path == out
    assert method == EXTRACTION_METHOD
    assert page_count == 1
    text = out.read_text(encoding="utf-8")
    assert "Conteúdo extraído por pdfplumber" in text


def test_extract_text_from_document_raises_on_unsupported_format(tmp_path: Path) -> None:
    """docx/xlsx fora do escopo Sprint 2 — exceção custom apontando para backlog."""
    docx = tmp_path / "fake.docx"
    docx.write_bytes(b"PK\x03\x04 placeholder docx")
    with pytest.raises(UnsupportedDocumentFormatError, match="SPRINT2_BACKLOG"):
        extract_text_from_document(docx, tmp_path / "out.txt")


def test_extract_text_from_document_raises_on_corrupted_pdf(tmp_path: Path) -> None:
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"definitely not a pdf")
    with pytest.raises(RuntimeError, match="pdfplumber falhou"):
        extract_text_from_document(bad, tmp_path / "out.txt")


def test_extract_text_from_document_raises_on_missing_input(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="documento não encontrado"):
        extract_text_from_document(tmp_path / "nope.pdf", tmp_path / "out.txt")


# ---------------------------------------------------------------------------
# extract_document_handler — alto nível (orchestrator chama, persiste DB)
# ---------------------------------------------------------------------------


@requires_reportlab
def test_extract_document_handler_creates_txt_and_inserts_files_row(
    seeded_vault: dict,
) -> None:
    txt_file_id = extract_document_handler(_make_task(seeded_vault), seeded_vault["conn"])

    assert txt_file_id is not None and txt_file_id.startswith("f_")
    txt_path = seeded_vault["vault"] / "20_transcriptions" / "memorial.pdf.text.txt"
    assert txt_path.exists()
    assert txt_path.stat().st_size > 0

    row = seeded_vault["conn"].execute(
        "SELECT file_type, derived_from, derivation_method, semantic_status "
        "FROM files WHERE file_id = ?",
        (txt_file_id,),
    ).fetchone()
    assert row["file_type"] == "text"
    assert row["derived_from"] == seeded_vault["pdf_file_id"]
    assert row["derivation_method"] == EXTRACTION_METHOD
    assert row["semantic_status"] == "awaiting_classification"


@requires_reportlab
def test_extract_document_handler_inserts_documents_and_derivations_rows(
    seeded_vault: dict,
) -> None:
    txt_file_id = extract_document_handler(_make_task(seeded_vault), seeded_vault["conn"])

    doc_row = seeded_vault["conn"].execute(
        "SELECT obra, file_id, text, page_count, extraction_method "
        "FROM documents WHERE file_id = ?",
        (txt_file_id,),
    ).fetchone()
    assert doc_row is not None
    assert doc_row["obra"] == seeded_vault["obra"]
    assert "Memorial Descritivo" in doc_row["text"]
    assert doc_row["page_count"] == 1
    assert doc_row["extraction_method"] == EXTRACTION_METHOD

    deriv_row = seeded_vault["conn"].execute(
        "SELECT source_file_id, derived_file_id, derivation_method "
        "FROM media_derivations WHERE derived_file_id = ?",
        (txt_file_id,),
    ).fetchone()
    assert deriv_row is not None
    assert deriv_row["source_file_id"] == seeded_vault["pdf_file_id"]
    assert deriv_row["derivation_method"] == EXTRACTION_METHOD


@requires_reportlab
def test_extract_document_handler_inherits_metadata_from_pdf(seeded_vault: dict) -> None:
    """O .txt derivado deve herdar referenced_by_message + timestamp do PDF-fonte."""
    txt_file_id = extract_document_handler(_make_task(seeded_vault), seeded_vault["conn"])
    row = seeded_vault["conn"].execute(
        "SELECT referenced_by_message, timestamp_resolved, timestamp_source "
        "FROM files WHERE file_id = ?",
        (txt_file_id,),
    ).fetchone()
    assert row["referenced_by_message"] == "msg_OBRA_DOC_L0010"
    assert row["timestamp_resolved"] == "2026-04-04T11:43:22+00:00"
    assert row["timestamp_source"] == "whatsapp_txt"


@requires_reportlab
def test_extract_document_handler_updates_pdf_status_to_extracted(
    seeded_vault: dict,
) -> None:
    """Após handler, PDF-fonte sai de 'awaiting_document_processing' para 'extracted'."""
    extract_document_handler(_make_task(seeded_vault), seeded_vault["conn"])
    row = seeded_vault["conn"].execute(
        "SELECT semantic_status FROM files WHERE file_id = ?",
        (seeded_vault["pdf_file_id"],),
    ).fetchone()
    assert row["semantic_status"] == "extracted"


@requires_reportlab
def test_extract_document_handler_does_not_enqueue_downstream_task(
    seeded_vault: dict,
) -> None:
    """Sprint 3 (classificador) decide o que fazer; handler NÃO enfileira."""
    extract_document_handler(_make_task(seeded_vault), seeded_vault["conn"])
    rows = seeded_vault["conn"].execute(
        "SELECT task_type FROM tasks WHERE obra = ?", (seeded_vault["obra"],),
    ).fetchall()
    assert rows == []


@requires_reportlab
def test_extract_document_handler_is_idempotent(seeded_vault: dict) -> None:
    """Re-invocação não duplica linhas em files, documents nem media_derivations."""
    txt_id_1 = extract_document_handler(_make_task(seeded_vault), seeded_vault["conn"])
    txt_id_2 = extract_document_handler(_make_task(seeded_vault), seeded_vault["conn"])
    assert txt_id_1 == txt_id_2

    counts = {}
    for table in ("files", "documents", "media_derivations"):
        col = "file_id" if table != "media_derivations" else "derived_file_id"
        counts[table] = seeded_vault["conn"].execute(
            f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (txt_id_1,),
        ).fetchone()[0]
    assert counts == {"files": 1, "documents": 1, "media_derivations": 1}


# ---------------------------------------------------------------------------
# A2 — PDF escaneado / sem texto extraível
# ---------------------------------------------------------------------------


@requires_reportlab
def test_handler_accepts_scanned_pdf_with_empty_text(
    vaults_root: Path,
    make_synthetic_pdf: Callable[..., Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    PDF sem texto (escaneado / só imagem) NÃO deve falhar — task vira DONE
    com text='' em documents e log.warning emitido. Cenário esperado para
    plantas de obra escaneadas que aparecem na vault EVERALDO real.
    """
    obra = "OBRA_SCAN"
    vault = vaults_root / obra
    media_dir = vault / "10_media"
    media_dir.mkdir(parents=True)

    pdf_src = make_synthetic_pdf("planta.pdf", scanned=True)
    pdf_dst = media_dir / pdf_src.name
    pdf_dst.write_bytes(pdf_src.read_bytes())

    conn = init_db(vault)
    pdf_sha = sha256_file(pdf_dst)
    pdf_file_id = f"f_{pdf_sha[:12]}"
    conn.execute(
        """
        INSERT INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            timestamp_resolved, timestamp_source,
            semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pdf_file_id, obra, f"10_media/{pdf_dst.name}", "document", pdf_sha,
            pdf_dst.stat().st_size,
            "2026-04-04T11:43:22+00:00", "filesystem",
            "awaiting_document_processing", "2026-04-17T00:00:00.000000Z",
        ),
    )
    conn.commit()

    task = Task(
        id=None,
        task_type=TaskType.EXTRACT_DOCUMENT,
        payload={"file_id": pdf_file_id, "file_path": f"10_media/{pdf_dst.name}"},
        status=TaskStatus.PENDING,
        depends_on=[],
        obra=obra,
        created_at="",
    )

    with caplog.at_level(logging.WARNING, logger="rdo_agent.document_extractor"):
        txt_file_id = extract_document_handler(task, conn)

    # Não levantou — pseudo-DONE; result_ref preenchido.
    assert txt_file_id is not None

    # documents.text deve ser '' (vazio), não None.
    doc_row = conn.execute(
        "SELECT text, page_count FROM documents WHERE file_id = ?", (txt_file_id,),
    ).fetchone()
    assert doc_row["text"] == ""
    assert doc_row["page_count"] == 1

    # log.warning chamado com mensagem específica.
    warning_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "PDF escaneado/sem texto" in r.getMessage()
    ]
    assert len(warning_records) >= 1
