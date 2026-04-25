"""
Pipeline State — Sessão 6 (dívida #44).

Wrapper ergonômico sobre a tabela ``tasks`` (já existente, populada
pelo orchestrator desde Sprint 1). Expõe operações idiomáticas de
state machine para callers e CLI:

- ``PipelineStateManager.status(obra)`` — agrega por (task_type, status)
- ``PipelineStateManager.resumable_state(obra)`` — detecta tasks que
  estavam ``running`` quando o processo morreu (crash recovery)
- ``PipelineStateManager.reset_running(obra)`` — devolve essas tasks
  pra ``pending`` para retry sob controle do operador
- ``PipelineStateManager.claim(obra, task_type=...)`` — atômico:
  pega próxima ``pending`` respeitando ``depends_on`` e marca
  ``running`` na mesma transação
- ``PipelineStateManager.complete(task_id, result_ref=None)``
- ``PipelineStateManager.fail(task_id, error_msg)``

Não duplica a lógica de schema/enqueue/handler do orchestrator —
apenas oferece API observável e helpers de recovery. Ver ADR-007 para
o rationale "wrapper vs nova tabela".
"""

from __future__ import annotations

from rdo_agent.pipeline_state.state_manager import (
    PipelineStateManager,
    StatusReport,
)

__all__ = [
    "PipelineStateManager",
    "StatusReport",
]
