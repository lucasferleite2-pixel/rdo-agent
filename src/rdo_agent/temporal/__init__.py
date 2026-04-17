"""
Resolvedor Temporal — Camada 1.

Aplica hierarquia de confiabilidade temporal sobre cada arquivo de mídia,
registrando qual fonte foi adotada e marcando conflitos entre fontes.

Hierarquia (maior → menor confiabilidade):
    1. Timestamp da mensagem no .txt      (MÁXIMA)
    2. Nome do arquivo (padrão WA)        (ALTA)
    3. Metadados EXIF/MediaInfo           (MÉDIA)
    4. mtime do filesystem                (BAIXA — último recurso)

Conflito: divergência > CONFLICT_THRESHOLD_HOURS (2h) entre QUAISQUER duas
fontes disponíveis (não só com a escolhida). Razão probatória: filename×mtime
divergente entre si é sinal de algo estranho — útil no laudo, ainda que a
fonte escolhida seja a WA.

Quando há conflito, a confiança final é capada em MEDIUM, mesmo que a fonte
escolhida seja MAX. Princípio: melhor declarar incerteza do que fingir
certeza que não temos.

Timezone: assumido America/Sao_Paulo nesta fase. Aplicado uma única vez
após coletar cada fonte; daí em diante todo o pipeline opera em aware BRT.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from itertools import combinations
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums e dataclass
# ---------------------------------------------------------------------------


class TemporalConfidence(str, Enum):
    MAX = "max"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TemporalSource(str, Enum):
    WHATSAPP_TXT = "whatsapp_txt"
    FILENAME = "filename"
    METADATA = "metadata"
    FILESYSTEM = "filesystem"


@dataclass
class TemporalResolution:
    """Resultado da resolução temporal de um arquivo."""

    file_path: str
    timestamp_resolved: datetime  # aware, BRT
    source_used: TemporalSource
    confidence: TemporalConfidence
    all_sources: dict[TemporalSource, datetime | None]  # aware ou None
    conflict_detected: bool
    notes: list[str]


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

BRT = ZoneInfo("America/Sao_Paulo")
CONFLICT_THRESHOLD_HOURS = 2

SOURCE_HIERARCHY: tuple[TemporalSource, ...] = (
    TemporalSource.WHATSAPP_TXT,
    TemporalSource.FILENAME,
    TemporalSource.METADATA,
    TemporalSource.FILESYSTEM,
)
SOURCE_CONFIDENCE: dict[TemporalSource, TemporalConfidence] = {
    TemporalSource.WHATSAPP_TXT: TemporalConfidence.MAX,
    TemporalSource.FILENAME: TemporalConfidence.HIGH,
    TemporalSource.METADATA: TemporalConfidence.MEDIUM,
    TemporalSource.FILESYSTEM: TemporalConfidence.LOW,
}

# Filename patterns
WHATSAPP_FILENAME_RE = re.compile(
    r"^(?:IMG|VID|PTT|AUD)-(\d{8})-WA\d+\.[A-Za-z0-9]+$"
)
NATIVE_FILENAME_RE = re.compile(
    r"^(?:IMG|VID)_(\d{8})_(\d{6})\.[A-Za-z0-9]+$"
)

# EXIF tags
_EXIF_DATETIME_ORIGINAL = 0x9003
_EXIF_DATETIME = 0x0132
_EXIF_IFD_POINTER = 0x8769

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"})
_MEDIA_EXTS = frozenset({".mp4", ".mov", ".3gp", ".mkv", ".m4a", ".opus", ".wav", ".mp3"})


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def resolve_temporal(
    file_path: Path,
    whatsapp_timestamp: datetime | None = None,
) -> TemporalResolution:
    """
    Resolve o timestamp canônico de um arquivo de mídia, escolhendo a fonte
    de maior confiança disponível e marcando conflitos entre fontes.

    Raises:
        ValueError: se nenhuma fonte temporal estiver disponível.
    """
    mtime_ts = _safe_mtime(file_path)

    all_sources: dict[TemporalSource, datetime | None] = {
        TemporalSource.WHATSAPP_TXT: _to_brt(whatsapp_timestamp),
        TemporalSource.FILENAME: _to_brt(parse_from_filename(file_path.name)),
        TemporalSource.METADATA: _to_brt(extract_metadata_timestamp(file_path)),
        TemporalSource.FILESYSTEM: mtime_ts,
    }

    chosen_source: TemporalSource | None = next(
        (src for src in SOURCE_HIERARCHY if all_sources[src] is not None),
        None,
    )
    if chosen_source is None:
        raise ValueError(f"nenhuma fonte temporal disponível para {file_path}")

    chosen_ts = all_sources[chosen_source]
    confidence = SOURCE_CONFIDENCE[chosen_source]

    conflict_detected, notes = _detect_conflicts(all_sources)
    if conflict_detected and confidence in {TemporalConfidence.MAX, TemporalConfidence.HIGH}:
        confidence = TemporalConfidence.MEDIUM

    assert chosen_ts is not None  # garantido pelo next() acima
    return TemporalResolution(
        file_path=str(file_path),
        timestamp_resolved=chosen_ts,
        source_used=chosen_source,
        confidence=confidence,
        all_sources=all_sources,
        conflict_detected=conflict_detected,
        notes=notes,
    )


def parse_from_filename(filename: str) -> datetime | None:
    """
    Extrai timestamp naive do nome de arquivo no padrão WhatsApp ou câmera nativa.

    Padrão WA  → datetime(YYYY, MM, DD, 0, 0, 0) — precisão de DIA.
    Padrão IMG_/VID_ → datetime com hora completa — precisão de SEGUNDO.

    Retorna None (nunca raise) para nomes não reconhecidos ou datas inválidas.
    """
    m = WHATSAPP_FILENAME_RE.match(filename)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            return None

    m = NATIVE_FILENAME_RE.match(filename)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y%m%d %H%M%S")
        except ValueError:
            return None

    return None


def extract_metadata_timestamp(file_path: Path) -> datetime | None:
    """
    Extrai timestamp naive de metadados — EXIF (Pillow) para imagens,
    MediaInfo (pymediainfo) para vídeo/áudio.

    Retorna None se o arquivo não existe, extensão não reconhecida,
    metadados ausentes, ou erro de parsing.
    """
    if not file_path.exists():
        return None
    ext = file_path.suffix.lower()
    if ext in _IMAGE_EXTS:
        return _exif_timestamp(file_path)
    if ext in _MEDIA_EXTS:
        return _mediainfo_timestamp(file_path)
    return None


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _to_brt(ts: datetime | None) -> datetime | None:
    """Naive → aware BRT (assume já é local). Aware → astimezone BRT."""
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=BRT)
    return ts.astimezone(BRT)


def _safe_mtime(file_path: Path) -> datetime | None:
    """
    Lê mtime e converte para aware BRT.

    NOTA: mtime do filesystem é Unix epoch (UTC). Passar tz=BRT no construtor
    de fromtimestamp faz a CONVERSÃO UTC→BRT corretamente. NÃO usar
    .replace(tzinfo=BRT) aqui — interpretaria o epoch como se fosse BRT,
    introduzindo offset de 3h.
    """
    try:
        return datetime.fromtimestamp(file_path.stat().st_mtime, tz=BRT)
    except OSError:
        return None


def _is_date_only(ts: datetime) -> bool:
    """True se o timestamp está em 00:00:00.000 exato (proxy para precisão de dia)."""
    return ts.hour == 0 and ts.minute == 0 and ts.second == 0 and ts.microsecond == 0


def _drift_seconds(ts_a: datetime, ts_b: datetime) -> float:
    """
    Calcula drift entre dois timestamps, com tratamento especial para fontes
    que codificam apenas a data.

    POR QUE EXISTE
    --------------
    O padrão WA de filename (IMG-YYYYMMDD-WA*.ext) codifica APENAS a data.
    `parse_from_filename` retorna `datetime(YYYY, MM, DD, 0, 0, 0)` por
    convenção: 00:00:00 é placeholder para "hora desconhecida", não meia-noite.

    Sem este tratamento, comparar uma WA-filename (sempre 00:00) contra um
    WhatsApp timestamp à tarde (ex: 14:30) sempre dispararia conflito espúrio
    de ~14h, mesmo quando ambas as fontes concordam perfeitamente sobre a data.

    REGRA EXATA
    -----------
    Se QUALQUER um dos dois timestamps tem h=0, m=0, s=0 e μs=0, comparamos
    apenas as DATAS (granularidade de dia). Datas iguais → drift=0. Datas
    diferentes → drift = N dias × 86400s.

    Caso contrário (ambos com hora não-trivial), comparação normal por
    segundo total.

    TRADE-OFF
    ---------
    Falso-negativo possível: uma foto cravada com EXIF DateTimeOriginal
    em 00:00:00.000 exato será comparada em granularidade de dia contra
    outras fontes — perdendo a capacidade de detectar drift sub-diário
    naquele caso específico.

    POR QUE É ACEITÁVEL
    -------------------
    1. EXIF cravado em meia-noite-exata é raríssimo na prática (câmeras
       modernas registram segundo + sub-segundo, e fotos legitimamente
       tiradas exatamente à meia-noite são incomuns em obras de construção).
    2. O modo de falha é DEGRADAÇÃO GRACIOSA: perdemos sensibilidade no
       conflito mas NÃO afirmamos certeza falsa. Para o laudo, isso é
       preferível a falso-positivo (que enfraqueceria conflitos reais por
       ruído sistemático).
    3. A heurística mantém o código simples — sem precisar carregar
       metadados de "precisão" por fonte através de toda a pipeline.
    """
    if _is_date_only(ts_a) or _is_date_only(ts_b):
        return abs((ts_a.date() - ts_b.date()).days) * 86400
    return abs((ts_a - ts_b).total_seconds())


def _detect_conflicts(
    timestamps: dict[TemporalSource, datetime | None],
) -> tuple[bool, list[str]]:
    """
    Pairwise: registra cada par com drift > threshold em notes separadamente,
    para auditoria saber quais fontes exatamente discordam.
    """
    available = [(src, ts) for src, ts in timestamps.items() if ts is not None]
    notes: list[str] = []
    threshold_s = CONFLICT_THRESHOLD_HOURS * 3600
    for (src_a, ts_a), (src_b, ts_b) in combinations(available, 2):
        drift_s = _drift_seconds(ts_a, ts_b)
        if drift_s > threshold_s:
            notes.append(
                f"{src_a.value} vs {src_b.value}: {drift_s / 3600:.1f}h drift"
            )
    return bool(notes), notes


def _exif_timestamp(file_path: Path) -> datetime | None:
    """Lê DateTimeOriginal (ExifIFD) com fallback para DateTime (IFD principal)."""
    try:
        with Image.open(file_path) as img:
            exif = img.getexif()
            ts: str | None = None
            try:
                exif_ifd = exif.get_ifd(_EXIF_IFD_POINTER)
                ts = exif_ifd.get(_EXIF_DATETIME_ORIGINAL)
            except (KeyError, AttributeError):
                pass
            if ts is None:
                ts = exif.get(_EXIF_DATETIME)
            if ts is None:
                return None
            return datetime.strptime(ts, "%Y:%m:%d %H:%M:%S")
    except (OSError, ValueError) as e:
        log.warning("falha ao ler EXIF de %s: %s", file_path, e)
        return None


def _mediainfo_timestamp(file_path: Path) -> datetime | None:
    """
    Lê creation_time/encoded_date via pymediainfo. Requer libmediainfo nativa.
    Retorna None se libmediainfo ausente ou tag não presente.
    """
    try:
        from pymediainfo import MediaInfo
    except ImportError:
        log.warning("pymediainfo não importável")
        return None
    try:
        info = MediaInfo.parse(str(file_path))
    except Exception as e:  # libmediainfo ausente ou arquivo corrompido
        log.warning("falha ao parsear via MediaInfo %s: %s", file_path, e)
        return None
    for track in info.tracks:
        raw = getattr(track, "encoded_date", None) or getattr(track, "tagged_date", None)
        if not raw:
            continue
        # Formato típico: "UTC 2026-03-12 09:45:32" ou "2026-03-12 09:45:32"
        cleaned = raw.replace("UTC", "").strip()
        try:
            return datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None
