"""Conftest compartilhado — fixtures reutilizáveis entre arquivos de teste."""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

# Tags EXIF — referência: https://exiftool.org/TagNames/EXIF.html
EXIF_DATETIME = 0x0132          # IFD principal
EXIF_DATETIME_ORIGINAL = 0x9003  # ExifIFD (tag canônica de captura)
EXIF_IFD_POINTER = 0x8769        # ponteiro para o ExifIFD


@pytest.fixture
def make_jpeg_with_exif(tmp_path: Path) -> Callable[[str, datetime | None], Path]:
    """
    Fábrica de JPEGs 1×1 com EXIF DateTimeOriginal cravado.

    Uso:
        path = make_jpeg_with_exif("IMG-x.jpg", datetime(2026, 3, 12, 9, 45, 32))
        path = make_jpeg_with_exif("sem_exif.jpg", None)  # sem metadados
    """
    def _make(filename: str, exif_datetime: datetime | None = None) -> Path:
        path = tmp_path / filename
        img = Image.new("RGB", (1, 1), color=(255, 255, 255))
        if exif_datetime is None:
            img.save(path, "JPEG")
            return path
        ts_str = exif_datetime.strftime("%Y:%m:%d %H:%M:%S")
        exif = img.getexif()
        exif_ifd = exif.get_ifd(EXIF_IFD_POINTER)
        exif_ifd[EXIF_DATETIME_ORIGINAL] = ts_str
        img.save(path, "JPEG", exif=exif)
        return path

    return _make


_DEFAULT_CHAT = (
    "12/03/2026 09:00 - As mensagens são protegidas com criptografia de ponta a ponta.\n"
    "12/03/2026 09:05 - Maria Souza: Bom dia equipe\n"
    "12/03/2026 09:10 - João Silva: IMG-20260312-WA0015.jpg (arquivo anexado)\n"
)


@pytest.fixture
def make_synthetic_zip(
    tmp_path: Path,
    make_jpeg_with_exif: Callable[[str, datetime | None], Path],
) -> Callable[..., Path]:
    """
    Fábrica de zips sintéticos no formato esperado do export do WhatsApp.

    Por padrão: _chat.txt com 3 mensagens (uma referenciando uma imagem) +
    a própria imagem JPEG com EXIF cravado em 2026-03-12 09:10.
    """

    def _make(
        filename: str = "WhatsApp Chat.zip",
        chat_content: str | None = None,
        with_jpeg: bool = True,
        extra_files: dict[str, bytes] | None = None,
    ) -> Path:
        zip_path = tmp_path / filename
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("_chat.txt", chat_content or _DEFAULT_CHAT)
            if with_jpeg:
                jpeg = make_jpeg_with_exif(
                    "_seed_IMG-20260312-WA0015.jpg",
                    datetime(2026, 3, 12, 9, 10, 0),
                )
                z.write(jpeg, "IMG-20260312-WA0015.jpg")
            if extra_files:
                for name, payload in extra_files.items():
                    z.writestr(name, payload)
        return zip_path

    return _make
