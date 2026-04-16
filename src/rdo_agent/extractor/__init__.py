"""
Extrator de Áudio de Vídeo + Grafo de Derivação — Camada 1.

Quando um vídeo tem seu áudio extraído, o sistema DEVE manter rastro
explícito da derivação. Caso contrário, o agente-engenheiro pode contar
o mesmo evento duas vezes (uma para o vídeo original, outra para o
áudio derivado).

Este módulo resolve esse problema com um grafo de derivação persistido
no SQLite (tabela media_derivations).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DerivationRecord:
    """Registro de derivação de mídia — corresponde a uma linha na tabela media_derivations."""

    derived_file_id: str        # ex: "f_VID20260312WA0007_audio"
    derived_file_path: str
    derived_file_type: str      # "audio", "frame", etc.
    derived_sha256: str

    source_file_id: str         # ex: "f_VID20260312WA0007_mp4"
    source_file_path: str
    source_sha256: str

    derivation_method: str      # comando ffmpeg exato usado, para auditoria
    referenced_by_message_id: str | None


def extract_audio_from_video(
    video_path: Path,
    output_dir: Path,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Path:
    """
    Extrai a faixa de áudio de um vídeo, otimizada para Whisper.

    Args:
        video_path: caminho do vídeo de origem
        output_dir: pasta onde salvar o áudio extraído
        sample_rate: 16kHz é o ideal para Whisper
        channels: 1 (mono) reduz tamanho e não prejudica transcrição

    Returns:
        Path do arquivo de áudio gerado

    Nome do arquivo de saída:
        video "VID-20260312-WA0007.mp4" → "VID-20260312-WA0007__audio.wav"
        (dois underscores indicam derivação explícita)

    Comando ffmpeg usado:
        ffmpeg -i INPUT -vn -ac 1 -ar 16000 -f wav OUTPUT
            -vn        → no video (só áudio)
            -ac 1      → 1 canal (mono)
            -ar 16000  → 16kHz sample rate

    Raises:
        FileNotFoundError: se video_path não existir
        RuntimeError: se ffmpeg falhar (verificar stderr)
    """
    # TODO Sprint 1
    raise NotImplementedError


def scan_and_extract_all(media_dir: Path, output_dir: Path) -> list[DerivationRecord]:
    """
    Varre uma pasta de mídias e extrai áudio de todos os vídeos encontrados.

    Vídeos suportados: .mp4, .3gp, .mov, .mkv, .webm

    Para cada vídeo:
        1. Extrai áudio (chama extract_audio_from_video)
        2. Calcula SHA-256 do áudio extraído
        3. Cria DerivationRecord vinculando áudio → vídeo
        4. Registra no SQLite (tabela media_derivations)

    Returns:
        Lista de DerivationRecord criados
    """
    # TODO Sprint 1
    raise NotImplementedError


def _check_ffmpeg_installed() -> None:
    """
    Verifica se ffmpeg está disponível no PATH.

    Raises:
        RuntimeError: com mensagem instrutiva de instalação se não estiver.
    """
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            check=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(
            "ffmpeg não encontrado no PATH. Instale com:\n"
            "  sudo apt install ffmpeg   (Ubuntu/WSL)\n"
            "  brew install ffmpeg       (macOS)"
        ) from e
