"""
MediaSource — acesso a mídia copy-on-demand (Sessão 7 / #42).

Antes desta sessão, o ingestor extraía 100% das mídias do ZIP no
início (`zipfile.extractall`). Em ZIP de 5GB com 12k áudios + 5k
imagens, isso equivalia a 30-60 minutos só copiando, antes de
qualquer processamento útil — e ocupava o dobro do disco do ZIP
sem necessidade.

`MediaSource` mantém o ZIP como **fonte primária** e oferece três
modos de acesso:

1. ``open(filename)`` — file-like sem materializar em disco. Útil
   para handlers que aceitam bytes (Vision API, OCR-first).
2. ``materialize(filename)`` — copia do ZIP para o vault e retorna
   ``Path``. Necessário quando o handler precisa de file path
   (Whisper, ffmpeg).
3. ``hash_streaming(filename)`` — sha256 lendo do ZIP em chunks,
   sem extrair nem manter o blob inteiro em RAM.

Política recomendada de materialização (não enforced aqui, ficam
para os handlers individuais decidirem):

| Tipo  | Política        | Por quê                                  |
|-------|-----------------|------------------------------------------|
| Áudio | materialize     | Whisper precisa de file path             |
| Vídeo | materialize     | ffmpeg/ffprobe precisam de file path     |
| Imagem| open (bytes)    | Vision API aceita bytes inline           |
| PDF   | materialize     | pdfplumber precisa de file path          |
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from io import BufferedReader
from pathlib import Path

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

CHUNK_SIZE: int = 64 * 1024  # 64KB — equilibra throughput e RAM peak


class MediaNotFoundError(KeyError):
    """Raised quando ``filename`` não existe nem no vault nem no ZIP."""


class MediaSource:
    """
    Acesso a mídia priorizando vault local; fallback para o ZIP.

    Args:
        zip_path: ZIP-fonte preservado (ex:
            ``vault/00_raw/whatsapp-export.zip``).
        vault_path: diretório base do vault. Materializações vão
            para ``vault_path/10_media/<filename>``.
    """

    MEDIA_SUBDIR = "10_media"

    def __init__(self, zip_path: Path, vault_path: Path):
        self.zip_path = Path(zip_path)
        self.vault_path = Path(vault_path)

    # ---- Caminho local & ZIP membership ----

    def _local_path(self, filename: str) -> Path:
        return self.vault_path / self.MEDIA_SUBDIR / filename

    def has_local(self, filename: str) -> bool:
        return self._local_path(filename).exists()

    def has_in_zip(self, filename: str) -> bool:
        try:
            with zipfile.ZipFile(self.zip_path) as zf:
                return filename in zf.namelist()
        except (FileNotFoundError, zipfile.BadZipFile):
            return False

    # ---- Acesso ----

    def open(self, filename: str) -> BufferedReader:
        """
        Abre ``filename`` para leitura binária. Prioriza local; cai
        no ZIP. Caller é responsável por fechar.

        Raises:
            MediaNotFoundError: se ``filename`` não existe em nenhum lugar.
        """
        local = self._local_path(filename)
        if local.exists():
            return local.open("rb")
        # Acesso direto ao ZIP. Aviso: o ZipExtFile retornado tem
        # interface compatível com BufferedReader para read() e
        # iteração, mas não é seekable em ZIPs deflated. Caller que
        # precisar seek deve materialize() primeiro.
        try:
            zf = zipfile.ZipFile(self.zip_path)
            return zf.open(filename, "r")  # type: ignore[return-value]
        except KeyError as e:
            raise MediaNotFoundError(
                f"{filename} não encontrado nem em {local} nem em {self.zip_path}"
            ) from e
        except FileNotFoundError as e:
            raise MediaNotFoundError(
                f"ZIP-fonte {self.zip_path} não encontrado"
            ) from e

    def materialize(self, filename: str) -> Path:
        """
        Copia ``filename`` do ZIP para o vault se ainda não estiver
        local. Idempotente.

        Returns:
            Path absoluto do arquivo materializado.

        Raises:
            MediaNotFoundError: se ``filename`` não existe no ZIP.
        """
        local = self._local_path(filename)
        if local.exists():
            return local

        local.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(self.zip_path) as zf:
                with zf.open(filename, "r") as src, local.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=CHUNK_SIZE)
        except KeyError as e:
            # Limpa partial se houve falha — evita arquivo de 0 bytes
            if local.exists():
                local.unlink()
            raise MediaNotFoundError(
                f"{filename} não está no ZIP {self.zip_path}"
            ) from e
        log.info("materialized %s (%d bytes)", filename, local.stat().st_size)
        return local

    def hash_streaming(self, filename: str) -> str:
        """
        Calcula sha256 hex completo de ``filename`` lendo em chunks.

        Prioriza local se disponível (mais rápido — sem overhead do
        ZIP). Cai no ZIP em streaming.

        Returns:
            Hex string (64 chars).

        Raises:
            MediaNotFoundError: se ``filename`` não existe.
        """
        h = hashlib.sha256()
        local = self._local_path(filename)
        if local.exists():
            with local.open("rb") as f:
                for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
                    h.update(chunk)
            return h.hexdigest()
        try:
            with zipfile.ZipFile(self.zip_path) as zf:
                with zf.open(filename, "r") as f:
                    for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
                        h.update(chunk)
        except KeyError as e:
            raise MediaNotFoundError(
                f"{filename} não está no ZIP nem no vault local"
            ) from e
        return h.hexdigest()

    # ---- Cleanup ----

    def list_materialized(self) -> list[Path]:
        """Lista paths já materializados localmente."""
        media_dir = self.vault_path / self.MEDIA_SUBDIR
        if not media_dir.exists():
            return []
        return sorted(p for p in media_dir.rglob("*") if p.is_file())

    def cleanup_unused(
        self, keep: set[str] | None = None,
    ) -> tuple[int, int]:
        """
        Remove materializações ausentes do conjunto ``keep``.

        ``keep`` deve ser conjunto de filenames (não Paths) que devem
        permanecer materializados — geralmente os que estão tagged
        como evidência forense em ``files.semantic_status``.

        Args:
            keep: filenames a manter. ``None`` = manter tudo (no-op).

        Returns:
            ``(removed, freed_bytes)``.
        """
        if keep is None:
            return 0, 0
        removed = 0
        freed = 0
        for p in self.list_materialized():
            if p.name not in keep:
                freed += p.stat().st_size
                p.unlink()
                removed += 1
        log.info("cleanup_unused: removed %d files (%d bytes)", removed, freed)
        return removed, freed


__all__ = [
    "CHUNK_SIZE",
    "MediaNotFoundError",
    "MediaSource",
]
