"""Testes do pre-flight check — Sessao 7, divida #55."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from rdo_agent.preflight import (
    DEFAULT_RATES,
    CostBreakdown,
    PreflightReport,
    TimeBreakdown,
    preflight_check,
)
from rdo_agent.preflight.estimator import format_report_lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_zip(
    path: Path, *,
    chat_txt_lines: list[str] | None = None,
    audio_count: int = 0,
    image_count: int = 0,
    video_count: int = 0,
    pdf_count: int = 0,
    audio_size_bytes: int = 16 * 1024,
    image_size_bytes: int = 64 * 1024,
) -> Path:
    """Gera ZIP sintetico com counts especificos."""
    with zipfile.ZipFile(path, "w") as zf:
        if chat_txt_lines is not None:
            zf.writestr("_chat.txt", "\n".join(chat_txt_lines))
        for i in range(audio_count):
            zf.writestr(f"audio_{i:03d}.opus", b"\x00" * audio_size_bytes)
        for i in range(image_count):
            zf.writestr(f"image_{i:03d}.jpg", b"\x00" * image_size_bytes)
        for i in range(video_count):
            zf.writestr(f"video_{i:03d}.mp4", b"\x00" * (audio_size_bytes * 4))
        for i in range(pdf_count):
            zf.writestr(f"doc_{i:03d}.pdf", b"\x00" * (image_size_bytes // 2))
    return path


# ---------------------------------------------------------------------------
# Testes unidade
# ---------------------------------------------------------------------------


def test_preflight_classifies_zip_members(tmp_path):
    zp = _make_zip(
        tmp_path / "x.zip",
        chat_txt_lines=["08/04/2026 09:00 - User: msg"],
        audio_count=12,
        image_count=5,
        video_count=2,
        pdf_count=1,
    )
    r = preflight_check(zp, vault_root=tmp_path)
    assert r.audio_count == 12
    assert r.image_count == 5
    assert r.video_count == 2
    assert r.pdf_count == 1
    assert r.chat_txt_found is True


def test_preflight_estimates_messages_from_sample(tmp_path):
    """Estimativa de msg count escala proporcional ao tamanho do chat.txt."""
    lines_short = [f"08/04/2026 09:{i % 60:02d} - User: msg {i}" for i in range(10)]
    lines_long = [f"08/04/2026 09:{i % 60:02d} - User: msg {i}" for i in range(500)]

    zp_short = _make_zip(tmp_path / "short.zip", chat_txt_lines=lines_short)
    zp_long = _make_zip(tmp_path / "long.zip", chat_txt_lines=lines_long)

    r_short = preflight_check(zp_short, vault_root=tmp_path)
    r_long = preflight_check(zp_long, vault_root=tmp_path)

    # Estimativa do longo deve ser ~50x maior (10 vs 500 lines)
    assert r_long.message_count_est > r_short.message_count_est * 30
    assert r_short.message_count_est > 0


def test_preflight_disk_check_warns_when_low(tmp_path, monkeypatch):
    """Disco baixo gera warning."""
    # ZIP com áudios pesados pra garantir disk_required > free mockado
    zp = _make_zip(
        tmp_path / "x.zip",
        chat_txt_lines=["08/04/2026 09:00 - U: m"],
        audio_count=10,
        audio_size_bytes=100 * 1024,  # 100KB cada
    )

    import shutil

    # Mock free=0 garante que qualquer disk_required > 0 falha
    fake_usage = type("Usage", (), {"total": 0, "used": 0, "free": 0})()
    monkeypatch.setattr(shutil, "disk_usage", lambda *_: fake_usage)

    r = preflight_check(zp, vault_root=tmp_path)
    assert not r.disk_ok
    assert any("disco insuficiente" in w for w in r.warnings)


def test_preflight_disk_check_passes_when_high(tmp_path, monkeypatch):
    zp = _make_zip(tmp_path / "x.zip", chat_txt_lines=["08/04/2026 09:00 - U: m"])

    import shutil
    fake_usage = type("Usage", (), {"total": 0, "used": 0, "free": 100 * 10**9})()
    monkeypatch.setattr(shutil, "disk_usage", lambda *_: fake_usage)

    r = preflight_check(zp, vault_root=tmp_path)
    assert r.disk_ok
    assert not any("disco insuficiente" in w for w in r.warnings)


def test_preflight_cost_estimate_scales_with_counts(tmp_path):
    """Mais imagens e mensagens = mais custo."""
    zp_small = _make_zip(
        tmp_path / "small.zip",
        chat_txt_lines=[f"08/04/2026 09:{i:02d} - U: msg" for i in range(10)],
        image_count=2,
    )
    zp_big = _make_zip(
        tmp_path / "big.zip",
        chat_txt_lines=[
            f"08/04/2026 09:{i % 60:02d} - U: msg {i}"
            for i in range(2000)
        ],
        image_count=100,
    )
    r_small = preflight_check(zp_small, vault_root=tmp_path)
    r_big = preflight_check(zp_big, vault_root=tmp_path)
    assert r_big.cost.total_usd > r_small.cost.total_usd


def test_preflight_cost_breakdown_components(tmp_path):
    """Cost breakdown tem 4 componentes; total = soma; bounds com margem."""
    zp = _make_zip(
        tmp_path / "x.zip",
        chat_txt_lines=[
            f"08/04/2026 09:{i % 60:02d} - U: msg {i}"
            for i in range(1000)
        ],
        image_count=20,
    )
    r = preflight_check(zp, vault_root=tmp_path)
    cb = r.cost
    assert isinstance(cb, CostBreakdown)
    assert cb.transcribe_usd == 0.0  # Whisper local
    assert cb.classify_usd > 0
    assert cb.vision_usd > 0
    assert cb.narrator_usd > 0
    assert cb.total_usd == pytest.approx(
        cb.transcribe_usd + cb.classify_usd + cb.vision_usd + cb.narrator_usd
    )
    # Bounds com margem 50%
    assert cb.lower_bound_usd < cb.total_usd < cb.upper_bound_usd


def test_preflight_time_estimate_scales_with_audios(tmp_path):
    """Mais audios -> mais tempo de transcribe."""
    zp1 = _make_zip(
        tmp_path / "few.zip",
        chat_txt_lines=["08/04/2026 09:00 - U: msg"],
        audio_count=10,
    )
    zp2 = _make_zip(
        tmp_path / "many.zip",
        chat_txt_lines=["08/04/2026 09:00 - U: msg"],
        audio_count=200,
    )
    r1 = preflight_check(zp1, vault_root=tmp_path)
    r2 = preflight_check(zp2, vault_root=tmp_path)
    assert isinstance(r2.time, TimeBreakdown)
    assert r2.time.transcribe_sec > r1.time.transcribe_sec * 10


def test_preflight_warns_when_no_chat_txt(tmp_path):
    zp = _make_zip(tmp_path / "x.zip", chat_txt_lines=None, audio_count=2)
    r = preflight_check(zp, vault_root=tmp_path)
    assert r.chat_txt_found is False
    assert any("chat.txt" in w for w in r.warnings)
    assert r.message_count_est == 0


def test_preflight_warns_when_high_cost(tmp_path):
    """Custo > $50 dispara warning explicito."""
    # Override rates para garantir custo > $50 com input pequeno
    from rdo_agent.preflight.estimator import Rates
    custom = Rates(
        narrator_usd_per_day=10.0,  # alto
        classify_usd_per_event=DEFAULT_RATES.classify_usd_per_event,
        vision_usd_per_image=DEFAULT_RATES.vision_usd_per_image,
    )
    zp = _make_zip(
        tmp_path / "x.zip",
        chat_txt_lines=[
            f"08/04/2026 09:{i % 60:02d} - U: msg {i}"
            for i in range(2000)
        ],
    )
    r = preflight_check(zp, vault_root=tmp_path, rates=custom)
    assert r.cost.total_usd > 50
    assert any("custo estimado alto" in w for w in r.warnings)


def test_preflight_raises_when_zip_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        preflight_check(tmp_path / "noexist.zip")


def test_preflight_format_report_lines_human_readable(tmp_path):
    zp = _make_zip(
        tmp_path / "x.zip",
        chat_txt_lines=["08/04/2026 09:00 - User: ola"],
        audio_count=1, image_count=1,
    )
    r = preflight_check(zp, vault_root=tmp_path)
    lines = list(format_report_lines(r))
    text = "\n".join(lines)
    assert "Pre-flight check" in text
    assert "CORPUS:" in text
    assert "RECURSOS:" in text
    assert "CUSTOS ESTIMADOS" in text
    assert "TEMPO ESTIMADO" in text


def test_preflight_env_overrides_rates(tmp_path, monkeypatch):
    """Env vars sobrescrevem rates default."""
    monkeypatch.setenv("RDO_AGENT_PREFLIGHT_VISION_USD_PER_IMAGE", "100.0")
    zp = _make_zip(
        tmp_path / "x.zip",
        chat_txt_lines=["08/04/2026 09:00 - U: m"],
        image_count=2,
    )
    r = preflight_check(zp, vault_root=tmp_path)
    # 2 images × $100 = $200 (vs default ~$0.01)
    assert r.cost.vision_usd == pytest.approx(200.0)
