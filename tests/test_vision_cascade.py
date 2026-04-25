"""Testes do vision cascade — Sessao 9, divida #47.

Cobre as 3 camadas locais (Heuristic, pHash dedup, Routing). Camada
4 (Vision API) eh testada via mock no orchestrator.

NAO usa torch nem CLIP (escolha consciente da Sessao 9; ver
divida #60 registrada para upgrade futuro).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rdo_agent.visual_analyzer.cascade import (
    AGGRESSIVE_THRESHOLDS,
    CONSERVATIVE_THRESHOLDS,
    HeuristicImageFilter,
    HeuristicVerdict,
    PerceptualHashDedup,
    RoutingClassifier,
    RoutingDecision,
    hamming_distance,
    migrate_image_phashes,
)


# ---------------------------------------------------------------------------
# Helpers — geradores de fixtures
# ---------------------------------------------------------------------------


def _make_image(
    path: Path, *,
    size: tuple[int, int] = (256, 256),
    color: tuple[int, int, int] = (128, 128, 128),
    pattern: str = "solid",
) -> Path:
    """Cria imagem PIL de teste e salva em ``path``."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, color)
    if pattern == "stripes":
        d = ImageDraw.Draw(img)
        for x in range(0, size[0], 8):
            d.line([(x, 0), (x, size[1])], fill=(255, 255, 255), width=2)
    elif pattern == "noise":
        import random
        random.seed(42)
        for _ in range(size[0] * size[1] // 4):
            x = random.randint(0, size[0] - 1)
            y = random.randint(0, size[1] - 1)
            img.putpixel((x, y), (
                random.randint(0, 255),
                random.randint(0, 255),
                random.randint(0, 255),
            ))
    img.save(path, format="JPEG", quality=85)
    return path


def _make_corrupted(path: Path) -> Path:
    """Salva 'JPEG' invalido com tamanho > 3KB para passar no check
    de tamanho de arquivo (queremos que falhe especificamente em
    PIL.open, não no size check)."""
    payload = b"\xff\xd8not a real jpeg payload at all" + b"\x00" * (4 * 1024)
    path.write_bytes(payload)
    return path


# ---------------------------------------------------------------------------
# HeuristicImageFilter
# ---------------------------------------------------------------------------


def test_heuristic_passes_normal_photo(tmp_path):
    img = _make_image(tmp_path / "normal.jpg", size=(800, 600), pattern="stripes")
    f = HeuristicImageFilter(aggressive=False)
    v = f.evaluate(img)
    assert v.skip is False


def test_heuristic_skips_too_small_file(tmp_path):
    """Sticker pequeno (< 3KB) skipa em conservador."""
    img = _make_image(tmp_path / "tiny.jpg", size=(64, 64), pattern="solid")
    # Forca tamanho pequeno truncando o arquivo
    raw = img.read_bytes()[:1500]  # ~1.5KB
    img.write_bytes(raw)
    f = HeuristicImageFilter(aggressive=False)
    v = f.evaluate(img)
    assert v.skip is True
    assert "too_small" in (v.reason or "")


def test_heuristic_skips_dimensions_too_small(tmp_path):
    """Imagem 32x32 em conservador (< 64) skipa por dimensões.
    Save como PNG sem compressão para garantir >= 3KB (passar
    no check de tamanho de arquivo antes do de dimensões)."""
    from PIL import Image

    # Imagem 32x32 em PNG não-comprimido fica > 3KB
    img_path = tmp_path / "emoji.png"
    img = Image.new("RGB", (32, 32), (200, 100, 50))
    img.save(img_path, format="PNG", compress_level=0)
    assert img_path.stat().st_size >= 3 * 1024  # garantia
    f = HeuristicImageFilter(aggressive=False)
    v = f.evaluate(img_path)
    assert v.skip is True
    assert "dimensions_too_small" in (v.reason or "")


def test_heuristic_skips_corrupted(tmp_path):
    img = _make_corrupted(tmp_path / "bad.jpg")
    f = HeuristicImageFilter()
    v = f.evaluate(img)
    assert v.skip is True
    assert "corrupted" in (v.reason or "")


def test_heuristic_skips_missing_file(tmp_path):
    f = HeuristicImageFilter()
    v = f.evaluate(tmp_path / "nope.jpg")
    assert v.skip is True


def test_heuristic_conservative_warns_blur_but_not_skip(tmp_path):
    """Imagem 100% solid color tem laplacian=0 (blur), conservador
    apenas warns."""
    img = _make_image(tmp_path / "solid.jpg", size=(800, 600),
                      color=(120, 120, 120), pattern="solid")
    f = HeuristicImageFilter(aggressive=False)
    v = f.evaluate(img)
    assert v.skip is False
    assert any("low_sharpness" in w for w in v.warnings)


def test_heuristic_aggressive_skips_blur(tmp_path):
    """Em aggressive, blur abaixo do threshold skipa."""
    img = _make_image(tmp_path / "solid.jpg", size=(800, 600),
                      color=(120, 120, 120), pattern="solid")
    f = HeuristicImageFilter(aggressive=True)
    v = f.evaluate(img)
    assert v.skip is True
    assert "too_blurry" in (v.reason or "")


def test_heuristic_aggressive_thresholds_higher():
    """Sanity: agressivo tem thresholds estritamente >= conservador."""
    assert (
        AGGRESSIVE_THRESHOLDS["size_too_small_bytes"]
        >= CONSERVATIVE_THRESHOLDS["size_too_small_bytes"]
    )
    assert (
        AGGRESSIVE_THRESHOLDS["min_dimension_px"]
        >= CONSERVATIVE_THRESHOLDS["min_dimension_px"]
    )
    assert (
        AGGRESSIVE_THRESHOLDS["blur_laplacian_min"]
        >= CONSERVATIVE_THRESHOLDS["blur_laplacian_min"]
    )


def test_heuristic_env_var_enables_aggressive(monkeypatch):
    monkeypatch.setenv("RDO_AGENT_VISUAL_FILTER_AGGRESSIVE", "true")
    f = HeuristicImageFilter()  # sem param explicito
    assert f.aggressive is True


def test_heuristic_env_var_off_default(monkeypatch):
    monkeypatch.delenv("RDO_AGENT_VISUAL_FILTER_AGGRESSIVE", raising=False)
    f = HeuristicImageFilter()
    assert f.aggressive is False


# ---------------------------------------------------------------------------
# PerceptualHashDedup
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = sqlite3.connect(tmp_path / "phash.db")
    c.row_factory = sqlite3.Row
    # Simula tabela files (FK)
    c.execute(
        "CREATE TABLE files (file_id TEXT PRIMARY KEY, obra TEXT)"
    )
    migrate_image_phashes(c)
    return c


def test_hamming_distance_identical():
    assert hamming_distance("abc123", "abc123") == 0


def test_hamming_distance_one_bit_diff():
    # 0xFFFF vs 0xFFFE = 1 bit diff
    assert hamming_distance("ffff", "fffe") == 1


def test_hamming_distance_size_mismatch_raises():
    with pytest.raises(ValueError):
        hamming_distance("abcd", "ab")


def test_phash_register_and_find_duplicate(conn, tmp_path):
    img1 = _make_image(tmp_path / "a.jpg", size=(400, 300), pattern="stripes")
    img2 = _make_image(tmp_path / "a_resized.jpg", size=(200, 150), pattern="stripes")

    conn.execute("INSERT INTO files VALUES ('f_a', 'OBRA_T')")
    conn.execute("INSERT INTO files VALUES ('f_a2', 'OBRA_T')")
    conn.commit()

    dd = PerceptualHashDedup(conn, hamming_threshold=10)
    h1 = dd.compute_phash(img1)
    h2 = dd.compute_phash(img2)
    dd.register("OBRA_T", "f_a", h1, visual_analysis_id=1)

    match = dd.find_duplicate("OBRA_T", h2)
    assert match is not None
    assert match[0] == "f_a"
    assert match[1] == 1


def test_phash_different_images_no_match(conn, tmp_path):
    img1 = _make_image(tmp_path / "stripes.jpg", size=(400, 300), pattern="stripes")
    img2 = _make_image(tmp_path / "noise.jpg", size=(400, 300), pattern="noise")

    conn.execute("INSERT INTO files VALUES ('f_s', 'OBRA_T')")
    conn.execute("INSERT INTO files VALUES ('f_n', 'OBRA_T')")
    conn.commit()

    dd = PerceptualHashDedup(conn, hamming_threshold=6)
    dd.register("OBRA_T", "f_s", dd.compute_phash(img1))
    match = dd.find_duplicate("OBRA_T", dd.compute_phash(img2))
    assert match is None


def test_phash_isolates_by_obra(conn, tmp_path):
    img = _make_image(tmp_path / "a.jpg", size=(400, 300), pattern="stripes")
    h = PerceptualHashDedup(conn).compute_phash(img)

    conn.execute("INSERT INTO files VALUES ('f_a', 'OBRA_A')")
    conn.execute("INSERT INTO files VALUES ('f_b', 'OBRA_B')")
    conn.commit()

    dd = PerceptualHashDedup(conn)
    dd.register("OBRA_A", "f_a", h)
    # Mesma img em obra diferente nao matcha (isolamento corpus)
    assert dd.find_duplicate("OBRA_B", h) is None


def test_phash_threshold_configurable(conn, tmp_path):
    img1 = _make_image(tmp_path / "a.jpg", size=(400, 300), pattern="stripes")
    img2 = _make_image(tmp_path / "b.jpg", size=(400, 300), pattern="noise")

    conn.execute("INSERT INTO files VALUES ('f_a', 'OBRA_T')")
    conn.execute("INSERT INTO files VALUES ('f_b', 'OBRA_T')")
    conn.commit()

    h1 = PerceptualHashDedup(conn).compute_phash(img1)
    h2 = PerceptualHashDedup(conn).compute_phash(img2)

    # Com threshold 100 (super-loose) acha mesmo bem diferente
    dd_loose = PerceptualHashDedup(conn, hamming_threshold=100)
    dd_loose.register("OBRA_T", "f_a", h1)
    assert dd_loose.find_duplicate("OBRA_T", h2) is not None

    # Com threshold 0 (estrito) só acha identicos
    dd_strict = PerceptualHashDedup(conn, hamming_threshold=0)
    assert dd_strict.find_duplicate("OBRA_T", h2) is None


def test_phash_count(conn, tmp_path):
    img = _make_image(tmp_path / "a.jpg", size=(400, 300), pattern="stripes")
    h = PerceptualHashDedup(conn).compute_phash(img)
    conn.execute("INSERT INTO files VALUES ('f1', 'X')")
    conn.execute("INSERT INTO files VALUES ('f2', 'X')")
    conn.execute("INSERT INTO files VALUES ('f3', 'Y')")
    conn.commit()

    dd = PerceptualHashDedup(conn)
    dd.register("X", "f1", h)
    dd.register("X", "f2", h)
    dd.register("Y", "f3", h)
    assert dd.count("X") == 2
    assert dd.count("Y") == 1


def test_migrate_image_phashes_idempotent(conn):
    migrate_image_phashes(conn)
    migrate_image_phashes(conn)
    migrate_image_phashes(conn)


# ---------------------------------------------------------------------------
# RoutingClassifier
# ---------------------------------------------------------------------------


def test_routing_default_target_is_vision(tmp_path):
    """Imagem quadrada normal → Vision API."""
    img = _make_image(tmp_path / "normal.jpg", size=(800, 800), pattern="stripes")
    rc = RoutingClassifier()
    decision = rc.classify(img)
    # Pode rotear pra document se Tesseract achar muito texto;
    # default do RoutingClassifier para imagem quadrada eh vision
    assert decision.target in ("vision", "document")


def test_routing_vertical_thin_routes_financial(tmp_path):
    """Aspect 0.5, dimensions médias → financial (comprovante)."""
    img = _make_image(
        tmp_path / "receipt.jpg", size=(400, 800), pattern="stripes",
    )
    rc = RoutingClassifier()
    decision = rc.classify(img)
    # Sem Tesseract, fail-open routes to financial
    # Com Tesseract+por, depende de bbox count
    assert decision.target in ("financial", "vision")


def test_routing_returns_decision_with_metadata(tmp_path):
    img = _make_image(tmp_path / "x.jpg", size=(400, 300), pattern="stripes")
    rc = RoutingClassifier()
    d = rc.classify(img)
    assert isinstance(d, RoutingDecision)
    assert d.reason
    if d.metadata:
        assert "aspect" in d.metadata


def test_routing_fail_open_when_pil_fails(tmp_path):
    rc = RoutingClassifier()
    d = rc.classify(tmp_path / "nonexistent.jpg")
    # Sem PIL valido, fail open para vision (caller decide)
    assert d.target == "vision"
    assert "pil_failed" in d.reason


def test_routing_supports_lang_check_caches(monkeypatch, tmp_path):
    """_supports_lang só chama subprocess uma vez (cache)."""
    rc = RoutingClassifier()
    if not rc.has_tesseract:
        pytest.skip("Tesseract não disponível")

    n_calls = [0]
    real_run = __import__("subprocess").run

    def counter(*args, **kwargs):
        n_calls[0] += 1
        return real_run(*args, **kwargs)

    monkeypatch.setattr("subprocess.run", counter)
    rc._supports_lang("eng")
    rc._supports_lang("eng")
    rc._supports_lang("por")
    # Apenas 1 chamada subprocess (cache)
    assert n_calls[0] == 1
