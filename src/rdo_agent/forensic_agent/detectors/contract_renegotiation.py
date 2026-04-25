"""
Detector CONTRACT_RENEGOTIATION — Sessão 5 (dívida #27).

Pattern empírico observado no caso EVERALDO_SANTAQUITERIA: o sistema
descobriu sozinho a renegociação 04/04 → 08/04 (R$ 7.000 → R$ 11.000)
através do narrador, mas não tinha detector explícito. Esta classe
formaliza a deteção:

  Pattern:
    - Mensagem A em T1 menciona valor V_A (escopo X)
    - Mensagem B em T2 > T1 menciona valor V_B != V_A (mesmo escopo X)
    - |V_B - V_A| / max(V_A, V_B) ∈ [10%, 80%]  (renegociação real,
      não item totalmente diferente)
    - T2 - T1 ≤ 30 dias
    - Overlap semântico forte entre A e B (reuso da tokenização do
      detector SEMANTIC: stems de alta especificidade compartilhados)

  Saída:
    - correlation_type='CONTRACT_RENEGOTIATION'
    - primary_event_ref='c_<id_A>' (negociação inicial)
    - related_event_ref='c_<id_B>' (renegociação)
    - time_gap_seconds = T2 - T1
    - rationale = "renegociação detectada: R$X → R$Y (variação Z%)"

Confidence (2 níveis observados em corpus):
    - 0.85: ≥2 stems HIGH compartilhados e variação em [20%, 70%]
    - 0.70: ≥1 stem HIGH compartilhado

Critério mínimo (anchoring): requer **≥1 stem HIGH** compartilhado.
Stems LOW/genéricos não ancoram sozinhos — texts de tópicos
totalmente distintos podem compartilhar 2-3 stems genéricos do PT
(filler) e gerariam falso positivo. A âncora HIGH garante que ambos
os textos discutem o mesmo escopo de obra/contrato.

Não bate de propósito com financial_records — esta correlação é
**mensagem ↔ mensagem**, sobre o histórico de negociação. Quem bate
com FRs são os detectores TEMPORAL/SEMANTIC/MATH.

Detector roda **depois** dos demais (esperado em pipeline) mas é
independente — não consulta tabela `correlations`.

Notas:
- Valores UNITARY (R$ por metro, etc) são descartados — renegociação
  contratual é sempre sobre o agregado.
- "Mesmas pessoas" não é checado nesta versão (proxy fraco sem JOIN
  com messages.sender). Documentado para evolução futura.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from rdo_agent.forensic_agent.correlator import Correlation
from rdo_agent.forensic_agent.detectors._common import (
    EventText,
    fetch_event_texts,
)
from rdo_agent.forensic_agent.detectors.math import (
    VALUE_KIND_UNITARY,
    extract_value_mentions,
)
from rdo_agent.forensic_agent.detectors.semantic import (
    HIGH_SPECIFICITY_STEMS,
    tokenize,
)
from rdo_agent.forensic_agent.types import CorrelationType

WINDOW = timedelta(days=30)

# Variação relativa entre os valores: < 10% é mesma negociação;
# > 80% é provavelmente item diferente, não renegociação.
MIN_RELATIVE_DIFF = 0.10
MAX_RELATIVE_DIFF = 0.80

# "Sweet spot" da renegociação real — variação significativa mas não
# absurda. Confidence mais alta quando dentro deste corredor.
STRONG_DIFF_LOWER = 0.20
STRONG_DIFF_UPPER = 0.70

# Confidences por tier de evidência semântica
CONF_STRONG = 0.85
CONF_MEDIUM = 0.70
# CONF_WEAK foi removido após observar falsos positivos com textos que
# compartilhavam apenas stems genéricos do PT-BR. Ancora obrigatoriamente
# em ≥1 stem HIGH (cobertur, telh, serralheria, sinal, saldo, ...).

DETECTOR_ID = "contract_renegotiation_v1"


def _max_aggregate_value(text: str) -> int | None:
    """
    Retorna o maior valor AGGREGATE/AMBIGUOUS mencionado em ``text``,
    ignorando UNITARY (R$/metro etc não pode bater com agregado).
    """
    mentions = [
        cents for cents, kind in extract_value_mentions(text)
        if kind != VALUE_KIND_UNITARY and cents > 0
    ]
    if not mentions:
        return None
    return max(mentions)


def _classify_renegotiation(
    shared_high: int, rel_diff: float,
) -> tuple[float, str] | None:
    """
    Decide o tier de confidence da correlação ou ``None`` se não atinge
    o critério mínimo (≥1 stem HIGH compartilhado). Retorna
    (confidence, evidence_label).
    """
    if shared_high >= 2 and STRONG_DIFF_LOWER <= rel_diff <= STRONG_DIFF_UPPER:
        return CONF_STRONG, f"{shared_high} stems HIGH, diff sweet-spot"
    if shared_high >= 1:
        return CONF_MEDIUM, f"{shared_high} stem(s) HIGH"
    return None


def _ref(event: EventText) -> str:
    return f"c_{event.classification_id}"


def _format_brl(cents: int) -> str:
    """R$X.XXX,YY a partir de centavos."""
    reais = cents // 100
    centavos = cents % 100
    with_sep = f"{reais:,}".replace(",", ".")
    return f"R${with_sep},{centavos:02d}"


def detect_contract_renegotiation(
    conn: sqlite3.Connection, obra: str,
    *, window: timedelta | None = None,
) -> list[Correlation]:
    """
    Emite correlações ``CONTRACT_RENEGOTIATION`` para a obra inteira.

    Implementação O(n²) sobre os classifications com valor mencionado.
    Em corpus piloto (EVERALDO ~250 classifications, ~10-20 com valor)
    isso é trivial. Para corpus maior considerar pré-filtragem por
    janela temporal mais restrita.

    Args:
        window: override do WINDOW default (30 dias). Sessao 10 (#50).
            Renegociacao tipica acontece em janela curta — janela menor
            reduz pares falsos positivos.
    """
    effective_window = window if window is not None else WINDOW

    events = [e for e in fetch_event_texts(conn, obra)
              if e.timestamp is not None]
    if not events:
        return []

    # Pre-extrai valor agregado max + tokens stemmed por evento.
    enriched: list[tuple[EventText, int, set[str]]] = []
    for ev in events:
        v = _max_aggregate_value(ev.text)
        if v is None:
            continue
        stems = tokenize(ev.text)
        if not stems:
            continue
        enriched.append((ev, v, stems))

    if len(enriched) < 2:
        return []

    out: list[Correlation] = []
    seen_pairs: set[tuple[int, int]] = set()  # dedup por par ordenado

    for i, (ev_a, v_a, stems_a) in enumerate(enriched):
        for ev_b, v_b, stems_b in enriched[i + 1 :]:
            # Ordenar por tempo: A antes, B depois
            if ev_a.timestamp == ev_b.timestamp:
                continue
            if ev_a.timestamp > ev_b.timestamp:
                first_ev, first_v, first_stems = ev_b, v_b, stems_b
                second_ev, second_v, second_stems = ev_a, v_a, stems_a
            else:
                first_ev, first_v, first_stems = ev_a, v_a, stems_a
                second_ev, second_v, second_stems = ev_b, v_b, stems_b

            delta = second_ev.timestamp - first_ev.timestamp
            if delta > effective_window:
                continue

            # Variação relativa: ambos > 0 garantido pelo extrator
            rel_diff = abs(second_v - first_v) / max(first_v, second_v)
            if rel_diff < MIN_RELATIVE_DIFF or rel_diff > MAX_RELATIVE_DIFF:
                continue

            shared = first_stems & second_stems
            shared_high = sum(1 for s in shared if s in HIGH_SPECIFICITY_STEMS)
            decision = _classify_renegotiation(shared_high, rel_diff)
            if decision is None:
                continue
            confidence, evidence_label = decision

            pair_key = (
                first_ev.classification_id, second_ev.classification_id,
            )
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            time_gap = int(delta.total_seconds())
            rationale = (
                f"renegociação detectada: {_format_brl(first_v)} → "
                f"{_format_brl(second_v)} (variação {rel_diff*100:.0f}%); "
                f"{evidence_label}"
            )
            out.append(Correlation(
                obra=obra,
                correlation_type=CorrelationType.CONTRACT_RENEGOTIATION.value,
                primary_event_ref=_ref(first_ev),
                primary_event_source="classification",
                related_event_ref=_ref(second_ev),
                related_event_source="classification",
                time_gap_seconds=time_gap,
                confidence=confidence,
                rationale=rationale,
                detected_by=DETECTOR_ID,
            ))
    return out


__all__ = [
    "CONF_MEDIUM",
    "CONF_STRONG",
    "DETECTOR_ID",
    "MAX_RELATIVE_DIFF",
    "MIN_RELATIVE_DIFF",
    "STRONG_DIFF_LOWER",
    "STRONG_DIFF_UPPER",
    "WINDOW",
    "detect_contract_renegotiation",
]
