"""Testes do extractor — extração de áudio + grafo de derivação (Blueprint §4.4)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import wave
from collections.abc import Callable
from pathlib import Path

import pytest

from rdo_agent.extractor import extract_audio_from_video, extract_audio_handler
from rdo_agent.orchestrator import Task, TaskStatus, TaskType, init_db
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_file
from tests.conftest import FFMPEG_AVAILABLE

requires_ffmpeg = pytest.mark.skipif(
    not FFMPEG_AVAILABLE,
    reason="ffmpeg não disponível no PATH",
)


# ---------------------------------------------------------------------------
# Fixtures locais
# ---------------------------------------------------------------------------


@pytest.fixture
def vaults_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Monkeypatch do singleton de config para apontar vaults_root → tmp_path.
    Garante isolamento total: extract_audio_handler resolve vault_path via
    config.get(), e cada teste recebe um diretório virgem.
    """
    root = tmp_path / "vaults"
    settings = config.Settings(
        openai_api_key="",
        anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=root,
        log_level="WARNING",
        dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)
    return root


@pytest.fixture
def seeded_vault(
    vaults_root: Path,
    make_synthetic_video: Callable[..., Path],
) -> dict:
    """
    Vault inicializada com:
        - 10_media/VID-20260312-WA0007.mp4 (sintético via ffmpeg)
        - linha em files referenciando o vídeo, com metadata temporal e msg
    Retorna dict com obra, vault, conn, video_file_id, video_path.
    """
    obra = "OBRA_TEST"
    vault = vaults_root / obra
    media_dir = vault / "10_media"
    media_dir.mkdir(parents=True)

    video_src = make_synthetic_video("VID-20260312-WA0007.mp4")
    video_dst = media_dir / video_src.name
    shutil.copy2(video_src, video_dst)

    conn = init_db(vault)
    # FK em files.referenced_by_message exige a mensagem existir antes.
    conn.execute(
        """
        INSERT INTO messages (
            message_id, obra, timestamp_whatsapp, sender, content, media_ref,
            raw_line, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "msg_OBRA_TEST_L0042", obra, "2026-03-12T09:45:32+00:00",
            "João Silva", "VID-20260312-WA0007.mp4 (arquivo anexado)",
            "VID-20260312-WA0007.mp4",
            "12/03/2026 09:45 - João Silva: VID-20260312-WA0007.mp4 (arquivo anexado)",
            "2026-04-17T00:00:00.000000Z",
        ),
    )
    video_sha = sha256_file(video_dst)
    video_file_id = f"f_{video_sha[:12]}"
    conn.execute(
        """
        INSERT INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            referenced_by_message, timestamp_resolved, timestamp_source,
            semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            video_file_id, obra, f"10_media/{video_dst.name}", "video", video_sha,
            video_dst.stat().st_size, "msg_OBRA_TEST_L0042",
            "2026-03-12T09:45:32+00:00", "whatsapp_txt",
            "awaiting_audio_extraction", "2026-04-17T00:00:00.000000Z",
        ),
    )
    conn.commit()
    return {
        "obra": obra,
        "vault": vault,
        "conn": conn,
        "video_file_id": video_file_id,
        "video_path": video_dst,
    }


def _make_extract_task(seeded: dict) -> Task:
    return Task(
        id=None,
        task_type=TaskType.EXTRACT_AUDIO,
        payload={
            "file_id": seeded["video_file_id"],
            "file_path": f"10_media/{seeded['video_path'].name}",
        },
        status=TaskStatus.PENDING,
        depends_on=[],
        obra=seeded["obra"],
        created_at="",
    )


# ---------------------------------------------------------------------------
# extract_audio_from_video — baixo nível
# ---------------------------------------------------------------------------


@requires_ffmpeg
def test_extract_audio_from_video_creates_wav_file(
    tmp_path: Path,
    make_synthetic_video: Callable[..., Path],
) -> None:
    video = make_synthetic_video()
    out = tmp_path / "output.wav"
    extract_audio_from_video(video, out)
    assert out.exists()
    assert out.stat().st_size > 0


@requires_ffmpeg
def test_extract_audio_from_video_returns_path_and_command_string(
    tmp_path: Path,
    make_synthetic_video: Callable[..., Path],
) -> None:
    video = make_synthetic_video()
    out = tmp_path / "output.wav"
    returned_path, cmd_str = extract_audio_from_video(video, out)
    assert returned_path == out
    assert isinstance(cmd_str, str)
    # Sanidade: o comando inclui as flags principais
    assert "ffmpeg" in cmd_str
    assert "-vn" in cmd_str
    assert "-ar 16000" in cmd_str
    assert "-ac 1" in cmd_str


@requires_ffmpeg
def test_extract_audio_from_video_uses_16khz_mono_wav(
    tmp_path: Path,
    make_synthetic_video: Callable[..., Path],
) -> None:
    """Verifica o formato declarado: WAV PCM, 16kHz, mono."""
    video = make_synthetic_video()
    out = tmp_path / "output.wav"
    extract_audio_from_video(video, out)
    with wave.open(str(out)) as w:
        assert w.getframerate() == 16000
        assert w.getnchannels() == 1


@requires_ffmpeg
def test_extract_audio_from_video_raises_on_missing_input(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="vídeo não encontrado"):
        extract_audio_from_video(tmp_path / "nope.mp4", tmp_path / "out.wav")


@requires_ffmpeg
def test_extract_audio_from_video_raises_on_invalid_video_file(tmp_path: Path) -> None:
    """Arquivo texto disfarçado de .mp4 → ffmpeg rc != 0 → RuntimeError."""
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_text("isto não é um vídeo")
    with pytest.raises(RuntimeError, match="ffmpeg falhou"):
        extract_audio_from_video(fake_video, tmp_path / "out.wav")


# ---------------------------------------------------------------------------
# extract_audio_handler — alto nível (orquestrador chama, persiste DB)
# ---------------------------------------------------------------------------


@requires_ffmpeg
def test_extract_audio_handler_creates_audio_file_in_vault(seeded_vault: dict) -> None:
    task = _make_extract_task(seeded_vault)
    audio_file_id = extract_audio_handler(task, seeded_vault["conn"])

    assert audio_file_id is not None
    assert audio_file_id.startswith("f_")
    audio_path = seeded_vault["vault"] / "10_media" / f"{seeded_vault['video_path'].name}.audio.wav"
    assert audio_path.exists()
    assert audio_path.stat().st_size > 0


@requires_ffmpeg
def test_extract_audio_handler_inserts_files_row_with_derivation_metadata(
    seeded_vault: dict,
) -> None:
    task = _make_extract_task(seeded_vault)
    audio_file_id = extract_audio_handler(task, seeded_vault["conn"])

    row = seeded_vault["conn"].execute(
        "SELECT file_type, derived_from, derivation_method, semantic_status, sha256 "
        "FROM files WHERE file_id = ?",
        (audio_file_id,),
    ).fetchone()
    assert row is not None
    assert row["file_type"] == "audio"
    assert row["derived_from"] == seeded_vault["video_file_id"]
    # derivation_method preserva o comando ffmpeg exato (auditoria)
    assert "ffmpeg" in row["derivation_method"]
    assert "-ar 16000" in row["derivation_method"]
    assert row["semantic_status"] == "awaiting_transcription"
    assert len(row["sha256"]) == 64


@requires_ffmpeg
def test_extract_audio_handler_inserts_media_derivations_row(seeded_vault: dict) -> None:
    task = _make_extract_task(seeded_vault)
    audio_file_id = extract_audio_handler(task, seeded_vault["conn"])

    row = seeded_vault["conn"].execute(
        "SELECT obra, source_file_id, derived_file_id, derivation_method "
        "FROM media_derivations WHERE derived_file_id = ?",
        (audio_file_id,),
    ).fetchone()
    assert row is not None
    assert row["obra"] == seeded_vault["obra"]
    assert row["source_file_id"] == seeded_vault["video_file_id"]
    assert row["derived_file_id"] == audio_file_id
    assert "ffmpeg" in row["derivation_method"]


@requires_ffmpeg
def test_extract_audio_handler_inherits_temporal_and_message_metadata(
    seeded_vault: dict,
) -> None:
    """O áudio derivado deve herdar referenced_by_message + timestamp do vídeo-fonte."""
    task = _make_extract_task(seeded_vault)
    audio_file_id = extract_audio_handler(task, seeded_vault["conn"])

    row = seeded_vault["conn"].execute(
        "SELECT referenced_by_message, timestamp_resolved, timestamp_source "
        "FROM files WHERE file_id = ?",
        (audio_file_id,),
    ).fetchone()
    assert row["referenced_by_message"] == "msg_OBRA_TEST_L0042"
    assert row["timestamp_resolved"] == "2026-03-12T09:45:32+00:00"
    assert row["timestamp_source"] == "whatsapp_txt"


@requires_ffmpeg
def test_extract_audio_handler_is_idempotent_via_unique_constraint(
    seeded_vault: dict,
) -> None:
    """
    Re-invocar o handler sobre o mesmo vídeo NÃO deve duplicar files nem
    media_derivations (UNIQUE em (source_file_id, derived_file_id) +
    PRIMARY KEY de files via sha256 determinístico).
    """
    task = _make_extract_task(seeded_vault)
    audio_id_1 = extract_audio_handler(task, seeded_vault["conn"])
    audio_id_2 = extract_audio_handler(task, seeded_vault["conn"])
    assert audio_id_1 == audio_id_2  # sha256 determinístico → mesmo file_id

    files_count = seeded_vault["conn"].execute(
        "SELECT COUNT(*) FROM files WHERE file_id = ?", (audio_id_1,),
    ).fetchone()[0]
    assert files_count == 1

    deriv_count = seeded_vault["conn"].execute(
        "SELECT COUNT(*) FROM media_derivations WHERE derived_file_id = ?",
        (audio_id_1,),
    ).fetchone()[0]
    assert deriv_count == 1


# ---------------------------------------------------------------------------
# A1, A2 — adicionados sobre o plano original
# ---------------------------------------------------------------------------


@requires_ffmpeg
def test_extract_audio_handler_transcribe_task_has_no_depends(seeded_vault: dict) -> None:
    """
    A task TRANSCRIBE enfileirada pelo handler deve ter depends_on == [].
    Comentário no handler explicita: a task EXTRACT_AUDIO já está RUNNING
    quando este código executa, então depender dela seria redundante.
    """
    import json

    task = _make_extract_task(seeded_vault)
    extract_audio_handler(task, seeded_vault["conn"])

    rows = seeded_vault["conn"].execute(
        "SELECT depends_on, payload, status FROM tasks WHERE task_type = ? AND obra = ?",
        (TaskType.TRANSCRIBE.value, seeded_vault["obra"]),
    ).fetchall()
    assert len(rows) == 1
    transcribe = rows[0]
    assert json.loads(transcribe["depends_on"]) == []
    assert transcribe["status"] == TaskStatus.PENDING.value
    payload = json.loads(transcribe["payload"])
    assert payload["file_id"].startswith("f_")
    assert payload["file_path"].endswith(".audio.wav")


@requires_ffmpeg
def test_extract_audio_handler_logs_ffmpeg_warnings(
    seeded_vault: dict,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Mesmo em sucesso (rc=0), warnings do ffmpeg devem ir para log.warning —
    são informação probatória (codec inesperado, áudio truncado, etc.).
    """
    real_run = subprocess.run

    class _FakeResult:
        def __init__(self, rc: int, stderr: bytes) -> None:
            self.returncode = rc
            self.stderr = stderr
            self.stdout = b""

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        # Só intercepta a chamada do extractor (ffmpeg + -y + -loglevel + -i ...).
        # Outras chamadas (ex: ffprobe interno do pytest, etc.) vão para o real.
        if isinstance(cmd, list) and cmd and cmd[0] == "ffmpeg" and "-vn" in cmd:
            output_path = Path(cmd[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # WAV mínimo válido: header RIFF/WAVE + fmt + data vazio.
            with wave.open(str(output_path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"")
            return _FakeResult(0, b"warning: stream 0:1 type unknown, ignored\n")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    task = _make_extract_task(seeded_vault)
    with caplog.at_level(logging.WARNING, logger="rdo_agent.extractor"):
        audio_file_id = extract_audio_handler(task, seeded_vault["conn"])

    assert audio_file_id is not None
    warning_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "ffmpeg" in r.getMessage()
    ]
    assert len(warning_records) >= 1
    assert "stream 0:1 type unknown" in warning_records[0].getMessage()
