"""
Video frame extraction — Sessão 9 / dívida #48.

Promove ``scripts/extract_video_frames.py`` (Sprint 4 Op3b — rodou
em EVERALDO produzindo 35 frames) para módulo formal integrado ao
state machine.

A lógica de extração de áudio de vídeo já existe em
``rdo_agent.extractor.extract_audio_from_video`` (com handler para
``TaskType.EXTRACT_AUDIO``). Aqui cobrimos o complemento — **frames
visuais**, que ficavam fora do state machine.

Funções públicas:

- ``extract_frames_for_video(conn, obra, video_file_id)`` — extrai
  5 frames adaptativos (5%, 25%, 50%, 75%, 95%) via ffprobe+ffmpeg,
  cria rows em ``files`` (file_type='image', derived_from=<video>),
  popula ``media_derivations`` e enfileira tasks
  ``TaskType.VISUAL_ANALYSIS``. Idempotente: re-executar ignora
  frames cuja sha256 já existe.

- ``process_videos_pending(conn, obra, ...)`` — drena vídeos do
  corpus que ainda não tiveram frames extraídos (detectado via
  ausência de rows em ``media_derivations`` apontando para o
  video como ``source_file_id`` com método ``ffmpeg_extract_frame_*``).
  Integra com ``StructuredLogger`` para observabilidade.

O script ``scripts/extract_video_frames.py`` continua existindo como
CLI shim, mas importa daqui — ver banner de deprecation no script.
"""

from __future__ import annotations

import shlex
import sqlite3
import subprocess
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from rdo_agent.orchestrator import (
    Task,
    TaskStatus,
    TaskType,
    enqueue,
)
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_file
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)


FRAME_PERCENTS: tuple[float, ...] = (0.05, 0.25, 0.50, 0.75, 0.95)
DERIVATION_METHOD_PREFIX = "ffmpeg_extract_frame_p"


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# ffprobe + ffmpeg helpers
# ---------------------------------------------------------------------------


class FfmpegMissingError(RuntimeError):
    """Levantada quando ffmpeg/ffprobe não está no PATH."""


def _ensure_ffmpeg() -> None:
    """Verifica que ffmpeg + ffprobe estão instalados."""
    import shutil
    if not shutil.which("ffmpeg"):
        raise FfmpegMissingError(
            "ffmpeg não encontrado no PATH. "
            "Instale via 'sudo apt install ffmpeg' ou equivalente."
        )
    if not shutil.which("ffprobe"):
        raise FfmpegMissingError(
            "ffprobe não encontrado no PATH (vem junto com ffmpeg)."
        )


def probe_duration(video_path: Path) -> float:
    """ffprobe → duração em segundos. Levanta RuntimeError em falha."""
    _ensure_ffmpeg()
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", str(video_path),
    ]
    r = subprocess.run(cmd, capture_output=True, check=True, text=True)
    out = r.stdout.strip()
    if not out:
        raise RuntimeError(f"ffprobe sem saida para {video_path}")
    return float(out)


def compute_timestamps(duration: float) -> list[float]:
    """
    Timestamps adaptativos (5%, 25%, 50%, 75%, 95%) com clamp
    simétrico ±0.5s para evitar bordas pretas iniciais/finais.
    """
    return [
        max(0.5, min(duration - 0.5, duration * p))
        for p in FRAME_PERCENTS
    ]


def extract_frame(video_path: Path, ts_sec: float, out_path: Path) -> str:
    """
    Extrai 1 frame em ``ts_sec`` para ``out_path`` via ffmpeg.
    Retorna o comando exato (string) — útil para gravar em
    ``files.derivation_method``.
    """
    _ensure_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{ts_sec:.2f}",
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(out_path),
    ]
    cmd_str = shlex.join(cmd)
    r = subprocess.run(cmd, capture_output=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(
            f"ffmpeg frame falhou (rc={r.returncode}) "
            f"{video_path.name}@{ts_sec:.1f}s: "
            f"{r.stderr.decode(errors='replace')[:300]}"
        )
    return cmd_str


# ---------------------------------------------------------------------------
# Operação principal
# ---------------------------------------------------------------------------


def extract_frames_for_video(
    conn: sqlite3.Connection, obra: str, video_file_id: str,
) -> dict:
    """
    Extrai 5 frames adaptativos do vídeo identificado por
    ``video_file_id``. Cria rows em ``files`` (file_type='image'),
    popula ``media_derivations`` e enfileira tasks
    ``TaskType.VISUAL_ANALYSIS`` para cada frame novo.

    Idempotente: se um frame produzido tem ``file_id`` já presente
    em ``files``, é skipado silenciosamente.

    Returns:
        dict com ``video_file_id``, ``duration_sec``,
        ``frames_created``, ``frames_skipped_existing``,
        ``tasks_enqueued``.

    Raises:
        FfmpegMissingError: ffmpeg/ffprobe ausentes.
        RuntimeError: video não encontrado, ffprobe falha,
            ffmpeg falha em todos os frames.
    """
    vault_path = config.get().vault_path(obra)

    src = conn.execute(
        "SELECT file_id, file_path, referenced_by_message, "
        "timestamp_resolved, timestamp_source "
        "FROM files WHERE file_id = ? AND obra = ?",
        (video_file_id, obra),
    ).fetchone()
    if src is None:
        raise RuntimeError(f"video {video_file_id} nao encontrado em files")

    video_path = vault_path / src["file_path"]
    duration = probe_duration(video_path)
    timestamps = compute_timestamps(duration)
    now = _now_iso_utc()

    frames_dir_rel = f"10_media/frames/{video_file_id}"
    frames_dir = vault_path / frames_dir_rel
    frames_dir.mkdir(parents=True, exist_ok=True)

    created = 0
    skipped = 0
    enqueued = 0

    for i, ts in enumerate(timestamps):
        pct = int(FRAME_PERCENTS[i] * 100)
        filename = f"frame_{i:02d}_p{pct:03d}.jpg"
        out_path = frames_dir / filename
        extract_frame(video_path, ts, out_path)

        frame_sha = sha256_file(out_path)
        frame_file_id = f"f_{frame_sha[:12]}"
        frame_rel_path = f"{frames_dir_rel}/{filename}"

        existing = conn.execute(
            "SELECT 1 FROM files WHERE file_id = ?",
            (frame_file_id,),
        ).fetchone()
        if existing:
            skipped += 1
            continue

        conn.execute(
            """
            INSERT OR IGNORE INTO files (
                file_id, obra, file_path, file_type, sha256, size_bytes,
                derived_from, derivation_method, referenced_by_message,
                timestamp_resolved, timestamp_source,
                semantic_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                frame_file_id, obra, frame_rel_path, "image",
                frame_sha, out_path.stat().st_size,
                video_file_id,
                f"{DERIVATION_METHOD_PREFIX}{pct:03d}",
                src["referenced_by_message"],
                src["timestamp_resolved"],
                src["timestamp_source"],
                "awaiting_analysis", now,
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO media_derivations (
                obra, source_file_id, derived_file_id, derivation_method,
                created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (obra, video_file_id, frame_file_id,
             f"{DERIVATION_METHOD_PREFIX}{pct:03d}", now),
        )
        enqueue(
            conn,
            Task(
                id=None, task_type=TaskType.VISUAL_ANALYSIS,
                payload={
                    "file_id": frame_file_id,
                    "file_path": frame_rel_path,
                },
                status=TaskStatus.PENDING, depends_on=[],
                obra=obra, created_at="", priority=5,
            ),
        )
        created += 1
        enqueued += 1

    conn.commit()
    return {
        "video_file_id": video_file_id,
        "duration_sec": round(duration, 2),
        "frames_created": created,
        "frames_skipped_existing": skipped,
        "tasks_enqueued": enqueued,
    }


# ---------------------------------------------------------------------------
# Wrapper de orquestração
# ---------------------------------------------------------------------------


def _videos_without_frames(
    conn: sqlite3.Connection, obra: str,
) -> Iterable[str]:
    """
    Retorna ``file_id``s de vídeos que ainda não têm derivações de
    frame (detectado por LEFT JOIN com ``media_derivations``
    filtrando method com prefixo ``ffmpeg_extract_frame_``).
    """
    rows = conn.execute(
        f"""
        SELECT f.file_id
          FROM files f
         WHERE f.obra = ?
           AND f.file_type = 'video'
           AND NOT EXISTS (
               SELECT 1 FROM media_derivations md
                WHERE md.obra = ?
                  AND md.source_file_id = f.file_id
                  AND md.derivation_method LIKE '{DERIVATION_METHOD_PREFIX}%'
           )
        """,
        (obra, obra),
    ).fetchall()
    return [r[0] for r in rows]


def process_videos_pending(
    conn: sqlite3.Connection, obra: str,
    *,
    max_videos: int | None = None,
    on_done=None,
    on_fail=None,
) -> dict[str, int]:
    """
    Drena vídeos do corpus que ainda não tiveram frames extraídos.
    Integra com ``StructuredLogger`` para observabilidade.

    Returns:
        dict com ``processed`` / ``failed`` / ``skipped`` (vídeos
        que já tinham frames antes desta chamada — devem ser 0
        porque o filtro inicial já exclui).
    """
    from rdo_agent.observability import StructuredLogger
    logger = StructuredLogger(obra)

    counts = {"processed": 0, "failed": 0}
    pending = list(_videos_without_frames(conn, obra))
    if max_videos is not None:
        pending = pending[:max_videos]

    for video_id in pending:
        logger.stage_start("extract_video_frames", video_id)
        t0 = time.time()
        try:
            result = extract_frames_for_video(conn, obra, video_id)
        except Exception as e:
            duration_ms = int((time.time() - t0) * 1000)
            logger.stage_failed(
                "extract_video_frames", video_id,
                type(e).__name__, str(e), duration_ms=duration_ms,
            )
            counts["failed"] += 1
            if on_fail:
                on_fail(video_id, {"error": str(e)})
            continue
        duration_ms = int((time.time() - t0) * 1000)
        logger.stage_done(
            "extract_video_frames", video_id, duration_ms,
            frames_created=result["frames_created"],
            duration_sec=result["duration_sec"],
        )
        counts["processed"] += 1
        if on_done:
            on_done(video_id, result)
    return counts


__all__ = [
    "DERIVATION_METHOD_PREFIX",
    "FRAME_PERCENTS",
    "FfmpegMissingError",
    "compute_timestamps",
    "extract_frame",
    "extract_frames_for_video",
    "probe_duration",
    "process_videos_pending",
]
