"""
Vision cascade — 4 camadas (Sessão 9 / dívida #47).

Reduz custo de chamadas a OpenAI Vision aplicando filtros antes da
API. Em corpus grande (5000+ imagens), 60-80% são meme/sticker/
screenshot irrelevantes — filtrá-los pré-API economiza dezenas de
dólares.

Camadas, em ordem crescente de custo:

1. **Heurísticas locais (CPU, $0)** — `HeuristicImageFilter`:
   tamanho, dimensões, blur (Laplacian variance), corruption.
   Modo `conservative` (default) skipa só o óbvio; modo
   `aggressive` (opt-in via env var) é mais estrito.

2. **pHash dedup (CPU, $0)** — `PerceptualHashDedup`: imagens
   visualmente iguais (incluindo rescales) compartilham análise.
   Uses `imagehash>=4.3`.

3. **Routing heurístico (CPU, $0)** — `RoutingClassifier`: aspect
   ratio + densidade de texto via Tesseract bbox count para
   identificar comprovantes (verticais finos com muito texto) e
   screenshots (texto denso). **Sem ML**, sem deps pesadas.

   Pre-classify zero-shot via CLIP/transformers fica para dívida #60
   futura quando triggers ativarem (corpus 1000+ imagens com
   pHash hit < 20%).

4. **Vision API** — única que custa $$. Só é chamada em imagens
   que sobreviveram às 3 camadas anteriores.

A função orquestradora `process_visual_pending` integra com a infra
do GRUPO 2 (state machine, logger, cost tracking, circuit breaker
``openai_vision``) e fica em ``visual_analyzer/__init__.py``. Aqui
moram as **primitivas** das camadas.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Camada 1 — Heurísticas locais
# ---------------------------------------------------------------------------


# Modo conservador: skipa só o óbvio
CONSERVATIVE_THRESHOLDS = {
    "size_too_small_bytes": 3 * 1024,        # 3KB
    "size_too_large_bytes": 25 * 1024 * 1024, # 25MB (warn, não skip)
    "min_dimension_px":     64,
    "max_dimension_px":     8192,            # warn, não skip
    "blur_laplacian_min":   10,              # warn, não skip
}

# Modo agressivo (opt-in): thresholds mais estritos
AGGRESSIVE_THRESHOLDS = {
    "size_too_small_bytes": 5 * 1024,
    "size_too_large_bytes": 15 * 1024 * 1024,  # também skipa
    "min_dimension_px":     100,
    "max_dimension_px":     5000,
    "blur_laplacian_min":   30,
}


@dataclass(frozen=True)
class HeuristicVerdict:
    """Resultado da Camada 1."""

    skip: bool
    reason: str | None = None
    warnings: tuple[str, ...] = ()


def _aggressive_mode_default() -> bool:
    val = os.environ.get("RDO_AGENT_VISUAL_FILTER_AGGRESSIVE", "").lower()
    return val in ("1", "true", "yes", "on")


class HeuristicImageFilter:
    """
    Filtros pure Python sobre PIL Image. Sem custo de API.

    Args:
        aggressive: ``True`` aumenta thresholds. Default lê env var
            ``RDO_AGENT_VISUAL_FILTER_AGGRESSIVE``.
    """

    def __init__(self, *, aggressive: bool | None = None):
        self.aggressive = (
            aggressive if aggressive is not None else _aggressive_mode_default()
        )
        self.thresholds = (
            AGGRESSIVE_THRESHOLDS if self.aggressive else CONSERVATIVE_THRESHOLDS
        )

    def evaluate(self, image_path: Path) -> HeuristicVerdict:
        """Roda filtros. Retorna ``HeuristicVerdict``."""
        try:
            size = image_path.stat().st_size
        except (OSError, FileNotFoundError) as e:
            return HeuristicVerdict(skip=True, reason=f"file_error: {e}")

        warnings: list[str] = []

        # 1) Tamanho mínimo (sticker/emoji micro)
        if size < self.thresholds["size_too_small_bytes"]:
            return HeuristicVerdict(
                skip=True,
                reason=f"too_small ({size}B < {self.thresholds['size_too_small_bytes']}B)",
            )

        # 2) Tamanho máximo
        if size > self.thresholds["size_too_large_bytes"]:
            if self.aggressive:
                return HeuristicVerdict(
                    skip=True,
                    reason=f"too_large ({size}B; aggressive mode)",
                )
            warnings.append(f"large_size: {size}B")

        # 3) Abrir imagem (corruption check)
        try:
            from PIL import Image
        except ImportError as e:
            return HeuristicVerdict(skip=True, reason=f"pillow_missing: {e}")

        try:
            with Image.open(image_path) as img:
                w, h = img.size
                # Force load para detectar corruption real
                img.load()
        except Exception as e:
            return HeuristicVerdict(
                skip=True, reason=f"corrupted: {type(e).__name__}",
            )

        # 4) Dimensões mínimas
        if w < self.thresholds["min_dimension_px"] or h < self.thresholds["min_dimension_px"]:
            return HeuristicVerdict(
                skip=True,
                reason=f"dimensions_too_small ({w}x{h})",
            )

        # 5) Dimensões máximas
        if w > self.thresholds["max_dimension_px"] or h > self.thresholds["max_dimension_px"]:
            if self.aggressive:
                return HeuristicVerdict(
                    skip=True,
                    reason=f"dimensions_too_large ({w}x{h}; aggressive)",
                )
            warnings.append(f"large_dimensions: {w}x{h}")

        # 6) Blur via Laplacian variance (modo agressivo skipa; conservador só warning)
        try:
            blur_var = _laplacian_variance(image_path)
        except Exception as e:
            warnings.append(f"blur_check_failed: {e}")
            blur_var = None

        if blur_var is not None and blur_var < self.thresholds["blur_laplacian_min"]:
            if self.aggressive:
                return HeuristicVerdict(
                    skip=True,
                    reason=f"too_blurry (laplacian={blur_var:.1f}; aggressive)",
                )
            warnings.append(f"low_sharpness: laplacian={blur_var:.1f}")

        return HeuristicVerdict(skip=False, warnings=tuple(warnings))


def _laplacian_variance(image_path: Path) -> float:
    """
    Variância do filtro Laplaciano sobre versão grayscale da imagem.
    Valores baixos (<30) indicam blur. Implementação pura PIL+numpy
    (sem OpenCV).
    """
    import numpy as np
    from PIL import Image, ImageOps

    with Image.open(image_path) as img:
        gray = ImageOps.grayscale(img)
        arr = np.asarray(gray, dtype=np.float32)

    # Kernel Laplaciano 3x3
    # Aplica via convolução manual sem dep de scipy (ja vem com imagehash,
    # mas evitamos dep direta aqui)
    h, w = arr.shape
    if h < 3 or w < 3:
        return 0.0
    # Diferença finita 2D de segunda ordem
    lap = (
        -4 * arr[1:-1, 1:-1]
        + arr[:-2, 1:-1] + arr[2:, 1:-1]
        + arr[1:-1, :-2] + arr[1:-1, 2:]
    )
    return float(lap.var())


# ---------------------------------------------------------------------------
# Camada 2 — pHash dedup
# ---------------------------------------------------------------------------


def migrate_image_phashes(conn: sqlite3.Connection) -> None:
    """Cria tabela ``image_phashes`` (idempotente)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS image_phashes (
            file_id        TEXT PRIMARY KEY,
            obra           TEXT NOT NULL,
            phash          TEXT NOT NULL,
            visual_analysis_id INTEGER,
            created_at     TEXT NOT NULL,
            FOREIGN KEY (file_id) REFERENCES files(file_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_image_phashes_obra "
        "ON image_phashes(obra)"
    )
    conn.commit()


def hamming_distance(a: str, b: str) -> int:
    """
    Distância de Hamming entre dois pHashes hex. Cada caractere hex
    representa 4 bits — XOR e popcount.
    """
    if len(a) != len(b):
        raise ValueError(f"hashes de tamanho diferente: {len(a)} vs {len(b)}")
    return bin(int(a, 16) ^ int(b, 16)).count("1")


class PerceptualHashDedup:
    """
    Dedup por pHash (8x8 DCT-based). Threshold default 6: imagens
    com diff ≤ 6 bits são "praticamente iguais" mesmo em resoluções
    diferentes.
    """

    def __init__(
        self, conn: sqlite3.Connection, *,
        hamming_threshold: int = 6,
    ):
        self.conn = conn
        self.hamming_threshold = hamming_threshold
        migrate_image_phashes(conn)

    def compute_phash(self, image_path: Path) -> str:
        """Retorna phash hex via biblioteca imagehash."""
        import imagehash
        from PIL import Image

        with Image.open(image_path) as img:
            return str(imagehash.phash(img))

    def find_duplicate(
        self, obra: str, phash: str, *,
        exclude_file_id: str | None = None,
    ) -> tuple[str, int | None] | None:
        """
        Retorna ``(file_id_match, visual_analysis_id)`` ou ``None``.
        Busca linear (O(n) sobre obra). Para corpus muito grande
        considerar índice DCT especializado (futuro).
        """
        rows = self.conn.execute(
            "SELECT file_id, phash, visual_analysis_id "
            "FROM image_phashes WHERE obra = ?",
            (obra,),
        ).fetchall()
        for r in rows:
            cand_file_id = r[0]
            if exclude_file_id and cand_file_id == exclude_file_id:
                continue
            if hamming_distance(phash, r[1]) <= self.hamming_threshold:
                return (cand_file_id, r[2])
        return None

    def register(
        self, obra: str, file_id: str, phash: str,
        *, visual_analysis_id: int | None = None,
    ) -> None:
        """INSERT OR REPLACE (file_id é PK)."""
        self.conn.execute(
            "INSERT OR REPLACE INTO image_phashes "
            "(file_id, obra, phash, visual_analysis_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (file_id, obra, phash, visual_analysis_id, _now_iso()),
        )
        self.conn.commit()

    def count(self, obra: str) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM image_phashes WHERE obra = ?",
            (obra,),
        ).fetchone()[0])


# ---------------------------------------------------------------------------
# Camada 3 — Routing heurístico (sem ML)
# ---------------------------------------------------------------------------


# Aspect ratio "comprovante de pagamento" (vertical fino; ex: PIX print)
# largura/altura entre 0.4 e 0.7 e dimensões médias
RECEIPT_ASPECT_MIN = 0.35
RECEIPT_ASPECT_MAX = 0.75
RECEIPT_DIM_MIN = 200
RECEIPT_DIM_MAX = 2400

# Densidade de texto (chars/cm² ou por bbox count via Tesseract).
# Threshold empírico — bbox count alto = screenshot/doc.
SCREENSHOT_BBOX_COUNT_MIN = 30


@dataclass(frozen=True)
class RoutingDecision:
    """Resultado da Camada 3."""

    target: str  # 'financial' | 'document' | 'vision'
    reason: str
    metadata: dict | None = None


class RoutingClassifier:
    """
    Decide se a imagem é candidata a OCR especializado (financial /
    document) ou continua para Vision API. Pure Python, zero ML.

    Optionally usa Tesseract para detectar densidade de texto, mas
    fail-open (assume "tem texto" se Tesseract ausente).
    """

    def __init__(self, *, tesseract_lang: str = "por"):
        import shutil
        self.has_tesseract = bool(shutil.which("tesseract"))
        self.tesseract_lang = tesseract_lang
        self._supported_langs: set[str] | None = None

    def _supports_lang(self, lang: str) -> bool:
        """Verifica se Tesseract tem o idioma instalado (cacheado)."""
        if not self.has_tesseract:
            return False
        if self._supported_langs is None:
            try:
                import subprocess
                out = subprocess.run(
                    ["tesseract", "--list-langs"],
                    capture_output=True, text=True, timeout=5,
                )
                self._supported_langs = {
                    line.strip() for line in out.stdout.splitlines()
                    if line.strip() and not line.startswith("List")
                }
            except Exception:
                self._supported_langs = set()
        return lang in self._supported_langs

    def classify(
        self, image_path: Path,
    ) -> RoutingDecision:
        """
        Retorna ``RoutingDecision``. ``target='vision'`` é o caminho
        default; routing especializado só dispara em sinais claros.
        """
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                w, h = img.size
        except Exception as e:
            return RoutingDecision(
                target="vision",
                reason=f"pil_failed: {e}",
            )

        aspect = w / max(h, 1)

        # Receipt: vertical fino com dimensões médias
        if (RECEIPT_ASPECT_MIN <= aspect <= RECEIPT_ASPECT_MAX
                and RECEIPT_DIM_MIN <= w <= RECEIPT_DIM_MAX
                and RECEIPT_DIM_MIN <= h <= RECEIPT_DIM_MAX):
            # Confirma com bbox count (se Tesseract disponível)
            text_dense = self._text_dense(image_path)
            if text_dense is None:
                # Fail-open: rota para financial mesmo sem Tesseract
                return RoutingDecision(
                    target="financial",
                    reason="aspect_vertical_medium_no_tesseract",
                    metadata={"aspect": round(aspect, 2)},
                )
            if text_dense:
                return RoutingDecision(
                    target="financial",
                    reason="aspect_vertical_medium + text_dense",
                    metadata={"aspect": round(aspect, 2)},
                )

        # Screenshot/doc: muita densidade de texto independente de aspect
        text_dense = self._text_dense(image_path)
        if text_dense:
            return RoutingDecision(
                target="document",
                reason="text_dense",
                metadata={"aspect": round(aspect, 2)},
            )

        return RoutingDecision(
            target="vision",
            reason="default",
            metadata={"aspect": round(aspect, 2)},
        )

    def _text_dense(self, image_path: Path) -> bool | None:
        """
        ``True`` se Tesseract acha ≥ N bboxes; ``False`` se acha
        poucos; ``None`` se Tesseract indisponível ou falhou
        (fail-open semântico — caller decide).
        """
        if not self.has_tesseract:
            return None
        # Escolhe lang fallback eng se por nao instalado
        lang = self.tesseract_lang if self._supports_lang(self.tesseract_lang) else "eng"
        if not self._supports_lang(lang):
            return None
        try:
            import subprocess
            out = subprocess.run(
                ["tesseract", str(image_path), "-",
                 "-l", lang, "--psm", "3", "tsv"],
                capture_output=True, text=True, timeout=15,
            )
            if out.returncode != 0:
                return None
            # tsv: header + linhas. Conta linhas com texto não-vazio.
            lines = out.stdout.splitlines()[1:]
            n_with_text = 0
            for line in lines:
                cols = line.split("\t")
                if len(cols) >= 12 and cols[-1].strip():
                    n_with_text += 1
            return n_with_text >= SCREENSHOT_BBOX_COUNT_MIN
        except Exception as e:
            log.warning("tesseract failed em %s: %s", image_path, e)
            return None


__all__ = [
    "AGGRESSIVE_THRESHOLDS",
    "CONSERVATIVE_THRESHOLDS",
    "HeuristicImageFilter",
    "HeuristicVerdict",
    "PerceptualHashDedup",
    "RECEIPT_ASPECT_MAX",
    "RECEIPT_ASPECT_MIN",
    "RECEIPT_DIM_MAX",
    "RECEIPT_DIM_MIN",
    "RoutingClassifier",
    "RoutingDecision",
    "SCREENSHOT_BBOX_COUNT_MIN",
    "hamming_distance",
    "migrate_image_phashes",
]
