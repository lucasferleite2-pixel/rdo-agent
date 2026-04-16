"""
Utilitários de hashing SHA-256.

Toda a cadeia de custódia do sistema depende destes hashes serem
calculados corretamente, em streaming, sobre arquivos potencialmente grandes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK_SIZE = 1024 * 1024  # 1 MB


def sha256_file(path: Path) -> str:
    """
    Calcula SHA-256 de um arquivo em streaming.

    Args:
        path: caminho absoluto ou relativo para o arquivo.

    Returns:
        Hexdigest SHA-256 (64 caracteres minúsculos).

    Raises:
        FileNotFoundError: se o arquivo não existir.
        PermissionError: se não houver permissão de leitura.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Calcula SHA-256 de um bloco de bytes em memória."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str, encoding: str = "utf-8") -> str:
    """Calcula SHA-256 de uma string, codificada em UTF-8 por padrão."""
    return sha256_bytes(text.encode(encoding))
