"""Testes do ingestor — ponto de entrada da cadeia de custódia."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from rdo_agent.ingestor import (
    DB_FILENAME,
    IngestConflictError,
    IngestManifest,
    create_vault_structure,
    run_ingest,
    validate_whatsapp_zip,
)

# ---------------------------------------------------------------------------
# Fixture local: monkeypatch de ots+git para isolar testes de I/O externo.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_ots_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Faz ots stamp 'sempre funcionar' criando um .ots placeholder."""

    def fake_stamp(manifest_path: Path) -> tuple[str | None, bool]:
        ots = manifest_path.with_suffix(manifest_path.suffix + ".ots")
        ots.write_bytes(b"FAKE_OTS_PROOF")
        return ots.name, False

    monkeypatch.setattr("rdo_agent.ingestor._ots_stamp", fake_stamp)


@pytest.fixture
def fake_ots_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simula calendar OTS indisponível."""
    monkeypatch.setattr(
        "rdo_agent.ingestor._ots_stamp",
        lambda manifest_path: (None, True),
    )


# ---------------------------------------------------------------------------
# validate_whatsapp_zip
# ---------------------------------------------------------------------------


def test_validate_zip_accepts_valid_export(
    make_synthetic_zip: Callable[..., Path],
) -> None:
    assert validate_whatsapp_zip(make_synthetic_zip()) is True


def test_validate_zip_rejects_missing_chat_txt(tmp_path: Path) -> None:
    bad = tmp_path / "no_txt.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("IMG.jpg", b"fake")
    assert validate_whatsapp_zip(bad) is False


def test_validate_zip_rejects_corrupted_file(tmp_path: Path) -> None:
    bad = tmp_path / "corrupt.zip"
    bad.write_bytes(b"not a zip at all")
    assert validate_whatsapp_zip(bad) is False


def test_validate_zip_accepts_localized_filename(tmp_path: Path) -> None:
    """Exports em pt-BR usam 'Conversa do WhatsApp com X.txt' em vez de _chat.txt."""
    z = tmp_path / "localized.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Conversa do WhatsApp com Equipe.txt", "12/03/2026 09:00 - X: hi\n")
    assert validate_whatsapp_zip(z) is True


# ---------------------------------------------------------------------------
# create_vault_structure
# ---------------------------------------------------------------------------


def test_create_vault_structure_creates_all_subdirs(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    create_vault_structure(vault)
    for d in ("00_raw", "10_media", "20_transcriptions", "30_visual",
              "40_events", "50_daily", "60_rdo"):
        assert (vault / d).is_dir()
    for sub in ("openai_api", "anthropic_api", "execution"):
        assert (vault / "99_logs" / sub).is_dir()


def test_create_vault_structure_does_not_create_obsidian_or_git(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    create_vault_structure(vault)
    assert not (vault / ".obsidian").exists()
    assert not (vault / ".git").exists()
    assert not (vault / DB_FILENAME).exists()


def test_create_vault_structure_idempotent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    create_vault_structure(vault)
    (vault / "00_raw" / "marker.txt").write_text("não devo ser apagado")
    create_vault_structure(vault)
    assert (vault / "00_raw" / "marker.txt").read_text() == "não devo ser apagado"


# ---------------------------------------------------------------------------
# run_ingest — pipeline completo
# ---------------------------------------------------------------------------


def test_run_ingest_full_pipeline_smoke(
    tmp_path: Path,
    make_synthetic_zip: Callable[..., Path],
    fake_ots_success: None,
) -> None:
    z = make_synthetic_zip()
    manifest = run_ingest(z, "OBRA_X", tmp_path / "vaults")

    assert manifest.obra == "OBRA_X"
    assert manifest.zip_sha256 and len(manifest.zip_sha256) == 64
    assert manifest.messages_count == 3
    assert any(f["file_type"] == "image" for f in manifest.files)
    assert manifest.opentimestamps_proof == "evidence_manifest.json.ots"
    assert manifest.opentimestamps_pending is False
    assert manifest.was_already_ingested is False


def test_run_ingest_writes_manifest_to_raw_dir(
    tmp_path: Path,
    make_synthetic_zip: Callable[..., Path],
    fake_ots_success: None,
) -> None:
    z = make_synthetic_zip()
    manifest = run_ingest(z, "OBRA_X", tmp_path / "vaults")
    p = tmp_path / "vaults" / "OBRA_X" / "00_raw" / "evidence_manifest.json"
    assert p.exists()
    on_disk = json.loads(p.read_text())
    assert on_disk["zip_sha256"] == manifest.zip_sha256


def test_run_ingest_writes_messages_to_db(
    tmp_path: Path,
    make_synthetic_zip: Callable[..., Path],
    fake_ots_success: None,
) -> None:
    z = make_synthetic_zip()
    run_ingest(z, "OBRA_X", tmp_path / "vaults")
    db = tmp_path / "vaults" / "OBRA_X" / DB_FILENAME
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT message_id, sender, media_ref FROM messages ORDER BY message_id").fetchall()
    assert len(rows) == 3
    # Mensagem da imagem deve referenciar o filename
    assert any(r[2] == "IMG-20260312-WA0015.jpg" for r in rows)
    # IDs no formato esperado
    assert all(r[0].startswith("msg_OBRA_X_L") for r in rows)


def test_run_ingest_writes_files_with_hashes_to_db(
    tmp_path: Path,
    make_synthetic_zip: Callable[..., Path],
    fake_ots_success: None,
) -> None:
    z = make_synthetic_zip()
    run_ingest(z, "OBRA_X", tmp_path / "vaults")
    conn = sqlite3.connect(tmp_path / "vaults" / "OBRA_X" / DB_FILENAME)
    rows = conn.execute(
        "SELECT file_id, file_type, sha256, referenced_by_message, "
        "timestamp_resolved, timestamp_source, semantic_status FROM files"
    ).fetchall()
    # 2 arquivos: o _chat.txt e a imagem
    assert len(rows) == 2
    image_row = next(r for r in rows if r[1] == "image")
    assert len(image_row[2]) == 64                                  # sha256 válido
    assert image_row[3] is not None                                 # referenciada por msg
    assert image_row[4] is not None                                 # tem timestamp resolvido
    assert image_row[5] in {"whatsapp_txt", "filename", "metadata", "filesystem"}
    assert image_row[6] == "awaiting_vision"


def test_run_ingest_enqueues_downstream_tasks(
    tmp_path: Path,
    make_synthetic_zip: Callable[..., Path],
    fake_ots_success: None,
) -> None:
    z = make_synthetic_zip()
    run_ingest(z, "OBRA_X", tmp_path / "vaults")
    conn = sqlite3.connect(tmp_path / "vaults" / "OBRA_X" / DB_FILENAME)
    rows = conn.execute(
        "SELECT task_type, status, payload FROM tasks WHERE obra=?",
        ("OBRA_X",),
    ).fetchall()
    # Apenas 1 task: VISUAL_ANALYSIS para a imagem (txt e _chat não disparam tasks)
    task_types = [r[0] for r in rows]
    assert "visual_analysis" in task_types
    for r in rows:
        assert r[1] == "pending"
        # Payload é JSON com file_id + file_path
        payload = json.loads(r[2])
        assert "file_id" in payload and "file_path" in payload


def test_run_ingest_marks_raw_readonly(
    tmp_path: Path,
    make_synthetic_zip: Callable[..., Path],
    fake_ots_success: None,
) -> None:
    z = make_synthetic_zip()
    run_ingest(z, "OBRA_X", tmp_path / "vaults")
    raw = tmp_path / "vaults" / "OBRA_X" / "00_raw"
    # Diretório 0o555 e arquivos 0o444
    assert (raw.stat().st_mode & 0o777) == 0o555
    for f in raw.iterdir():
        assert (f.stat().st_mode & 0o777) == 0o444


def test_run_ingest_continues_when_ots_unavailable(
    tmp_path: Path,
    make_synthetic_zip: Callable[..., Path],
    fake_ots_offline: None,
) -> None:
    z = make_synthetic_zip()
    manifest = run_ingest(z, "OBRA_X", tmp_path / "vaults")
    assert manifest.opentimestamps_proof is None
    assert manifest.opentimestamps_pending is True
    # Pipeline ainda completou: manifest existe no disco
    assert (tmp_path / "vaults" / "OBRA_X" / "00_raw" / "evidence_manifest.json").exists()


def test_run_ingest_creates_git_commit(
    tmp_path: Path,
    make_synthetic_zip: Callable[..., Path],
    fake_ots_success: None,
) -> None:
    z = make_synthetic_zip()
    manifest = run_ingest(z, "OBRA_X", tmp_path / "vaults")
    # git pode estar ausente; se presente, hash de commit deve ser preenchido
    if manifest.git_commit_hash is not None:
        assert len(manifest.git_commit_hash) == 40  # SHA-1 hex
        assert (tmp_path / "vaults" / "OBRA_X" / ".git").is_dir()


def test_run_ingest_raises_on_missing_zip(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_ingest(tmp_path / "nope.zip", "OBRA_X", tmp_path / "vaults")


def test_run_ingest_raises_on_invalid_zip(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"definitely not a zip")
    with pytest.raises(ValueError, match="zip inválido"):
        run_ingest(bad, "OBRA_X", tmp_path / "vaults")


# ---------------------------------------------------------------------------
# A2 + A3 — idempotência e detecção de conflito
# ---------------------------------------------------------------------------


def test_run_ingest_returns_existing_manifest_on_same_zip(
    tmp_path: Path,
    make_synthetic_zip: Callable[..., Path],
    fake_ots_success: None,
) -> None:
    z = make_synthetic_zip()
    vault_root = tmp_path / "vaults"
    first = run_ingest(z, "OBRA_X", vault_root)
    assert first.was_already_ingested is False

    second = run_ingest(z, "OBRA_X", vault_root)
    assert second.was_already_ingested is True
    assert second.zip_sha256 == first.zip_sha256
    assert second.ingest_timestamp == first.ingest_timestamp

    # Não duplica registros em messages/files
    conn = sqlite3.connect(vault_root / "OBRA_X" / DB_FILENAME)
    msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    assert msg_count == first.messages_count
    assert file_count == len(first.files)


def test_run_ingest_raises_on_different_zip_same_obra(
    tmp_path: Path,
    make_synthetic_zip: Callable[..., Path],
    fake_ots_success: None,
) -> None:
    vault_root = tmp_path / "vaults"
    z_a = make_synthetic_zip("a.zip")
    z_b = make_synthetic_zip(
        "b.zip",
        chat_content="12/03/2026 10:00 - X: conversa diferente para mudar o hash\n",
        with_jpeg=False,
    )
    first = run_ingest(z_a, "OBRA_X", vault_root)
    with pytest.raises(IngestConflictError) as exc:
        run_ingest(z_b, "OBRA_X", vault_root)
    msg = str(exc.value)
    # Mensagem deve conter os dois hashes truncados
    assert first.zip_sha256[:8] in msg
    second_hash = __import__("rdo_agent.utils.hashing", fromlist=["sha256_file"]).sha256_file(z_b)
    assert second_hash[:8] in msg


# ---------------------------------------------------------------------------
# Serialização canônica do manifest
# ---------------------------------------------------------------------------


def test_manifest_to_dict_is_json_serializable(
    tmp_path: Path,
    make_synthetic_zip: Callable[..., Path],
    fake_ots_success: None,
) -> None:
    z = make_synthetic_zip()
    manifest = run_ingest(z, "OBRA_X", tmp_path / "vaults")
    d = manifest.to_dict()
    # Round-trip JSON
    s = json.dumps(d, sort_keys=True)
    recovered = json.loads(s)
    assert recovered["zip_sha256"] == manifest.zip_sha256
    assert IngestManifest(**recovered).zip_sha256 == manifest.zip_sha256


# ---------------------------------------------------------------------------
# PDFs e outros documentos — Sprint 1 só classifica; Sprint 2 processa
# ---------------------------------------------------------------------------


def test_pdf_is_classified_as_document(
    tmp_path: Path,
    make_synthetic_zip: Callable[..., Path],
    fake_ots_success: None,
) -> None:
    """
    PDFs devem entrar em files com file_type='document' e
    semantic_status='awaiting_document_processing'. Sprint 1 NÃO enfileira
    task — Sprint 2 cria EXTRACT_DOCUMENT (ver SPRINT2_BACKLOG.md).
    """
    pdf_bytes = b"%PDF-1.4\n1 0 obj<<>>endobj\nxref\n0 1\n%%EOF\n"
    z = make_synthetic_zip(extra_files={"memorial_descritivo.pdf": pdf_bytes})
    run_ingest(z, "OBRA_X", tmp_path / "vaults")

    conn = sqlite3.connect(tmp_path / "vaults" / "OBRA_X" / DB_FILENAME)
    rows = conn.execute(
        "SELECT file_path, file_type, semantic_status FROM files "
        "WHERE file_path LIKE '%.pdf'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "document"
    assert rows[0][2] == "awaiting_document_processing"

    # Sprint 1: nenhuma task enfileirada para o PDF
    task_rows = conn.execute(
        "SELECT payload FROM tasks WHERE payload LIKE '%memorial_descritivo.pdf%'"
    ).fetchall()
    assert task_rows == []
