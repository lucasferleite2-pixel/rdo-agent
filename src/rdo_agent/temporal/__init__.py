"""
Resolvedor Temporal — Camada 1.

Aplica hierarquia de confiabilidade temporal sobre cada arquivo de mídia,
registrando qual fonte foi adotada e marcando conflitos.

Hierarquia (maior → menor confiabilidade):
    1. Timestamp da mensagem no .txt      (MÁXIMA)
    2. Nome do arquivo (padrão WA)        (ALTA)
    3. Metadados EXIF/MediaInfo           (MÉDIA)
    4. mtime do filesystem                (BAIXA — último recurso)

Quando duas fontes divergem em >2h, marca conflict_detected=True e rebaixa
a confiança final para MEDIUM mesmo que a fonte escolhida seja MAX.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path


class TemporalConfidence(str, Enum):
    MAX = "max"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TemporalSource(str, Enum):
    WHATSAPP_TXT = "whatsapp_txt"
    FILENAME = "filename"
    METADATA = "metadata"  # EXIF ou MediaInfo
    FILESYSTEM = "filesystem"


@dataclass
class TemporalResolution:
    """Resultado da resolução temporal de um arquivo."""

    file_path: str
    timestamp_resolved: datetime
    source_used: TemporalSource
    confidence: TemporalConfidence
    all_sources: dict[TemporalSource, datetime | None]
    conflict_detected: bool
    notes: list[str]


CONFLICT_THRESHOLD_HOURS = 2


def resolve_temporal(
    file_path: Path,
    whatsapp_timestamp: datetime | None = None,
) -> TemporalResolution:
    """
    Resolve o timestamp canônico de um arquivo de mídia.

    Args:
        file_path: caminho do arquivo
        whatsapp_timestamp: timestamp da mensagem que referencia este
            arquivo, vindo do parser do .txt. Se None, pula nível 1.

    Returns:
        TemporalResolution com fonte escolhida e metadados completos

    Pipeline:
        1. Coletar timestamps de todas as fontes disponíveis:
           - whatsapp_timestamp (se fornecido)
           - parse_from_filename(file_path.name)
           - extract_metadata_timestamp(file_path)
           - file_path.stat().st_mtime
        2. Aplicar hierarquia — escolher a fonte de maior confiança disponível
        3. Verificar conflitos entre fontes disponíveis
        4. Retornar TemporalResolution completo
    """
    # TODO Sprint 1
    raise NotImplementedError


def parse_from_filename(filename: str) -> datetime | None:
    """
    Extrai timestamp do nome de arquivo no padrão WhatsApp.

    Padrões suportados:
        IMG-20260312-WA0015.jpg      → 2026-03-12 (hora 00:00)
        VID-20260312-WA0007.mp4      → 2026-03-12
        PTT-20260312-WA0007.opus     → 2026-03-12 (áudio push-to-talk)
        AUD-20260312-WA0001.m4a      → 2026-03-12
        IMG_20260312_094532.jpg      → 2026-03-12 09:45:32 (câmeras nativas)

    Returns:
        datetime ou None se padrão não reconhecido.
    """
    # TODO Sprint 1
    raise NotImplementedError


def extract_metadata_timestamp(file_path: Path) -> datetime | None:
    """
    Extrai timestamp de metadados EXIF (imagens) ou MediaInfo (vídeo/áudio).

    Para imagens: tenta EXIF DateTimeOriginal, depois DateTime.
    Para vídeo/áudio: tenta creation_time via pymediainfo.

    Returns:
        datetime ou None se não houver metadados.
    """
    # TODO Sprint 1
    raise NotImplementedError


def _has_conflict(timestamps: dict[TemporalSource, datetime | None]) -> bool:
    """Verifica se há divergência >CONFLICT_THRESHOLD_HOURS entre as fontes."""
    # TODO Sprint 1
    raise NotImplementedError
