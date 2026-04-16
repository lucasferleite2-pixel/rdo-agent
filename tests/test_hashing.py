"""Testes do módulo de hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from rdo_agent.utils.hashing import sha256_bytes, sha256_file, sha256_text


def test_sha256_bytes_empty() -> None:
    # Hash SHA-256 conhecido de bytes vazios
    expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert sha256_bytes(b"") == expected


def test_sha256_bytes_hello() -> None:
    expected = hashlib.sha256(b"hello").hexdigest()
    assert sha256_bytes(b"hello") == expected


def test_sha256_text_utf8() -> None:
    expected = hashlib.sha256("olá mundo".encode("utf-8")).hexdigest()
    assert sha256_text("olá mundo") == expected


def test_sha256_file(tmp_path: Path) -> None:
    file_path = tmp_path / "test.txt"
    content = b"conteudo de teste para hash"
    file_path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert sha256_file(file_path) == expected


def test_sha256_file_large(tmp_path: Path) -> None:
    """Verifica que o streaming funciona para arquivos maiores que o CHUNK_SIZE."""
    file_path = tmp_path / "large.bin"
    # 3 MB de zeros — força pelo menos 3 iterações do loop de chunks
    content = b"\x00" * (3 * 1024 * 1024)
    file_path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert sha256_file(file_path) == expected


def test_sha256_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        sha256_file(tmp_path / "does_not_exist.txt")
