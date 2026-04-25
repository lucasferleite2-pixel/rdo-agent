"""Testes do video module — Sessao 9, divida #48.

Promove scripts/extract_video_frames.py para src/rdo_agent/video/
com integracao em StructuredLogger e drainer process_videos_pending.

Usa ffmpeg lavfi para gerar videos sinteticos sem dependencia
externa.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from rdo_agent.orchestrator import (
    TaskStatus, TaskType, init_db,
)
from rdo_agent.video import (
    DERIVATION_METHOD_PREFIX,
    FRAME_PERCENTS,
    FfmpegMissingError,
    compute_timestamps,
    extract_frames_for_video,
    probe_duration,
    process_videos_pending,
)


SKIP_FFMPEG = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe não instalado",
)


def _make_synthetic_video(out_path: Path, duration: int = 5) -> Path:
    """Gera video sintetico (testsrc) via ffmpeg lavfi."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc=duration={duration}:size=320x240:rate=10",
         "-pix_fmt", "yuv420p", str(out_path)],
        check=True, capture_output=True,
    )
    return out_path


# ---------------------------------------------------------------------------
# compute_timestamps (puro)
# ---------------------------------------------------------------------------


def test_compute_timestamps_returns_5_values():
    ts = compute_timestamps(100.0)
    assert len(ts) == 5
    assert len(FRAME_PERCENTS) == 5


def test_compute_timestamps_clamps_borders():
    """Bordas tem clamp ±0.5s para evitar frames pretos."""
    ts = compute_timestamps(100.0)
    assert ts[0] >= 0.5
    assert ts[-1] <= 99.5


def test_compute_timestamps_short_video_clamps_correctly():
    """Video de 1s — todos timestamps devem estar em [0.5, 0.5]."""
    ts = compute_timestamps(1.0)
    for t in ts:
        assert 0.4 <= t <= 0.6  # tolerancia float


def test_compute_timestamps_monotonic_for_normal_duration():
    """Timestamps em ordem crescente para vídeos normais."""
    ts = compute_timestamps(60.0)
    assert ts == sorted(ts)


# ---------------------------------------------------------------------------
# probe_duration / extract_frame (precisa ffmpeg)
# ---------------------------------------------------------------------------


@SKIP_FFMPEG
def test_probe_duration_synthetic_video(tmp_path):
    video = _make_synthetic_video(tmp_path / "test.mp4", duration=5)
    duration = probe_duration(video)
    assert 4.5 <= duration <= 5.5


@SKIP_FFMPEG
def test_extract_frame_creates_jpeg(tmp_path):
    from rdo_agent.video import extract_frame
    video = _make_synthetic_video(tmp_path / "test.mp4", duration=5)
    out = tmp_path / "frame.jpg"
    extract_frame(video, 2.5, out)
    assert out.exists()
    # JPEG magic bytes
    assert out.read_bytes()[:2] == b"\xff\xd8"


@SKIP_FFMPEG
def test_probe_duration_missing_file_raises(tmp_path):
    with pytest.raises(subprocess.CalledProcessError):
        probe_duration(tmp_path / "nonexistent.mp4")


# ---------------------------------------------------------------------------
# extract_frames_for_video (integração SQLite + ffmpeg)
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Vault efêmero com files table pronta."""
    from rdo_agent.utils import config
    settings = config.Settings(
        openai_api_key="x", anthropic_api_key="",
        claude_model="x", vaults_root=tmp_path,
        log_level="WARNING", dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)

    obra = "OBRA_VIDEO"
    vault_path = tmp_path / obra
    vault_path.mkdir(parents=True)
    conn = init_db(vault_path)
    return conn, obra, vault_path


@SKIP_FFMPEG
def test_extract_frames_for_video_creates_5_frames(vault):
    conn, obra, vault_path = vault
    media_dir = vault_path / "10_media"
    media_dir.mkdir(parents=True)

    video_file = media_dir / "v_001.mp4"
    _make_synthetic_video(video_file, duration=5)

    # Insere video em files
    conn.execute(
        "INSERT INTO files (file_id, obra, file_path, file_type, sha256, "
        "size_bytes, semantic_status, created_at) "
        "VALUES (?, ?, ?, 'video', ?, ?, 'awaiting_audio_extraction', "
        "'2026-04-25T00:00:00Z')",
        ("f_video_test", obra, "10_media/v_001.mp4",
         "v" * 64, video_file.stat().st_size),
    )
    conn.commit()

    result = extract_frames_for_video(conn, obra, "f_video_test")
    assert result["frames_created"] == 5
    assert result["frames_skipped_existing"] == 0
    assert result["tasks_enqueued"] == 5
    assert 4.5 <= result["duration_sec"] <= 5.5


@SKIP_FFMPEG
def test_extract_frames_idempotent(vault):
    """Re-rodar não duplica frames (file_id deterministico via sha256)."""
    conn, obra, vault_path = vault
    media_dir = vault_path / "10_media"
    media_dir.mkdir(parents=True)
    video_file = media_dir / "v_002.mp4"
    _make_synthetic_video(video_file, duration=5)

    conn.execute(
        "INSERT INTO files (file_id, obra, file_path, file_type, sha256, "
        "size_bytes, semantic_status, created_at) "
        "VALUES (?, ?, ?, 'video', ?, ?, 'x', '2026-04-25T00:00:00Z')",
        ("f_video_idem", obra, "10_media/v_002.mp4",
         "i" * 64, video_file.stat().st_size),
    )
    conn.commit()

    r1 = extract_frames_for_video(conn, obra, "f_video_idem")
    r2 = extract_frames_for_video(conn, obra, "f_video_idem")

    assert r1["frames_created"] == 5
    assert r2["frames_created"] == 0
    assert r2["frames_skipped_existing"] == 5


@SKIP_FFMPEG
def test_extract_frames_creates_visual_analysis_tasks(vault):
    """Cada frame gera 1 task VISUAL_ANALYSIS."""
    conn, obra, vault_path = vault
    media_dir = vault_path / "10_media"
    media_dir.mkdir(parents=True)
    video_file = media_dir / "v_003.mp4"
    _make_synthetic_video(video_file, duration=5)

    conn.execute(
        "INSERT INTO files (file_id, obra, file_path, file_type, sha256, "
        "size_bytes, semantic_status, created_at) "
        "VALUES (?, ?, ?, 'video', ?, ?, 'x', '2026-04-25T00:00:00Z')",
        ("f_video_tasks", obra, "10_media/v_003.mp4",
         "t" * 64, video_file.stat().st_size),
    )
    conn.commit()

    extract_frames_for_video(conn, obra, "f_video_tasks")

    # 5 tasks VISUAL_ANALYSIS pending
    n = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE obra = ? "
        "AND task_type = ? AND status = ?",
        (obra, TaskType.VISUAL_ANALYSIS.value, TaskStatus.PENDING.value),
    ).fetchone()[0]
    assert n == 5


@SKIP_FFMPEG
def test_extract_frames_populates_media_derivations(vault):
    conn, obra, vault_path = vault
    media_dir = vault_path / "10_media"
    media_dir.mkdir(parents=True)
    video_file = media_dir / "v_004.mp4"
    _make_synthetic_video(video_file, duration=5)

    conn.execute(
        "INSERT INTO files (file_id, obra, file_path, file_type, sha256, "
        "size_bytes, semantic_status, created_at) "
        "VALUES (?, ?, ?, 'video', ?, ?, 'x', '2026-04-25T00:00:00Z')",
        ("f_video_md", obra, "10_media/v_004.mp4",
         "m" * 64, video_file.stat().st_size),
    )
    conn.commit()

    extract_frames_for_video(conn, obra, "f_video_md")

    rows = conn.execute(
        "SELECT derivation_method FROM media_derivations "
        "WHERE source_file_id = ?",
        ("f_video_md",),
    ).fetchall()
    assert len(rows) == 5
    methods = {r[0] for r in rows}
    # Os 5 prefixos esperados (5%, 25%, 50%, 75%, 95%)
    assert methods == {
        f"{DERIVATION_METHOD_PREFIX}{int(p*100):03d}"
        for p in FRAME_PERCENTS
    }


def test_extract_frames_unknown_video_raises(vault):
    conn, obra, _ = vault
    with pytest.raises(RuntimeError, match="nao encontrado"):
        extract_frames_for_video(conn, obra, "f_inexistente")


# ---------------------------------------------------------------------------
# process_videos_pending (drain wrapper)
# ---------------------------------------------------------------------------


@SKIP_FFMPEG
def test_process_videos_pending_processes_all_unprocessed(vault):
    conn, obra, vault_path = vault
    media_dir = vault_path / "10_media"
    media_dir.mkdir(parents=True)

    # 2 videos sem frames extraidos
    for i in range(1, 3):
        video_file = media_dir / f"vp_{i}.mp4"
        _make_synthetic_video(video_file, duration=4)
        conn.execute(
            "INSERT INTO files (file_id, obra, file_path, file_type, sha256, "
            "size_bytes, semantic_status, created_at) "
            "VALUES (?, ?, ?, 'video', ?, ?, 'x', '2026-04-25T00:00:00Z')",
            (f"f_vp_{i}", obra, f"10_media/vp_{i}.mp4",
             chr(96 + i) * 64, video_file.stat().st_size),
        )
    conn.commit()

    counts = process_videos_pending(conn, obra)
    assert counts == {"processed": 2, "failed": 0}


@SKIP_FFMPEG
def test_process_videos_pending_skips_already_processed(vault):
    """Re-execução não reprocessa vídeos com frames já extraídos."""
    conn, obra, vault_path = vault
    media_dir = vault_path / "10_media"
    media_dir.mkdir(parents=True)
    video_file = media_dir / "v_x.mp4"
    _make_synthetic_video(video_file, duration=4)
    conn.execute(
        "INSERT INTO files (file_id, obra, file_path, file_type, sha256, "
        "size_bytes, semantic_status, created_at) "
        "VALUES (?, ?, ?, 'video', ?, ?, 'x', '2026-04-25T00:00:00Z')",
        ("f_vx", obra, "10_media/v_x.mp4",
         "x" * 64, video_file.stat().st_size),
    )
    conn.commit()

    c1 = process_videos_pending(conn, obra)
    c2 = process_videos_pending(conn, obra)
    assert c1 == {"processed": 1, "failed": 0}
    # Segunda chamada não detecta nada pendente
    assert c2 == {"processed": 0, "failed": 0}


@SKIP_FFMPEG
def test_process_videos_pending_max_videos_caps(vault):
    conn, obra, vault_path = vault
    media_dir = vault_path / "10_media"
    media_dir.mkdir(parents=True)
    for i in range(1, 4):
        f = media_dir / f"vc_{i}.mp4"
        _make_synthetic_video(f, duration=4)
        conn.execute(
            "INSERT INTO files (file_id, obra, file_path, file_type, sha256, "
            "size_bytes, semantic_status, created_at) "
            "VALUES (?, ?, ?, 'video', ?, ?, 'x', '2026-04-25T00:00:00Z')",
            (f"f_vc_{i}", obra, f"10_media/vc_{i}.mp4",
             f"c{i}".ljust(64, "0"), f.stat().st_size),
        )
    conn.commit()

    counts = process_videos_pending(conn, obra, max_videos=2)
    assert counts["processed"] == 2
