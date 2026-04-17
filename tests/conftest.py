"""Conftest compartilhado — fixtures reutilizáveis entre arquivos de teste."""

from __future__ import annotations

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
