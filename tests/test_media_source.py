"""Testes do MediaSource — Sessao 7, divida #42."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from rdo_agent.ingestor.media_source import (
    MediaNotFoundError,
    MediaSource,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_zip(tmp_path: Path) -> Path:
    """Cria ZIP sintetico com algumas midias."""
    zp = tmp_path / "export.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("audio_001.opus", b"AUDIO_FAKE_BYTES_" * 32)
        zf.writestr("image_001.jpg", b"\xff\xd8\xff\xe0FAKE_JPEG_BYTES")
        zf.writestr("doc_001.pdf", b"%PDF-1.4 fake content " * 8)
    return zp


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture
def media(sample_zip: Path, vault: Path) -> MediaSource:
    return MediaSource(zip_path=sample_zip, vault_path=vault)


# ---------------------------------------------------------------------------
# has_local / has_in_zip
# ---------------------------------------------------------------------------


def test_has_in_zip_returns_true_for_existing(media: MediaSource):
    assert media.has_in_zip("audio_001.opus") is True
    assert media.has_in_zip("inexistente.opus") is False


def test_has_local_false_when_not_materialized(media: MediaSource):
    assert media.has_local("audio_001.opus") is False


# ---------------------------------------------------------------------------
# open()
# ---------------------------------------------------------------------------


def test_open_from_zip_when_local_absent(media: MediaSource):
    with media.open("audio_001.opus") as f:
        data = f.read()
    assert b"AUDIO_FAKE_BYTES_" in data


def test_open_from_local_when_present(media: MediaSource, vault: Path):
    """Local materializado tem prioridade sobre ZIP."""
    media_dir = vault / MediaSource.MEDIA_SUBDIR
    media_dir.mkdir(parents=True)
    local_payload = b"LOCAL_OVERRIDE_PAYLOAD"
    (media_dir / "audio_001.opus").write_bytes(local_payload)

    with media.open("audio_001.opus") as f:
        data = f.read()
    assert data == local_payload  # local venceu, ignorou ZIP


def test_open_raises_when_not_found(media: MediaSource):
    with pytest.raises(MediaNotFoundError, match="inexistente.opus"):
        media.open("inexistente.opus")


def test_open_raises_when_zip_missing(tmp_path: Path):
    src = MediaSource(zip_path=tmp_path / "noexist.zip", vault_path=tmp_path)
    with pytest.raises(MediaNotFoundError, match="não encontrado"):
        src.open("any.opus")


# ---------------------------------------------------------------------------
# materialize()
# ---------------------------------------------------------------------------


def test_materialize_creates_file(media: MediaSource, vault: Path):
    path = media.materialize("audio_001.opus")
    assert path.exists()
    assert path.parent.name == MediaSource.MEDIA_SUBDIR
    assert b"AUDIO_FAKE_BYTES_" in path.read_bytes()


def test_materialize_idempotent(media: MediaSource):
    p1 = media.materialize("audio_001.opus")
    mtime1 = p1.stat().st_mtime
    p2 = media.materialize("audio_001.opus")
    # Segundo materialize NAO reescreve (mtime preservado)
    assert p1 == p2
    assert p2.stat().st_mtime == mtime1


def test_materialize_preserves_full_content(media: MediaSource, sample_zip: Path):
    """Conteudo materializado eh byte-identical ao do ZIP."""
    path = media.materialize("doc_001.pdf")
    materialized_bytes = path.read_bytes()

    with zipfile.ZipFile(sample_zip) as zf:
        with zf.open("doc_001.pdf") as f:
            zip_bytes = f.read()
    assert materialized_bytes == zip_bytes


def test_materialize_raises_when_filename_not_in_zip(media: MediaSource):
    with pytest.raises(MediaNotFoundError, match="inexistente.opus"):
        media.materialize("inexistente.opus")


# ---------------------------------------------------------------------------
# hash_streaming()
# ---------------------------------------------------------------------------


def test_hash_streaming_matches_full_hash(media: MediaSource, sample_zip: Path):
    """sha256 streaming bate com sha256 do conteudo inteiro."""
    with zipfile.ZipFile(sample_zip) as zf:
        with zf.open("audio_001.opus") as f:
            full = hashlib.sha256(f.read()).hexdigest()
    streamed = media.hash_streaming("audio_001.opus")
    assert streamed == full


def test_hash_streaming_uses_local_when_present(
    media: MediaSource, vault: Path,
):
    """Local materializado eh usado pelo hash (mais rapido que ZIP)."""
    media.materialize("image_001.jpg")
    h_after_materialize = media.hash_streaming("image_001.jpg")

    # Mesmo hash quando recalculado (consistencia)
    assert media.hash_streaming("image_001.jpg") == h_after_materialize


def test_hash_streaming_raises_when_not_found(media: MediaSource):
    with pytest.raises(MediaNotFoundError):
        media.hash_streaming("inexistente.bin")


# ---------------------------------------------------------------------------
# Disk usage check (smoke test do ganho de #42)
# ---------------------------------------------------------------------------


def test_disk_usage_lower_with_copy_on_demand(media: MediaSource, vault: Path):
    """
    Smoke test do ganho da divida #42: depois de open() apenas (sem
    materialize), o vault local NAO tem o arquivo.
    """
    with media.open("audio_001.opus") as f:
        _ = f.read()
    assert not media.has_local("audio_001.opus")
    # list_materialized vazio
    assert media.list_materialized() == []


# ---------------------------------------------------------------------------
# cleanup_unused
# ---------------------------------------------------------------------------


def test_cleanup_unused_removes_unkept(media: MediaSource):
    """Materializa 3 e mantem so 1 — cleanup remove 2."""
    media.materialize("audio_001.opus")
    media.materialize("image_001.jpg")
    media.materialize("doc_001.pdf")
    assert len(media.list_materialized()) == 3

    removed, freed = media.cleanup_unused(keep={"audio_001.opus"})
    assert removed == 2
    assert freed > 0
    remaining = [p.name for p in media.list_materialized()]
    assert remaining == ["audio_001.opus"]


def test_cleanup_unused_none_is_noop(media: MediaSource):
    media.materialize("audio_001.opus")
    removed, freed = media.cleanup_unused(keep=None)
    assert removed == 0 and freed == 0
    assert media.has_local("audio_001.opus")
