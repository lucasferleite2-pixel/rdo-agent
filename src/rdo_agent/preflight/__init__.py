"""
Pre-flight check — Sessão 7 (dívida #55).

Estimativa antecipada de **custo, tempo e disco** antes de disparar
processamento pesado. Roda contra um ZIP-fonte do WhatsApp e produz
``PreflightReport`` com:

- Contagem estimada de mensagens / áudios / imagens / vídeos / PDFs
  (via amostragem do ZIP, sem extrair).
- Disco necessário vs disponível.
- Custos por estágio (transcribe / classify / vision / narrator).
- Tempo estimado (single-machine, batch=1).

Uso CLI:

    rdo-agent estimate --zip path/to/chat.zip

Integração com ``ingest`` (futuro): rodar pre-flight automaticamente
e exigir confirmação quando custo > $50, disco crítico, ou APIs
inalcançáveis. Esta sessão entrega só a primitiva e o comando
``estimate`` standalone — wiring no ``ingest`` fica para Sessão 8+.
"""

from __future__ import annotations

from rdo_agent.preflight.estimator import (
    DEFAULT_RATES,
    CostBreakdown,
    PreflightReport,
    TimeBreakdown,
    preflight_check,
)

__all__ = [
    "DEFAULT_RATES",
    "CostBreakdown",
    "PreflightReport",
    "TimeBreakdown",
    "preflight_check",
]
