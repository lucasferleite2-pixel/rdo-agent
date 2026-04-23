"""
loader.py — Carrega e valida YAML de Ground Truth.

Import lazy do `yaml` pra nao quebrar quando a dep nao estiver
instalada e o usuario nao usar --context. Erro claro se --context
for usado sem a dep.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


class GroundTruthValidationError(Exception):
    """Erro de validacao schema do GT YAML."""


def _require(data: dict, key: str, *, context: str) -> Any:
    if key not in data:
        raise GroundTruthValidationError(
            f"campo obrigatorio ausente: '{key}' em '{context}'"
        )
    return data[key]


def _parse_obra_real(d: dict) -> ObraReal:
    nome = _require(d, "nome", context="obra_real")
    contratada = _require(d, "contratada", context="obra_real")
    return ObraReal(
        nome=str(nome),
        contratada=str(contratada),
        codesc=d.get("codesc"),
        municipio=d.get("municipio"),
        uf=d.get("uf"),
        contratante_publico=d.get("contratante_publico"),
    )


def _parse_parte(d: dict, ctx: str) -> CanalParte:
    return CanalParte(
        nome=str(_require(d, "nome", context=ctx)),
        papel=str(_require(d, "papel", context=ctx)),
        especialidade=d.get("especialidade"),
    )


def _parse_canal(d: dict) -> Canal:
    pa = _require(d, "parte_A", context="canal")
    pb = _require(d, "parte_B", context="canal")
    return Canal(
        id=str(_require(d, "id", context="canal")),
        tipo=str(_require(d, "tipo", context="canal")),
        parte_A=_parse_parte(pa, "canal.parte_A"),
        parte_B=_parse_parte(pb, "canal.parte_B"),
    )


def _parse_contrato(d: dict, idx: int) -> Contrato:
    ctx = f"contratos[{idx}]"
    return Contrato(
        id=str(_require(d, "id", context=ctx)),
        escopo=str(_require(d, "escopo", context=ctx)),
        valor_total=float(_require(d, "valor_total", context=ctx)),
        forma_pagamento=d.get("forma_pagamento"),
        origem=d.get("origem"),
        data_acordo=d.get("data_acordo"),
        observacao=d.get("observacao"),
        status=d.get("status"),
    )


def _parse_pag_conf(d: dict, idx: int) -> PagamentoConfirmado:
    ctx = f"pagamentos_confirmados[{idx}]"
    return PagamentoConfirmado(
        valor=float(_require(d, "valor", context=ctx)),
        data=str(_require(d, "data", context=ctx)),
        hora=d.get("hora"),
        contrato_ref=d.get("contrato_ref"),
        parcela=d.get("parcela"),
        tipo=d.get("tipo"),
        descricao_pix=d.get("descricao_pix"),
        nota=d.get("nota"),
    )


def _parse_pag_pend(d: dict, idx: int) -> PagamentoPendente:
    ctx = f"pagamentos_pendentes[{idx}]"
    return PagamentoPendente(
        valor=float(_require(d, "valor", context=ctx)),
        contrato_ref=d.get("contrato_ref"),
        parcela=d.get("parcela"),
        gatilho_pagamento=d.get("gatilho_pagamento"),
        data_prevista=d.get("data_prevista"),
        nota=d.get("nota"),
    )


def _parse_totais(d: dict) -> Totais:
    return Totais(
        valor_negociado_total=d.get("valor_negociado_total"),
        valor_pago_total=d.get("valor_pago_total"),
        valor_pago_contratual=d.get("valor_pago_contratual"),
        valor_pendente=d.get("valor_pendente"),
        pct_concluido_financeiramente=d.get("pct_concluido_financeiramente"),
    )


def _parse_problema(d: dict, idx: int) -> ProblemaConhecido:
    ctx = f"estado_atual.problemas_conhecidos[{idx}]"
    return ProblemaConhecido(
        descricao=str(_require(d, "descricao", context=ctx)),
        detectado_em=d.get("detectado_em"),
        impacto=d.get("impacto"),
        responsabilidade=d.get("responsabilidade"),
    )


def _parse_estado_atual(d: dict) -> EstadoAtual:
    problemas = [
        _parse_problema(p, i)
        for i, p in enumerate(d.get("problemas_conhecidos") or [])
    ]
    return EstadoAtual(
        data_snapshot=d.get("data_snapshot"),
        obra_em_execucao=d.get("obra_em_execucao"),
        everaldo_ainda_no_canteiro=d.get("everaldo_ainda_no_canteiro"),
        c1_status=d.get("c1_status"),
        c2_status=d.get("c2_status"),
        problemas_conhecidos=problemas,
    )


def _parse_root(raw: dict) -> GroundTruth:
    if not isinstance(raw, dict):
        raise GroundTruthValidationError(
            f"GT root deve ser dict (mapping YAML), recebeu {type(raw).__name__}"
        )
    obra_real = _parse_obra_real(_require(raw, "obra_real", context="root"))
    canal = _parse_canal(_require(raw, "canal", context="root"))
    contratos = [
        _parse_contrato(c, i)
        for i, c in enumerate(raw.get("contratos") or [])
    ]
    pag_conf = [
        _parse_pag_conf(p, i)
        for i, p in enumerate(raw.get("pagamentos_confirmados") or [])
    ]
    pag_pend = [
        _parse_pag_pend(p, i)
        for i, p in enumerate(raw.get("pagamentos_pendentes") or [])
    ]
    totais = _parse_totais(raw.get("totais") or {})
    estado = _parse_estado_atual(raw.get("estado_atual") or {})
    aspectos = [
        str(x) for x in (raw.get("aspectos_nao_registrados_em_evidencia") or [])
    ]

    return GroundTruth(
        obra_real=obra_real,
        canal=canal,
        contratos=contratos,
        pagamentos_confirmados=pag_conf,
        pagamentos_pendentes=pag_pend,
        totais=totais,
        estado_atual=estado,
        aspectos_nao_registrados_em_evidencia=aspectos,
        raw=raw,
    )


def load_ground_truth(path: str | Path) -> GroundTruth:
    """
    Carrega YAML de Ground Truth. Levanta:
      - FileNotFoundError: path nao existe
      - GroundTruthValidationError: YAML malformado ou falta campo
        obrigatorio (obra_real.nome, obra_real.contratada, canal.id,
        canal.tipo, canal.parte_A.*, canal.parte_B.*, contratos[].id,
        contratos[].escopo, contratos[].valor_total, pagamentos[].valor,
        pagamentos[].data)
      - ImportError: PyYAML nao instalado (mensagem orienta `pip install pyyaml`)
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Ground Truth YAML nao encontrado: {p}")

    try:
        import yaml
    except ImportError as exc:
        raise ImportError(
            "PyYAML nao instalado. Para usar --context (Ground Truth), "
            "execute: pip install pyyaml"
        ) from exc

    text = p.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise GroundTruthValidationError(
            f"erro de parse YAML em {p}: {exc}"
        ) from exc

    return _parse_root(raw)


__all__ = [
    "GroundTruthValidationError",
    "load_ground_truth",
]
