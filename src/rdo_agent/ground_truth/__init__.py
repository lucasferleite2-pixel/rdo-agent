"""
Ground Truth — Sprint 5 Fase C.

Input estruturado de fatos contratuais conhecidos pelo operador mas
ausentes do corpus WhatsApp (acordos presenciais, contratos fisicos,
negociacoes por telefone). Usado pelo narrator pra marcar assercoes
como CONFORME / DIVERGENTE / NAO VERIFICAVEL em relacao ao GT.

Interface publica:

  - `GroundTruth`: dataclass raiz com obra_real, canal, contratos,
    pagamentos_confirmados, pagamentos_pendentes, totais,
    estado_atual, aspectos_nao_registrados_em_evidencia
  - `Contrato`, `PagamentoConfirmado`, `PagamentoPendente`, etc:
    dataclasses de componente
  - `load_ground_truth(path)`: carrega YAML -> GroundTruth (valida
    schema; levanta GroundTruthValidationError com mensagens
    informativas se faltar campo obrigatorio)
  - `GroundTruthValidationError`: excecao especifica
"""

from __future__ import annotations

from rdo_agent.ground_truth.loader import (
    GroundTruthValidationError,
    load_ground_truth,
)
from rdo_agent.ground_truth.schema import (
    Canal,
    CanalParte,
    Contrato,
    EstadoAtual,
    GroundTruth,
    ObraReal,
    PagamentoConfirmado,
    PagamentoPendente,
    ProblemaConhecido,
    Totais,
)

__all__ = [
    "Canal",
    "CanalParte",
    "Contrato",
    "EstadoAtual",
    "GroundTruth",
    "GroundTruthValidationError",
    "ObraReal",
    "PagamentoConfirmado",
    "PagamentoPendente",
    "ProblemaConhecido",
    "Totais",
    "load_ground_truth",
]
