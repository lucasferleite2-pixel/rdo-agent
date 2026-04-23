"""
gt_extractor — Sprint 5 Fase D (Sessao 2).

Modulo de extracao de Ground Truth interativa. Converte o que o
operador SABE (mas o corpus WhatsApp nao cobre) em YAML estruturado
consumivel pelo narrator V3_GT.

Dois modos:

  - SIMPLE (questionario fixo, sincrono, zero API):
    `run_simple_interview(input) -> GroundTruth`
    Perguntas canonicas em sequencia; operador responde via stdin;
    Enter vazio/"skip" pula secao opcional; campos obrigatorios
    revalidam.

  - ADAPTIVE (conduzida por Claude Sonnet 4.6) — Fase D2:
    `run_adaptive_interview(input) -> GroundTruth`
    Modelo faz perguntas baseadas em historico, detecta contradicoes,
    sugere campos. Turnos iterativos ate is_complete=True.

Ambas producem `GroundTruth` (reuso da schema de `rdo_agent.ground_truth`)
e podem ser serializadas via `write_ground_truth_yaml(gt, path)`.
"""

from __future__ import annotations

from rdo_agent.gt_extractor.interview import (
    InterviewInput,
    InterviewSkipped,
    run_simple_interview,
)
from rdo_agent.gt_extractor.yaml_writer import (
    write_ground_truth_yaml,
)

__all__ = [
    "InterviewInput",
    "InterviewSkipped",
    "run_simple_interview",
    "write_ground_truth_yaml",
]
