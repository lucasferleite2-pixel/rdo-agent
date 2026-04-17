"""
Extrator de Áudio de Vídeo — Camada 1 (Blueprint §4.4).

Quando um vídeo tem seu áudio extraído, o sistema mantém rastro explícito
da derivação: o áudio aponta para o vídeo de origem (files.derived_from +
files.derivation_method) e o grafo N:N é registrado em media_derivations.
Sem esse rastro, o agente-engenheiro contaria o mesmo evento duas vezes
(uma para o vídeo, outra para o áudio derivado).

Dois níveis públicos:
    extract_audio_from_video(video_path, output_path, ...) -> (Path, str)
        Baixo nível: invoca ffmpeg, retorna (path do .wav, comando exato).
    extract_audio_handler(task, conn) -> str | None
        Alto nível: chamado pelo orchestrator para tasks EXTRACT_AUDIO.
        Resolve vault_path via config.get(), persiste files +
        media_derivations e enfileira TRANSCRIBE com depends_on=[].

Dados de derivação vivem nas tabelas files + media_derivations;
processamento em massa é via run_worker do orchestrator (não há
scan_and_extract_all neste módulo — seria duplicação da fila).
"""

from __future__ import annotations

import shlex
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from rdo_agent.orchestrator import Task, TaskStatus, TaskType, enqueue
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_file
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def extract_audio_from_video(
    video_path: Path,
    output_path: Path,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
) -> tuple[Path, str]:
    """
    Extrai a faixa de áudio de um vídeo para WAV otimizado para Whisper.

    Args:
        video_path: vídeo de origem (deve existir).
        output_path: caminho final do .wav. Pais são criados se necessário.
        sample_rate: 16kHz é o ideal para Whisper.
        channels: 1 (mono) reduz tamanho sem prejudicar transcrição.

    Returns:
        (output_path, comando ffmpeg exato) — o comando volta para que o
        handler possa registrá-lo em files.derivation_method (auditoria).

    Raises:
        FileNotFoundError: se video_path não existir.
        RuntimeError: se ffmpeg sair com returncode != 0.
        OSError: se ffmpeg não estiver no PATH.
    """
    if not video_path.exists():
        raise FileNotFoundError(f"vídeo não encontrado: {video_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel", "warning",
        "-i", str(video_path),
        "-vn",
        "-ac", str(channels),
        "-ar", str(sample_rate),
        "-f", "wav",
        str(output_path),
    ]
    cmd_str = shlex.join(cmd)

    try:
        result = subprocess.run(cmd, capture_output=True, check=False)
    except FileNotFoundError as e:
        raise OSError(
            "ffmpeg não encontrado no PATH. Instale com:\n"
            "  sudo apt install ffmpeg   (Ubuntu/WSL)\n"
            "  brew install ffmpeg       (macOS)"
        ) from e

    stderr_text = result.stderr.decode(errors="replace").strip()

    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg falhou (rc={result.returncode}) em {video_path.name}: {stderr_text[:500]}"
        )

    # -loglevel warning emite warnings para stderr mesmo em sucesso.
    # Warnings são informação probatória (codec inesperado, áudio truncado,
    # PTS reordenado, etc.) — logamos cada linha mesmo no caminho feliz.
    if stderr_text:
        for line in stderr_text.splitlines():
            log.warning("ffmpeg [%s]: %s", video_path.name, line)

    return output_path, cmd_str


def extract_audio_handler(task: Task, conn: sqlite3.Connection) -> str | None:
    """
    Handler para tasks EXTRACT_AUDIO consumidas por run_worker.

    Pipeline:
        1. Resolve vault_path via config.get().vault_path(obra) — payload
           NÃO carrega vault_path (Q1: payload é dado lógico, não físico).
        2. Lê o registro do vídeo-fonte em files (para herdar
           referenced_by_message, timestamp_resolved e timestamp_source).
        3. Chama extract_audio_from_video. Falhas propagam — run_worker
           captura e marca a task como FAILED com traceback.
        4. Hash do .wav, INSERT em files com derived_from + derivation_method.
        5. INSERT OR IGNORE em media_derivations (UNIQUE garante idempotência).
        6. Enfileira TRANSCRIBE com depends_on=[] — a task EXTRACT_AUDIO
           já está RUNNING quando este código executa, então bloquear
           TRANSCRIBE em depends_on=[extract_id] seria redundante.

    Returns:
        file_id do áudio criado (vai para tasks.result_ref).
    """
    payload = task.payload
    video_file_id = payload["file_id"]
    video_rel_path = payload["file_path"]

    obra = task.obra
    vault_path = config.get().vault_path(obra)
    video_path = vault_path / video_rel_path

    src_row = conn.execute(
        "SELECT file_id, referenced_by_message, timestamp_resolved, timestamp_source "
        "FROM files WHERE file_id = ?",
        (video_file_id,),
    ).fetchone()
    if src_row is None:
        raise RuntimeError(f"vídeo {video_file_id} não encontrado em files (obra={obra})")

    audio_filename = f"{video_path.name}.audio.wav"
    audio_path = vault_path / "10_media" / audio_filename

    _, ffmpeg_cmd = extract_audio_from_video(video_path, audio_path)

    audio_sha256 = sha256_file(audio_path)
    audio_file_id = f"f_{audio_sha256[:12]}"
    audio_rel_path = f"10_media/{audio_filename}"
    now = _now_iso_utc()

    conn.execute(
        """
        INSERT OR IGNORE INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            derived_from, derivation_method, referenced_by_message,
            timestamp_resolved, timestamp_source, semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audio_file_id,
            obra,
            audio_rel_path,
            "audio",
            audio_sha256,
            audio_path.stat().st_size,
            video_file_id,
            ffmpeg_cmd,
            src_row["referenced_by_message"],
            src_row["timestamp_resolved"],
            src_row["timestamp_source"],
            "awaiting_transcription",
            now,
        ),
    )

    conn.execute(
        """
        INSERT OR IGNORE INTO media_derivations (
            obra, source_file_id, derived_file_id, derivation_method, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (obra, video_file_id, audio_file_id, ffmpeg_cmd, now),
    )

    enqueue(
        conn,
        Task(
            id=None,
            task_type=TaskType.TRANSCRIBE,
            payload={"file_id": audio_file_id, "file_path": audio_rel_path},
            status=TaskStatus.PENDING,
            depends_on=[],
            obra=obra,
            created_at="",
        ),
    )

    conn.commit()
    return audio_file_id


__all__ = [
    "extract_audio_from_video",
    "extract_audio_handler",
]
