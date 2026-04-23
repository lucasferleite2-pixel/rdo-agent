"""
yaml_writer.py — serializa GroundTruth -> YAML compativel com o loader
de `rdo_agent.ground_truth`.

Estrategia: converte via `dataclasses.asdict`, remove campos None (mais
enxuto e evita ruido em diff/git), remove `raw` (redundante) e escreve
com `yaml.safe_dump` (block style, allow_unicode=True) pra leitura
humana.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from rdo_agent.ground_truth.schema import GroundTruth


def _prune_empty(obj: Any) -> Any:
    """
    Recursivamente remove:
      - chaves cujo valor eh None
      - listas vazias (quando filhas de dict — preserva [] em root se
        explicitamente desejado, mas aqui nao ocorre)
      - strings vazias (idem None)
    """
    if isinstance(obj, dict):
        pruned: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "raw":
                continue
            pruned_v = _prune_empty(v)
            if pruned_v is None or pruned_v == "" or pruned_v == []:
                continue
            pruned[k] = pruned_v
        return pruned
    if isinstance(obj, list):
        return [_prune_empty(x) for x in obj]
    return obj


def write_ground_truth_yaml(gt: GroundTruth, path: Path) -> None:
    """
    Serializa GroundTruth em YAML humano-legivel.

    Comportamento:
      - sobrescreve `path` se existir
      - cria diretorios intermediarios
      - pruna fields vazios para enxugar o output
      - preserva ordem canonica das secoes (obra_real, canal, contratos,
        pagamentos_confirmados, pagamentos_pendentes, totais,
        estado_atual, aspectos_nao_registrados_em_evidencia)
    """
    import yaml

    data = asdict(gt)
    pruned = _prune_empty(data)

    # Reordena em ordem canonica pra ficar igual ao YAML original
    canonical_order = [
        "obra_real", "canal", "contratos", "pagamentos_confirmados",
        "pagamentos_pendentes", "totais", "estado_atual",
        "aspectos_nao_registrados_em_evidencia",
    ]
    ordered: dict[str, Any] = {}
    for k in canonical_order:
        if k in pruned:
            ordered[k] = pruned[k]
    # preserva chaves desconhecidas (forward compat) ao final
    for k, v in pruned.items():
        if k not in ordered:
            ordered[k] = v

    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        ordered, sort_keys=False, allow_unicode=True, default_flow_style=False,
    )
    path.write_text(text, encoding="utf-8")


__all__ = [
    "write_ground_truth_yaml",
]
