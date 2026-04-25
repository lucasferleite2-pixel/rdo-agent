"""
Pre-flight estimator (#55).

Implementação minimalista: estimativas baseadas em **counts do ZIP**
+ tabela de **rates default** (custos USD por unidade, throughput
single-machine). Rates calibradas pelo histórico do projeto até v1.2
e podem ser ajustadas nas Sessões 8/9 quando dados de produção real
chegarem.

Estrutura para evolução:

- Adicionar curvas de cache hit (#46 classify, #52 narratives) sem
  mudar API pública.
- Sobrescrever rates via env vars
  ``RDO_AGENT_PREFLIGHT_<RATE_KEY>=<value>``.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Rates default — calibrados em conversa de pricing público + observação
# do projeto (Sessões 1-6). Cada estimativa tem ±50% margem nesta versão.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rates:
    # Whisper API (OpenAI whisper-1) — Sessão 11 / fix do bug:
    # estimate originalmente assumia "Whisper local = $0", mas a
    # discovery da Sessão 8 (P1) confirmou que `transcriber/` chama
    # OpenAI Whisper API a $0.006/min (rate oficial). Subestimava
    # custo real em $10+ em corpus de 1.6k+ minutos de áudio.
    whisper_usd_per_audio_min: float = 0.006  # whisper-1 OpenAI
    whisper_seconds_per_audio_min: float = 30.0  # 30s para 1min de áudio
    # Classify (gpt-4o-mini)
    classify_usd_per_event: float = 5e-5  # ~ $0.05 por 1000 eventos
    classify_seconds_per_event: float = 0.3
    # Vision (gpt-4o)
    vision_usd_per_image: float = 0.005
    vision_seconds_per_image: float = 1.5
    # Narrator (Sonnet)
    narrator_usd_per_day: float = 0.07  # baseado em $0.31 por overview ~5 dias
    narrator_seconds_per_day: float = 25.0
    # Ingest local (parser + hash + materialize)
    ingest_seconds_per_message: float = 0.005
    ingest_seconds_per_audio_mb: float = 0.1
    # Disco — multiplicador do ZIP que vai virar dado processado
    disk_multiplier: float = 3.0  # ZIP + materialização parcial + DB


DEFAULT_RATES = Rates()


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(f"RDO_AGENT_PREFLIGHT_{name}")
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        log.warning("env override invalido %s=%r; usando default", name, val)
        return default


def _rates_from_env() -> Rates:
    """Permite override de qualquer rate via env vars."""
    return Rates(
        whisper_usd_per_audio_min=_env_float(
            "WHISPER_USD_PER_AUDIO_MIN",
            DEFAULT_RATES.whisper_usd_per_audio_min,
        ),
        whisper_seconds_per_audio_min=_env_float(
            "WHISPER_SEC_PER_AUDIO_MIN",
            DEFAULT_RATES.whisper_seconds_per_audio_min,
        ),
        classify_usd_per_event=_env_float(
            "CLASSIFY_USD_PER_EVENT",
            DEFAULT_RATES.classify_usd_per_event,
        ),
        classify_seconds_per_event=_env_float(
            "CLASSIFY_SEC_PER_EVENT",
            DEFAULT_RATES.classify_seconds_per_event,
        ),
        vision_usd_per_image=_env_float(
            "VISION_USD_PER_IMAGE",
            DEFAULT_RATES.vision_usd_per_image,
        ),
        vision_seconds_per_image=_env_float(
            "VISION_SEC_PER_IMAGE",
            DEFAULT_RATES.vision_seconds_per_image,
        ),
        narrator_usd_per_day=_env_float(
            "NARRATOR_USD_PER_DAY",
            DEFAULT_RATES.narrator_usd_per_day,
        ),
        narrator_seconds_per_day=_env_float(
            "NARRATOR_SEC_PER_DAY",
            DEFAULT_RATES.narrator_seconds_per_day,
        ),
        ingest_seconds_per_message=_env_float(
            "INGEST_SEC_PER_MSG",
            DEFAULT_RATES.ingest_seconds_per_message,
        ),
        ingest_seconds_per_audio_mb=_env_float(
            "INGEST_SEC_PER_AUDIO_MB",
            DEFAULT_RATES.ingest_seconds_per_audio_mb,
        ),
        disk_multiplier=_env_float(
            "DISK_MULTIPLIER",
            DEFAULT_RATES.disk_multiplier,
        ),
    )


# ---------------------------------------------------------------------------
# Heuristicas de classificação por extensão
# ---------------------------------------------------------------------------

AUDIO_EXTS = frozenset({".opus", ".mp3", ".m4a", ".ogg", ".aac", ".wav"})
IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})
VIDEO_EXTS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".3gp"})
PDF_EXTS = frozenset({".pdf"})

# Heurística: avg duração de áudio do WhatsApp = 45 segundos
AVG_AUDIO_SECONDS = 45.0


def _ext(name: str) -> str:
    return Path(name).suffix.lower()


def _is_chat_txt(name: str) -> bool:
    base = Path(name).name.lower()
    return base.endswith(".txt") and "chat" in base


# ---------------------------------------------------------------------------
# Counts e estimativa de mensagens via amostra
# ---------------------------------------------------------------------------


def _count_messages_from_sample(
    zf: zipfile.ZipFile, chat_filename: str, *, sample_lines: int = 5000,
) -> int:
    """
    Estima total de mensagens lendo as primeiras ``sample_lines`` linhas
    e extrapolando proporcionalmente ao tamanho do arquivo. Linha = 1
    mensagem (aproximação grosseira; não conta multi-linha como múltiplas).
    """
    info = zf.getinfo(chat_filename)
    file_size = info.file_size
    if file_size == 0:
        return 0
    sample_bytes = 0
    sample_lines_count = 0
    with zf.open(chat_filename) as f:
        # Ler em modo binário para estimar bytes-por-linha sem decode
        for line in f:
            sample_bytes += len(line)
            sample_lines_count += 1
            if sample_lines_count >= sample_lines:
                break
    if sample_lines_count == 0:
        return 0
    avg_bytes_per_line = sample_bytes / sample_lines_count
    return int(file_size / max(avg_bytes_per_line, 1))


def _classify_zip_members(
    zf: zipfile.ZipFile,
) -> tuple[int, int, int, int, int, str | None]:
    """
    Itera namelist e classifica por extensão. Retorna:
    ``(audios, images, videos, pdfs, total_zip_size_bytes, chat_filename)``.
    """
    audios = 0
    images = 0
    videos = 0
    pdfs = 0
    total_size = 0
    chat_filename: str | None = None
    for info in zf.infolist():
        total_size += info.file_size
        ext = _ext(info.filename)
        if ext in AUDIO_EXTS:
            audios += 1
        elif ext in IMAGE_EXTS:
            images += 1
        elif ext in VIDEO_EXTS:
            videos += 1
        elif ext in PDF_EXTS:
            pdfs += 1
        elif _is_chat_txt(info.filename):
            chat_filename = info.filename
    return audios, images, videos, pdfs, total_size, chat_filename


# ---------------------------------------------------------------------------
# Cost / Time breakdowns
# ---------------------------------------------------------------------------


@dataclass
class CostBreakdown:
    """Estimativa de custo USD por estágio."""

    transcribe_usd: float = 0.0  # Whisper API ($0.006/min)
    classify_usd: float = 0.0
    vision_usd: float = 0.0
    narrator_usd: float = 0.0
    margin_pct: float = 0.5  # ±50% por padrão na v1.3

    @property
    def total_usd(self) -> float:
        return (
            self.transcribe_usd + self.classify_usd
            + self.vision_usd + self.narrator_usd
        )

    @property
    def lower_bound_usd(self) -> float:
        return self.total_usd * (1 - self.margin_pct)

    @property
    def upper_bound_usd(self) -> float:
        return self.total_usd * (1 + self.margin_pct)


@dataclass
class TimeBreakdown:
    """Estimativa de tempo total em segundos por estágio."""

    ingest_sec: float = 0.0
    transcribe_sec: float = 0.0
    classify_sec: float = 0.0
    vision_sec: float = 0.0
    narrator_sec: float = 0.0

    @property
    def total_sec(self) -> float:
        return (
            self.ingest_sec + self.transcribe_sec + self.classify_sec
            + self.vision_sec + self.narrator_sec
        )

    @property
    def total_hours(self) -> float:
        return self.total_sec / 3600


# ---------------------------------------------------------------------------
# PreflightReport
# ---------------------------------------------------------------------------


@dataclass
class PreflightReport:
    """Snapshot de estimativas para apresentar ao operador antes de processar."""

    zip_path: Path
    zip_size_bytes: int = 0

    message_count_est: int = 0
    audio_count: int = 0
    image_count: int = 0
    video_count: int = 0
    pdf_count: int = 0

    audio_total_sec_est: float = 0.0
    days_count_est: int = 0  # estimativa de dias narrados (msg / ~50/dia)

    disk_required_bytes: int = 0
    disk_available_bytes: int = 0

    cost: CostBreakdown = field(default_factory=CostBreakdown)
    time: TimeBreakdown = field(default_factory=TimeBreakdown)

    warnings: list[str] = field(default_factory=list)
    rates: Rates = field(default_factory=lambda: DEFAULT_RATES)
    chat_txt_found: bool = False

    @property
    def zip_size_gb(self) -> float:
        return self.zip_size_bytes / 1e9

    @property
    def disk_required_gb(self) -> float:
        return self.disk_required_bytes / 1e9

    @property
    def disk_available_gb(self) -> float:
        return self.disk_available_bytes / 1e9

    @property
    def disk_ok(self) -> bool:
        return self.disk_available_bytes >= self.disk_required_bytes

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


def preflight_check(
    zip_path: Path,
    *,
    vault_root: Path | None = None,
    rates: Rates | None = None,
) -> PreflightReport:
    """
    Roda pre-flight contra ``zip_path`` sem extrair nada.

    Args:
        zip_path: ZIP-fonte (export do WhatsApp).
        vault_root: para checagem de disco disponível. Default
            ``~/rdo_vaults/`` (ou ``/tmp`` se ausente).
        rates: override de rates. Default = ``Rates()`` com env overrides
            aplicados via ``RDO_AGENT_PREFLIGHT_*``.

    Returns:
        :class:`PreflightReport`.

    Raises:
        FileNotFoundError: se ``zip_path`` não existe.
        zipfile.BadZipFile: se ``zip_path`` não é ZIP válido.
    """
    rates = rates or _rates_from_env()
    report = PreflightReport(zip_path=zip_path, rates=rates)

    if not zip_path.exists():
        raise FileNotFoundError(zip_path)

    report.zip_size_bytes = zip_path.stat().st_size

    with zipfile.ZipFile(zip_path) as zf:
        (
            audios, images, videos, pdfs,
            total_size_unzipped, chat_filename,
        ) = _classify_zip_members(zf)
        report.audio_count = audios
        report.image_count = images
        report.video_count = videos
        report.pdf_count = pdfs
        report.chat_txt_found = chat_filename is not None

        if chat_filename:
            report.message_count_est = _count_messages_from_sample(
                zf, chat_filename,
            )
        else:
            report.warnings.append(
                "chat.txt não encontrado no ZIP — não há mensagens "
                "para processar"
            )

    # Áudios: estimativa de duração total
    report.audio_total_sec_est = audios * AVG_AUDIO_SECONDS

    # Dias narrados: heurística msg/dia ~50 (média WhatsApp pessoal)
    report.days_count_est = max(1, report.message_count_est // 50)

    # Disco: ZIP * disk_multiplier
    report.disk_required_bytes = int(
        report.zip_size_bytes * rates.disk_multiplier
    )
    vault_root = vault_root or (Path.home() / "rdo_vaults")
    if not vault_root.exists():
        vault_root = Path("/tmp")
    try:
        report.disk_available_bytes = shutil.disk_usage(vault_root).free
    except OSError as e:
        log.warning("disk_usage falhou em %s: %s", vault_root, e)
        report.disk_available_bytes = 0
        report.warnings.append(
            f"não foi possível checar disco em {vault_root}: {e}"
        )

    if not report.disk_ok:
        report.warnings.append(
            f"disco insuficiente: requer {report.disk_required_gb:.1f} GB "
            f"em {vault_root}, disponível {report.disk_available_gb:.1f} GB"
        )

    # Cost — heurísticas simples
    audio_minutes = report.audio_total_sec_est / 60
    report.cost = CostBreakdown(
        transcribe_usd=audio_minutes * rates.whisper_usd_per_audio_min,
        classify_usd=(
            report.message_count_est
            * rates.classify_usd_per_event
        ),
        vision_usd=(
            report.image_count * rates.vision_usd_per_image
        ),
        narrator_usd=(
            report.days_count_est * rates.narrator_usd_per_day
        ),
    )

    # Time — single-machine, batch=1
    audio_minutes = report.audio_total_sec_est / 60
    report.time = TimeBreakdown(
        ingest_sec=(
            report.message_count_est * rates.ingest_seconds_per_message
            + report.zip_size_bytes / (1024 * 1024)
              * rates.ingest_seconds_per_audio_mb
        ),
        transcribe_sec=(
            audio_minutes * rates.whisper_seconds_per_audio_min
        ),
        classify_sec=(
            report.message_count_est * rates.classify_seconds_per_event
        ),
        vision_sec=(
            report.image_count * rates.vision_seconds_per_image
        ),
        narrator_sec=(
            report.days_count_est * rates.narrator_seconds_per_day
        ),
    )

    # Warnings de custo e tempo
    if report.cost.total_usd > 50:
        report.warnings.append(
            f"custo estimado alto: ${report.cost.total_usd:.0f} "
            f"(±${report.cost.upper_bound_usd - report.cost.total_usd:.0f})"
        )
    if report.time.total_hours > 24:
        report.warnings.append(
            f"tempo estimado >24h ({report.time.total_hours:.0f}h) — "
            "considere rodar em maquina dedicada"
        )

    return report


# Re-export para CLI
def format_report_lines(report: PreflightReport) -> Iterable[str]:
    """
    Gera linhas formatadas (sem rich) do report. Utilitário para CLI
    e logs. Cada linha um string já com `\\n` removido.
    """
    yield f"Pre-flight check para {report.zip_path}:"
    yield ""
    yield "CORPUS:"
    yield f"  Mensagens estimadas:  ~{report.message_count_est:,}"
    yield (
        f"  Áudios:               {report.audio_count:,}  "
        f"(~{report.audio_total_sec_est/60:.0f} min total)"
    )
    yield f"  Imagens:              {report.image_count:,}"
    yield f"  Vídeos:               {report.video_count:,}"
    yield f"  PDFs:                 {report.pdf_count:,}"
    yield f"  Tamanho ZIP:          {report.zip_size_gb:.2f} GB"
    yield f"  Dias narrados (est.): ~{report.days_count_est}"
    yield ""
    yield "RECURSOS:"
    yield f"  Disco necessário:     {report.disk_required_gb:.1f} GB"
    avail = report.disk_available_gb
    sym = "✓" if report.disk_ok else "✗"
    yield f"  Disco disponível:     {avail:.1f} GB  {sym}"
    yield ""
    yield "CUSTOS ESTIMADOS (±50%):"
    yield f"  Transcribe (Whisper API):   ${report.cost.transcribe_usd:.2f}"
    yield f"  Classify (gpt-4o-mini):     ${report.cost.classify_usd:.2f}"
    yield f"  Vision (gpt-4o):            ${report.cost.vision_usd:.2f}"
    yield f"  Narrator (Sonnet):          ${report.cost.narrator_usd:.2f}"
    yield (
        f"  TOTAL:                      ${report.cost.total_usd:.2f}  "
        f"(${report.cost.lower_bound_usd:.0f}–${report.cost.upper_bound_usd:.0f})"
    )
    yield ""
    yield "TEMPO ESTIMADO (single-machine):"
    yield f"  Ingestão:    {report.time.ingest_sec / 60:.0f} min"
    yield f"  Transcribe:  {report.time.transcribe_sec / 3600:.1f} h"
    yield f"  Classify:    {report.time.classify_sec / 60:.0f} min"
    yield f"  Vision:      {report.time.vision_sec / 60:.0f} min"
    yield f"  Narrator:    {report.time.narrator_sec / 3600:.1f} h"
    yield f"  TOTAL:       {report.time.total_hours:.1f} h"
    if report.warnings:
        yield ""
        yield "AVISOS:"
        for w in report.warnings:
            yield f"  ! {w}"
