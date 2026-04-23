"""
Forensic Agent — Sprint 5.

Camada de agente forense que consome dossier estruturado + gera
narrativa em linguagem natural (Fase A) e detecta correlacoes
(Fase B — esqueleto apenas nesta sessao).

Interface publica:

Fase A (producao):
  - build_day_dossier(conn, obra, date) -> dict
  - build_obra_overview_dossier(conn, obra) -> dict
  - compute_dossier_hash(dossier) -> str
  - narrate(dossier, conn) -> NarrationResult
  - validate_narrative(body, dossier, self_assessment, full) -> dict
  - save_narrative(conn, *, ...) -> (id, path, was_cached)

Fase B (esqueleto):
  - Correlation (dataclass)
  - find_correlations_for_day (NotImplementedError)
  - find_correlations_obra_wide (NotImplementedError)
  - save_correlation (ja implementado)

Exemplo de uso completo (Fase A):

    from rdo_agent.forensic_agent import (
        build_day_dossier, narrate, validate_narrative, save_narrative,
        compute_dossier_hash,
    )

    d = build_day_dossier(conn, "EVERALDO_SANTAQUITERIA", "2026-04-06")
    h = compute_dossier_hash(d)
    result = narrate(d, conn)
    v = validate_narrative(result.markdown_body, d,
                           result.self_assessment, result.markdown_text)
    nid, path, cached = save_narrative(
        conn, obra=d["obra"], scope=d["scope"], scope_ref=d["scope_ref"],
        dossier_hash=h, narration=result, validation=v,
        events_count=d["statistics"]["events_total"],
    )
"""

from __future__ import annotations

from rdo_agent.forensic_agent.correlator import (
    Correlation,
    EventSource,
    find_correlations_for_day,
    find_correlations_obra_wide,
    save_correlation,
)
from rdo_agent.forensic_agent.types import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CorrelationType,
)
from rdo_agent.forensic_agent.dossier_builder import (
    build_day_dossier,
    build_obra_overview_dossier,
    compute_dossier_hash,
)
from rdo_agent.forensic_agent.narrator import (
    MODEL,
    PROMPT_VERSION,
    NarrationResult,
    narrate,
)
from rdo_agent.forensic_agent.persistence import (
    save_narrative,
)
from rdo_agent.forensic_agent.validator import (
    validate_narrative,
)

__all__ = [
    # Fase A
    "MODEL",
    "PROMPT_VERSION",
    "NarrationResult",
    "build_day_dossier",
    "build_obra_overview_dossier",
    "compute_dossier_hash",
    "narrate",
    "save_narrative",
    "validate_narrative",
    # Fase B esqueleto
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "Correlation",
    "CorrelationType",
    "EventSource",
    "find_correlations_for_day",
    "find_correlations_obra_wide",
    "save_correlation",
]
