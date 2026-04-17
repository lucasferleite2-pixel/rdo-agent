"""Testes do resolvedor temporal — filename, EXIF, mtime, timezone, conflitos."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from rdo_agent.temporal import (
    BRT,
    TemporalConfidence,
    TemporalSource,
    extract_metadata_timestamp,
    parse_from_filename,
    resolve_temporal,
)

# Detecta se libmediainfo nativa está disponível para o teste de vídeo.
try:
    from pymediainfo import MediaInfo

    _LIBMEDIAINFO_OK = MediaInfo.can_parse()
except Exception:
    _LIBMEDIAINFO_OK = False

_FFMPEG_OK = shutil.which("ffmpeg") is not None


def _touch_with_mtime(path: Path, when: datetime) -> Path:
    """Cria arquivo com mtime cravado no datetime fornecido (assume BRT se naive)."""
    path.write_bytes(b"x")
    _set_mtime(path, when)
    return path


def _set_mtime(path: Path, when: datetime) -> None:
    """
    Alinha mtime de um arquivo já existente. Sem isso, tmp_path deixa mtime=agora
    e introduz drift espúrio nas comparações (now × 12/03/2026 ≈ 800h).
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=BRT)
    epoch = when.timestamp()
    os.utime(path, (epoch, epoch))


# ---------------------------------------------------------------------------
# parse_from_filename
# ---------------------------------------------------------------------------


def test_parse_filename_whatsapp_image() -> None:
    assert parse_from_filename("IMG-20260312-WA0015.jpg") == datetime(2026, 3, 12)


def test_parse_filename_whatsapp_video() -> None:
    assert parse_from_filename("VID-20260312-WA0007.mp4") == datetime(2026, 3, 12)


def test_parse_filename_whatsapp_ptt() -> None:
    assert parse_from_filename("PTT-20260312-WA0001.opus") == datetime(2026, 3, 12)


def test_parse_filename_whatsapp_aud() -> None:
    assert parse_from_filename("AUD-20260312-WA0001.m4a") == datetime(2026, 3, 12)


def test_parse_filename_native_camera() -> None:
    assert parse_from_filename("IMG_20260312_094532.jpg") == datetime(2026, 3, 12, 9, 45, 32)


def test_parse_filename_unknown_returns_none() -> None:
    assert parse_from_filename("foto.jpg") is None
    assert parse_from_filename("documento.pdf") is None
    assert parse_from_filename("WhatsApp Video 2026-03-12.mp4") is None


def test_parse_filename_invalid_date_returns_none() -> None:
    """Mês 13 não existe — retorna None, não levanta."""
    assert parse_from_filename("IMG-20261332-WA0001.jpg") is None


# ---------------------------------------------------------------------------
# extract_metadata_timestamp
# ---------------------------------------------------------------------------


def test_extract_metadata_jpeg_with_exif(
    make_jpeg_with_exif: Callable[[str, datetime | None], Path],
) -> None:
    target = datetime(2026, 3, 12, 9, 45, 32)
    path = make_jpeg_with_exif("IMG-20260312-WA0015.jpg", target)
    assert extract_metadata_timestamp(path) == target


def test_extract_metadata_jpeg_without_exif(
    make_jpeg_with_exif: Callable[[str, datetime | None], Path],
) -> None:
    path = make_jpeg_with_exif("IMG-20260312-WA0015.jpg", None)
    assert extract_metadata_timestamp(path) is None


def test_extract_metadata_unknown_extension(tmp_path: Path) -> None:
    p = tmp_path / "notas.txt"
    p.write_text("foo")
    assert extract_metadata_timestamp(p) is None


def test_extract_metadata_nonexistent_file_returns_none(tmp_path: Path) -> None:
    assert extract_metadata_timestamp(tmp_path / "nao_existe.jpg") is None


@pytest.mark.skipif(
    not (_LIBMEDIAINFO_OK and _FFMPEG_OK),
    reason="libmediainfo ou ffmpeg ausente",
)
def test_extract_metadata_video_via_pymediainfo(tmp_path: Path) -> None:
    out = tmp_path / "VID-20260312-WA0007.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
            "-metadata", "creation_time=2026-03-12T09:45:32Z",
            str(out),
        ],
        check=True,
    )
    ts = extract_metadata_timestamp(out)
    assert ts is not None
    assert (ts.year, ts.month, ts.day) == (2026, 3, 12)


# ---------------------------------------------------------------------------
# resolve_temporal — escolha por hierarquia
# ---------------------------------------------------------------------------


def test_resolves_using_whatsapp_when_available(
    make_jpeg_with_exif: Callable[[str, datetime | None], Path],
) -> None:
    wa_ts = datetime(2026, 3, 12, 9, 45, 32)
    path = make_jpeg_with_exif("IMG-20260312-WA0015.jpg", wa_ts)
    _set_mtime(path, wa_ts)
    res = resolve_temporal(path, whatsapp_timestamp=wa_ts)
    assert res.source_used == TemporalSource.WHATSAPP_TXT
    assert res.confidence == TemporalConfidence.MAX
    assert res.timestamp_resolved == wa_ts.replace(tzinfo=BRT)
    assert res.conflict_detected is False


def test_resolves_to_filename_when_no_whatsapp(
    make_jpeg_with_exif: Callable[[str, datetime | None], Path],
) -> None:
    path = make_jpeg_with_exif("IMG-20260312-WA0015.jpg", None)
    _set_mtime(path, datetime(2026, 3, 12, 9, 0, 0))
    res = resolve_temporal(path, whatsapp_timestamp=None)
    assert res.source_used == TemporalSource.FILENAME
    assert res.confidence == TemporalConfidence.HIGH
    assert res.conflict_detected is False


def test_resolves_to_metadata_when_no_filename_pattern(
    make_jpeg_with_exif: Callable[[str, datetime | None], Path],
) -> None:
    target = datetime(2026, 3, 12, 9, 45, 32)
    # Nome sem padrão WA/nativo → cai para EXIF
    path = make_jpeg_with_exif("foto.jpg", target)
    _set_mtime(path, target)
    res = resolve_temporal(path, whatsapp_timestamp=None)
    assert res.source_used == TemporalSource.METADATA
    assert res.confidence == TemporalConfidence.MEDIUM
    assert res.conflict_detected is False


def test_resolves_to_filesystem_as_last_resort(tmp_path: Path) -> None:
    target = datetime(2026, 3, 12, 9, 45, 32, tzinfo=BRT)
    path = _touch_with_mtime(tmp_path / "qualquer.bin", target)
    res = resolve_temporal(path, whatsapp_timestamp=None)
    assert res.source_used == TemporalSource.FILESYSTEM
    assert res.confidence == TemporalConfidence.LOW
    assert res.timestamp_resolved == target


def test_resolves_raises_when_no_source_available(tmp_path: Path) -> None:
    """Arquivo inexistente, sem WA, sem padrão de nome reconhecível."""
    fake = tmp_path / "nao_existe.bin"
    with pytest.raises(ValueError, match="nenhuma fonte temporal"):
        resolve_temporal(fake, whatsapp_timestamp=None)


# ---------------------------------------------------------------------------
# Conflitos
# ---------------------------------------------------------------------------


def test_resolve_detects_conflict_above_threshold(
    make_jpeg_with_exif: Callable[[str, datetime | None], Path],
) -> None:
    """WA = 09:00, EXIF = 14:00 (5h drift) → conflict + cap MEDIUM."""
    wa_ts = datetime(2026, 3, 12, 9, 0, 0)
    exif_ts = datetime(2026, 3, 12, 14, 0, 0)
    path = make_jpeg_with_exif("IMG-20260312-WA0015.jpg", exif_ts)
    _set_mtime(path, wa_ts)  # alinha mtime com WA para isolar o conflito ao par WA×EXIF
    res = resolve_temporal(path, whatsapp_timestamp=wa_ts)
    assert res.source_used == TemporalSource.WHATSAPP_TXT
    assert res.conflict_detected is True
    assert res.confidence == TemporalConfidence.MEDIUM
    assert any(
        "whatsapp_txt" in n and "metadata" in n and "drift" in n
        for n in res.notes
    )


def test_resolve_no_conflict_below_threshold(
    make_jpeg_with_exif: Callable[[str, datetime | None], Path],
) -> None:
    wa_ts = datetime(2026, 3, 12, 9, 0, 0)
    exif_ts = datetime(2026, 3, 12, 9, 30, 0)  # 30min drift
    path = make_jpeg_with_exif("IMG-20260312-WA0015.jpg", exif_ts)
    _set_mtime(path, wa_ts)
    res = resolve_temporal(path, whatsapp_timestamp=wa_ts)
    assert res.conflict_detected is False
    assert res.confidence == TemporalConfidence.MAX
    assert res.notes == []


def test_drift_ignores_time_when_filename_source_lacks_time_info(
    make_jpeg_with_exif: Callable[[str, datetime | None], Path],
) -> None:
    """
    Heurística do _drift_seconds: WA-filename codifica só data (00:00:00 é
    placeholder, não meia-noite). WA timestamp à tarde (14:30) NÃO deve
    disparar conflito contra WA-filename do mesmo dia, apesar dos 14.5h
    de diferença nominal.

    Sem essa heurística, todo arquivo de mídia enviado depois das ~02:00
    do dia geraria conflito espúrio.
    """
    wa_ts = datetime(2026, 3, 12, 14, 30, 0)
    path = make_jpeg_with_exif("IMG-20260312-WA0015.jpg", None)  # sem EXIF
    _set_mtime(path, wa_ts)  # mtime alinhado para isolar o par WA × filename
    res = resolve_temporal(path, whatsapp_timestamp=wa_ts)

    # Filename está disponível e codifica 12/03 00:00; WA é 12/03 14:30
    fn_ts = res.all_sources[TemporalSource.FILENAME]
    assert fn_ts is not None
    assert fn_ts.time().hour == 0
    # Mesmo com 14.5h de drift nominal, mesma data → sem conflito
    assert res.conflict_detected is False
    assert res.confidence == TemporalConfidence.MAX
    assert res.notes == []


def test_conflict_between_non_chosen_sources(
    tmp_path: Path,
    make_jpeg_with_exif: Callable[[str, datetime | None], Path],
) -> None:
    """
    A2: WA é a escolhida (MAX). filename e mtime ambos a <2h da WA, mas
    divergem >2h ENTRE SI. Deve disparar conflict=True e cap MEDIUM mesmo
    que o par envolvendo WA não conflite.

    Geometria: WA=12:00, filename=10:30 (1.5h da WA), mtime=13:30 (1.5h da WA)
    → filename × mtime = 3h drift.
    """
    wa_ts = datetime(2026, 3, 12, 12, 0, 0)
    # Filename nativo (precisão de segundo) — necessário para drift sub-diário
    path = tmp_path / "IMG_20260312_103000.jpg"
    img_payload = make_jpeg_with_exif("tmp_no_exif.jpg", None).read_bytes()
    path.write_bytes(img_payload)

    mtime_target = datetime(2026, 3, 12, 13, 30, 0, tzinfo=BRT)
    os.utime(path, (mtime_target.timestamp(), mtime_target.timestamp()))

    res = resolve_temporal(path, whatsapp_timestamp=wa_ts)
    assert res.source_used == TemporalSource.WHATSAPP_TXT
    assert res.conflict_detected is True
    assert res.confidence == TemporalConfidence.MEDIUM
    # A nota deve identificar o par filename × filesystem
    assert any(
        "filename" in n and "filesystem" in n and "drift" in n
        for n in res.notes
    )


def test_notes_contain_each_conflicting_pair_separately(
    make_jpeg_with_exif: Callable[[str, datetime | None], Path],
) -> None:
    """
    A1: cada par conflitante registrado em entrada própria de notes —
    não aggregado em mensagem única.

    Cenário: WA=09:00, mtime=09:00 (alinhados), EXIF=18:00 (9h fora).
    EXIF conflita com os DOIS outros → dois pares distintos esperados
    em notes, cada um nomeando explicitamente as duas fontes envolvidas.
    """
    wa_ts = datetime(2026, 3, 12, 9, 0, 0)
    exif_ts = datetime(2026, 3, 12, 18, 0, 0)
    path = make_jpeg_with_exif("IMG-20260312-WA0015.jpg", exif_ts)
    _set_mtime(path, wa_ts)
    res = resolve_temporal(path, whatsapp_timestamp=wa_ts)

    assert len(res.notes) == 2
    assert any(
        "whatsapp_txt" in n and "metadata" in n and "drift" in n for n in res.notes
    )
    assert any(
        "metadata" in n and "filesystem" in n and "drift" in n for n in res.notes
    )
    # Formato de cada nota: "<src_a> vs <src_b>: Nh drift"
    for n in res.notes:
        assert "vs" in n and "drift" in n


# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------


def test_timestamp_resolved_is_always_aware_brt(
    make_jpeg_with_exif: Callable[[str, datetime | None], Path],
) -> None:
    wa_ts = datetime(2026, 3, 12, 9, 0, 0)  # naive
    path = make_jpeg_with_exif("IMG-20260312-WA0015.jpg", None)
    _set_mtime(path, wa_ts)
    res = resolve_temporal(path, whatsapp_timestamp=wa_ts)
    assert res.timestamp_resolved.tzinfo is not None
    assert res.timestamp_resolved.utcoffset() == wa_ts.replace(tzinfo=BRT).utcoffset()
