"""
schema.py — dataclasses do Ground Truth YAML (Sprint 5 Fase C).

Correspondencia direta com `docs/ground_truth/*.yml`. Campos opcionais
devem defaultar pra None/list vazia; campos obrigatorios levantam erro
no loader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ObraReal:
    nome: str
    contratada: str
    codesc: int | None = None
    municipio: str | None = None
    uf: str | None = None
    contratante_publico: str | None = None


@dataclass
class CanalParte:
    nome: str
    papel: str
    especialidade: str | None = None


@dataclass
class Canal:
    id: str
    tipo: str
    parte_A: CanalParte
    parte_B: CanalParte


@dataclass
class Contrato:
    id: str
    escopo: str
    valor_total: float
    forma_pagamento: str | None = None
    origem: str | None = None
    data_acordo: str | None = None
    observacao: str | None = None
    status: str | None = None


@dataclass
class PagamentoConfirmado:
    """
    Um pagamento efetivamente ocorrido. `contrato_ref` pode ser None
    para reembolsos/despesas operacionais fora dos contratos.
    """

    valor: float
    data: str            # YYYY-MM-DD
    hora: str | None = None  # HH:MM
    contrato_ref: str | None = None
    parcela: str | None = None
    tipo: str | None = None
    descricao_pix: str | None = None
    nota: str | None = None


@dataclass
class PagamentoPendente:
    valor: float
    contrato_ref: str | None = None
    parcela: str | None = None
    gatilho_pagamento: str | None = None
    data_prevista: str | None = None
    nota: str | None = None


@dataclass
class Totais:
    valor_negociado_total: float | None = None
    valor_pago_total: float | None = None
    valor_pago_contratual: float | None = None
    valor_pendente: float | None = None
    pct_concluido_financeiramente: float | None = None


@dataclass
class ProblemaConhecido:
    descricao: str
    detectado_em: str | None = None
    impacto: str | None = None
    responsabilidade: str | None = None


@dataclass
class EstadoAtual:
    data_snapshot: str | None = None
    obra_em_execucao: bool | None = None
    everaldo_ainda_no_canteiro: bool | None = None  # legado do piloto; permanece opcional
    c1_status: str | None = None
    c2_status: str | None = None
    problemas_conhecidos: list[ProblemaConhecido] = field(default_factory=list)


@dataclass
class GroundTruth:
    """
    Raiz do Ground Truth. Ao menos `obra_real` e `canal` sao obrigatorios;
    demais sao recomendados mas nao bloqueantes.
    """

    obra_real: ObraReal
    canal: Canal
    contratos: list[Contrato] = field(default_factory=list)
    pagamentos_confirmados: list[PagamentoConfirmado] = field(default_factory=list)
    pagamentos_pendentes: list[PagamentoPendente] = field(default_factory=list)
    totais: Totais | None = None
    estado_atual: EstadoAtual | None = None
    aspectos_nao_registrados_em_evidencia: list[str] = field(default_factory=list)

    # Raw YAML preservado pra referencia; usado pra serializar no dossier
    # sem perder campos desconhecidos pela schema (forward compat).
    raw: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "Canal",
    "CanalParte",
    "Contrato",
    "EstadoAtual",
    "GroundTruth",
    "ObraReal",
    "PagamentoConfirmado",
    "PagamentoPendente",
    "ProblemaConhecido",
    "Totais",
]
