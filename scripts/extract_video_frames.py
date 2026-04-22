"""
Extrator de frames adaptativos de video — Sprint 4 Op3b.

Para cada video na lista informada, extrai 5 frames em timestamps
adaptativos (5%, 25%, 50%, 75%, 95% da duracao) via ffprobe+ffmpeg,
cria linha em files (file_type='image', derived_from=<video_id>) e
enfileira tasks VISUAL_ANALYSIS. NAO chama API.

Usage:
    python scripts/extract_video_frames.py --obra <codesc> [file_id ...]

Se nenhum file_id for passado, usa lista interna de dias-chave
EVERALDO_SANTAQUITERIA (Sprint 4 Op3b briefing).

Output em stdout: JSON com contagem de frames criados por video.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from rdo_agent.orchestrator import (
    Task,
    TaskStatus,
    TaskType,
    enqueue,
    init_db,
)
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_file
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_KEY_DATE_VIDEOS: tuple[str, ...] = (
    "f_ecb7374a8b76",  # 08/04
    "f_1f5d5c030375",  # 14/04
    "f_1def40a04f4e",  # 14/04
    "f_1f818f64eefa",  # 15/04
    "f_ef77117947ca",  # 15/04
    "f_445a0975174b",  # 15/04
    "f_e68d7a6ac115",  # 15/04
)

FRAME_PERCENTS: tuple[float, ...] = (0.05, 0.25, 0.50, 0.75, 0.95)


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _probe_duration(video_path: Path) -> float:
    """ffprobe -> duracao em segundos (float)."""
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


def _compute_timestamps(duration: float) -> list[float]:
    """Timestamps adaptativos, clamp simetrico para evitar bordas pretas."""
    return [max(0.5, min(duration - 0.5, duration * p)) for p in FRAME_PERCENTS]


def _extract_frame(video_path: Path, ts_sec: float, out_path: Path) -> str:
    """Extrai 1 frame via ffmpeg. Retorna o comando exato (string)."""
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
            f"ffmpeg frame falhou (rc={r.returncode}) {video_path.name}@{ts_sec:.1f}s: "
            f"{r.stderr.decode(errors='replace')[:300]}"
        )
    return cmd_str


def extract_frames_for_video(
    conn, obra: str, video_file_id: str,
) -> dict:
    """
    Para um video, extrai 5 frames, cria files rows e enfileira
    VISUAL_ANALYSIS tasks. Idempotente: se frame file_id ja existe em
    files, skip (check via INSERT OR IGNORE).

    Returns:
        dict com keys: video_file_id, duration_sec, frames_created,
        frames_skipped_existing, tasks_enqueued.
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
    duration = _probe_duration(video_path)
    timestamps = _compute_timestamps(duration)
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
        _extract_frame(video_path, ts, out_path)

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
                f"ffmpeg_extract_frame_p{pct:03d}",
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
             f"ffmpeg_extract_frame_p{pct:03d}", now),
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Extrai frames adaptativos de videos.")
    parser.add_argument("--obra", required=True, help="CODESC da obra")
    parser.add_argument(
        "video_file_ids", nargs="*",
        help="Lista de file_ids de video (default: lista de dias-chave EVERALDO)",
    )
    args = parser.parse_args()

    ids = tuple(args.video_file_ids) or DEFAULT_KEY_DATE_VIDEOS
    conn = init_db(config.get().vault_path(args.obra))
    results = []
    for vid in ids:
        try:
            r = extract_frames_for_video(conn, args.obra, vid)
        except Exception as exc:
            r = {"video_file_id": vid, "error": f"{type(exc).__name__}: {exc}"}
        results.append(r)
    conn.close()

    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
