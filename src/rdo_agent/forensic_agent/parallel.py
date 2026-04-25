"""
Correlator paralelo (Sessão 10 / dívida #50).

Discovery (Phase 10.0) revelou que o correlator atual em
``correlator.py:detect_correlations`` é orquestrador thin que invoca
4 detectores **independentes** (TEMPORAL, SEMANTIC, MATH,
CONTRACT_RENEGOTIATION). A complexidade O(N²) está dentro de cada
detector, não no orquestrador. Cada um já tem janela temporal
própria (TEMPORAL=30min, SEMANTIC=3d, MATH=48h, RENEGOTIATION=30d).

Esta sessão entrega **paralelismo inter-detector** via
``ProcessPoolExecutor``: os 4 detectores rodam em workers separados,
cada um com sua própria conexão SQLite (conn não é pickle-safe).
Ganho fixo de até 4× em CPU multi-core.

Paralelismo **intra-detector** (subdividir o trabalho de UM detector
individual entre N workers) fica como dívida #61, ativada por
triggers de produção (Sessão 11+).

Window override por detector: cada detector aceita kwarg ``window``
(``timedelta``). Override útil para corpus longo (ex: reduzir
SEMANTIC de 3d para 1d, MATH de 48h para 24h).
"""

from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from rdo_agent.forensic_agent.correlator import (
    Correlation,
    save_correlation,
)
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)


# Nome canônico de cada detector — usado como key em
# ``DetectorWindows``, parâmetro pra ``_run_detector_worker`` e
# label em logs.
DETECTOR_NAMES: tuple[str, ...] = (
    "temporal", "semantic", "math", "contract_renegotiation",
)


@dataclass
class DetectorWindows:
    """
    Override de janela temporal por detector.

    None mantém o WINDOW default do detector (TEMPORAL=30min,
    SEMANTIC=3d, MATH=48h, RENEGOTIATION=30d).
    """

    temporal: timedelta | None = None
    semantic: timedelta | None = None
    math: timedelta | None = None
    contract_renegotiation: timedelta | None = None

    def for_detector(self, name: str) -> timedelta | None:
        return getattr(self, name, None)

    @classmethod
    def all_days(cls, days: float) -> "DetectorWindows":
        """Atalho: aplica mesma janela em dias para todos."""
        td = timedelta(days=days)
        return cls(temporal=td, semantic=td, math=td, contract_renegotiation=td)


@dataclass
class CorrelationStats:
    """Métricas de execução paralela (1 row por detector)."""

    by_detector: dict[str, int] = field(default_factory=dict)
    errors_by_detector: dict[str, str] = field(default_factory=dict)
    total: int = 0


def _run_detector_worker(args: tuple) -> tuple[str, list[Correlation], str | None]:
    """
    Entry point do worker (precisa ser top-level para pickling).

    Args:
        args: (detector_name, db_path_str, obra, window_seconds_or_none)

    Returns:
        (detector_name, list_correlations, error_message_or_none).
        Erros não derrubam o pool — são reportados como string.
    """
    detector_name, db_path_str, obra, window_seconds = args
    window = (
        timedelta(seconds=window_seconds) if window_seconds is not None else None
    )

    conn = sqlite3.connect(db_path_str)
    conn.row_factory = sqlite3.Row
    try:
        if detector_name == "temporal":
            from rdo_agent.forensic_agent.detectors.temporal import (
                detect_temporal_payment_context,
            )
            results = detect_temporal_payment_context(conn, obra, window=window)
        elif detector_name == "semantic":
            from rdo_agent.forensic_agent.detectors.semantic import (
                detect_semantic_payment_scope,
            )
            results = detect_semantic_payment_scope(conn, obra, window=window)
        elif detector_name == "math":
            from rdo_agent.forensic_agent.detectors.math import (
                detect_math_relations,
            )
            results = detect_math_relations(conn, obra, window=window)
        elif detector_name == "contract_renegotiation":
            from rdo_agent.forensic_agent.detectors.contract_renegotiation import (
                detect_contract_renegotiation,
            )
            results = detect_contract_renegotiation(conn, obra, window=window)
        else:
            return (
                detector_name, [],
                f"detector desconhecido: {detector_name}",
            )
        return (detector_name, list(results), None)
    except Exception as e:
        return (detector_name, [], f"{type(e).__name__}: {e}")
    finally:
        conn.close()


def parallel_detect_correlations(
    db_path: Path,
    obra: str,
    *,
    workers: int | None = None,
    windows: DetectorWindows | None = None,
    persist: bool = True,
    on_progress=None,
) -> tuple[list[Correlation], CorrelationStats]:
    """
    Executa os 4 detectores em paralelo via ``ProcessPoolExecutor``.

    Args:
        db_path: caminho do arquivo SQLite (não conexão — workers
            criam a própria).
        obra: identificador do canal/corpus.
        workers: default = min(4, cpu_count). 4 é o teto natural
            (4 detectores), aumentar não ajuda.
        windows: override de janela por detector.
        persist: ``True`` (default) faz ``save_correlation`` em cada
            resultado no main process após todos os workers terminarem.
            Persistência fora do worker evita conflito de escrita
            paralela em SQLite.
        on_progress: callback opcional ``(detector, n_results,
            error)``. Útil para CLI/UI.

    Returns:
        ``(list[Correlation] de todos os detectores, CorrelationStats)``.
        Erros parciais (1 detector falha, outros completam) são
        reportados em ``CorrelationStats.errors_by_detector`` mas não
        levantam — caller decide o que fazer.
    """
    db_path = Path(db_path)
    workers = workers or min(4, max(1, (os.cpu_count() or 1)))
    windows = windows or DetectorWindows()

    args_list: list[tuple] = []
    for name in DETECTOR_NAMES:
        win = windows.for_detector(name)
        win_sec = int(win.total_seconds()) if win is not None else None
        args_list.append((name, str(db_path), obra, win_sec))

    stats = CorrelationStats()
    all_correlations: list[Correlation] = []

    with ProcessPoolExecutor(max_workers=workers) as executor:
        future_to_name = {
            executor.submit(_run_detector_worker, args): args[0]
            for args in args_list
        }
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                detector_name, results, error = future.result()
            except Exception as e:
                stats.errors_by_detector[name] = (
                    f"{type(e).__name__}: {e}"
                )
                log.warning("detector %s morreu: %s", name, e)
                if on_progress:
                    on_progress(name, 0, str(e))
                continue
            if error:
                stats.errors_by_detector[detector_name] = error
                log.warning(
                    "detector %s reportou erro: %s",
                    detector_name, error,
                )
            stats.by_detector[detector_name] = len(results)
            all_correlations.extend(results)
            if on_progress:
                on_progress(detector_name, len(results), error)

    stats.total = len(all_correlations)

    if persist and all_correlations:
        # Persistência no main process — uma única transação
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            for c in all_correlations:
                save_correlation(conn, c)
        finally:
            conn.close()

    log.info(
        "parallel_detect: total=%d workers=%d errors=%d",
        stats.total, workers, len(stats.errors_by_detector),
    )
    return all_correlations, stats


__all__ = [
    "DETECTOR_NAMES",
    "CorrelationStats",
    "DetectorWindows",
    "parallel_detect_correlations",
]
