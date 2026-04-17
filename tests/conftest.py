"""Conftest compartilhado — fixtures reutilizáveis entre arquivos de teste."""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
"""Disponibilidade do binário ffmpeg — usado para skipar testes do extractor
em ambientes onde ffmpeg não está instalado (CI mínimo, container slim)."""

try:
    import reportlab.pdfgen.canvas  # noqa: F401
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
"""Disponibilidade do reportlab (dev-dep) — usado para gerar PDFs sintéticos
nos testes do document_extractor sem commitar binários no repo."""

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


@pytest.fixture
def make_synthetic_pdf(tmp_path: Path) -> Callable[..., Path]:
    """
    Fábrica de PDFs sintéticos via reportlab. Sem commit de binários.

    Modo padrão (scanned=False): cria PDF com texto extraível via
    canvas.drawString — pdfplumber recupera o texto integralmente.

    Modo scanned=True: cria PDF apenas com formas desenhadas (rect),
    sem texto. Simula PDF escaneado / página de imagem onde
    pdfplumber.extract_text() retorna None ou vazio.

    Pré-requisito: reportlab disponível (dev-dep). Testes que usam
    devem decorar com @pytest.mark.skipif(not REPORTLAB_AVAILABLE).
    """
    def _make(
        filename: str = "memorial.pdf",
        text: str = "Memorial Descritivo da Obra\nLinha 2 do conteúdo.",
        scanned: bool = False,
    ) -> Path:
        if not REPORTLAB_AVAILABLE:
            raise RuntimeError(
                "make_synthetic_pdf requer reportlab; decore o teste "
                "com @pytest.mark.skipif(not REPORTLAB_AVAILABLE)."
            )
        from reportlab.pdfgen import canvas

        path = tmp_path / filename
        c = canvas.Canvas(str(path))
        if scanned:
            # Sem texto: apenas um retângulo preenchido. pdfplumber não
            # consegue extrair nada útil — extract_text() retorna None/"".
            c.rect(100, 100, 200, 200, fill=1)
        else:
            y = 750
            for line in text.split("\n"):
                c.drawString(100, y, line)
                y -= 20
        c.showPage()
        c.save()
        return path

    return _make


@pytest.fixture
def make_synthetic_video(tmp_path: Path) -> Callable[..., Path]:
    """
    Fábrica de vídeos sintéticos curtos via ffmpeg (testsrc + sine).

    Gera um .mp4 de ~1s com vídeo testsrc 64×64 e áudio senoidal 1kHz —
    suficiente para validar a extração de áudio sem depender de fixtures
    binárias commitadas no repositório.

    Pré-requisito: ffmpeg disponível. Testes que usam esta fixture devem
    decorar com @pytest.mark.skipif(not FFMPEG_AVAILABLE, reason=...).
    A fixture levanta RuntimeError se ffmpeg estiver ausente, garantindo
    falha barulhenta caso o skipif seja esquecido.
    """
    def _make(filename: str = "VID-20260312-WA0007.mp4", duration: float = 1.0) -> Path:
        if not FFMPEG_AVAILABLE:
            raise RuntimeError(
                "make_synthetic_video requer ffmpeg no PATH; decore o teste "
                "com @pytest.mark.skipif(not FFMPEG_AVAILABLE)."
            )
        out = tmp_path / filename
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", f"testsrc=size=64x64:rate=5:duration={duration}",
                "-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration}",
                "-c:v", "mpeg4", "-c:a", "aac", "-shortest",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
        return out

    return _make
