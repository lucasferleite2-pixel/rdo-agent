"""
Detector MATH_* — Sprint 5 Fase B.

Regra: para cada financial_record com valor_centavos, extrai valores
monetarios (regex R$X) dos textos de classifications e compara:

  - MATH_VALUE_MATCH (conf=1.0): |V_mencionado - V_pago| < R$1
  - MATH_INSTALLMENT_MATCH (conf=0.8): V_mencionado == V_pago/2 ou
    V_mencionado == V_pago*2 (sinal vs total, OR vs segunda metade)
  - MATH_VALUE_DIVERGENCE (conf=0.6): valor mencionado na faixa
    [0.5*V_pago, 1.5*V_pago] que NAO bateu exato nem installment
    (flag pra revisao humana: possivel divergencia de escopo/reajuste)

Tolerancia match exato: 100 centavos (R$1 — cobre arredondamento
de OCR e "R$3500" vs R$3.500,00).

Janela temporal: +-7 dias (mais larga que semantic — valores
contratuais podem ser mencionados dias antes/depois do pagamento
em discussoes de escopo, ou retomados em revisoes).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import timedelta

from rdo_agent.forensic_agent.correlator import Correlation
from rdo_agent.forensic_agent.detectors._common import (
    fetch_event_texts,
    fetch_financial_timestamps,
)
from rdo_agent.forensic_agent.types import CorrelationType

WINDOW = timedelta(hours=48)
"""
Janela temporal: +-48h em torno do financial_record. Escolhida apos
observar (divida #23) que a anterior +-7d correlacionava eventos com
gap de 77h, gerando ruido narrativo (um valor mencionado muito antes
ou muito depois raramente indica a mesma transacao quando ja ha
pagamento intermediario). Configuravel alterando esta constante.
"""

# R$ prefix obrigatorio pra reduzir falso-positivo (numeros random em
# texto). Captura: "R$3500", "R$ 3.500", "R$ 3.500,00", "R$3500,00".
VALUE_RE = re.compile(
    r"R\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d+(?:,\d{2})?)",
    re.IGNORECASE,
)

# Divida #26: diferenciar valor unitario (por m, por kg, cada)
# de agregado (total, fechou em, sem qualificador). MATH nao
# correlaciona UNITARY com financial_record (FR eh sempre agregado);
# AMBIGUOUS recebe penalidade na confidence.
VALUE_KIND_UNITARY = "unitary"
VALUE_KIND_AGGREGATE = "aggregate"
VALUE_KIND_AMBIGUOUS = "ambiguous"

# Patterns que indicam valor UNITARIO (preco por unidade/medida).
# Case-insensitive; incluem variacoes comuns em PT-BR.
_UNITARY_PATTERNS: tuple[str, ...] = (
    "/m", "/metro", "/kg", "/hora",
    "por metro", "por m ", "por m.",  # "por m " evita casar "por mês"
    "por kg", "por hora", "por dia", "por unidade",
    "cada", "unitario", "unitário", "por cabeça",
)

# Patterns que reforcam valor AGREGADO (total, sum, fechamento).
_AGGREGATE_PATTERNS: tuple[str, ...] = (
    "total", "soma", "somando",
    "fechou em", "fechamos em", "fechado em", "fechar em",
    "ficou em", "ficar em", "sai por", "sair por",
    "acerta em", "acertamos em", "pacote", "tudo em",
)

# Janela de contexto (chars antes/depois do match) pra classificar
VALUE_CONTEXT_CHARS = 20

# Penalidade de confidence quando o valor mencionado eh AMBIGUOUS
AMBIGUOUS_PENALTY = 0.2

# Tolerancia pra match exato: R$1 (100 centavos) — cobre arredondamentos
# de leitura OCR/formato.
EXACT_TOLERANCE_CENTS = 100

# Faixa de "divergencia suspeita": valor entre 50% e 150% do target
DIVERGENCE_LOWER = 0.5
DIVERGENCE_UPPER = 1.5

DETECTOR_ID = "math_v1"


def parse_brl_to_cents(raw: str) -> int | None:
    """
    Converte string BR ("3.500,00", "3500", "3500,50") pra centavos.

    Regras:
      - '.' = separador de milhar (removido)
      - ',' = separador decimal (virar '.')
      - sem ',': inteiro (ex: "3500" -> 350000 centavos)
      - com ',dd': decimal com 2 casas (ex: "3500,50" -> 350050)

    Retorna None se parse falha.
    """
    if not raw:
        return None
    clean = raw.strip().replace(".", "").replace(",", ".")
    try:
        return int(round(float(clean) * 100))
    except ValueError:
        return None


def classify_value_mention(
    text: str, start: int, end: int,
) -> str:
    """
    Classifica mencao de valor como UNITARY / AGGREGATE / AMBIGUOUS
    (divida #26) baseado em qualificadores na janela de contexto.

    Regras:
      - UNITARY_PATTERNS (exclusivo) => UNITARY
      - AGGREGATE_PATTERNS (exclusivo) => AGGREGATE
      - Ambos presentes => AMBIGUOUS
      - Nenhum => AGGREGATE (default; valor sem qualificador costuma
        ser total em negociacoes de obra)
    """
    if not text:
        return VALUE_KIND_AGGREGATE
    lo = max(0, start - VALUE_CONTEXT_CHARS)
    hi = min(len(text), end + VALUE_CONTEXT_CHARS)
    ctx = text[lo:hi].lower()
    has_unit = any(p in ctx for p in _UNITARY_PATTERNS)
    has_agg = any(p in ctx for p in _AGGREGATE_PATTERNS)
    if has_unit and has_agg:
        return VALUE_KIND_AMBIGUOUS
    if has_unit:
        return VALUE_KIND_UNITARY
    return VALUE_KIND_AGGREGATE


def extract_value_mentions(text: str) -> list[tuple[int, str]]:
    """
    Retorna [(cents, kind), ...] — valores + classificacao unit/agg/amb.
    Valores duplicados (mesmo cents + mesmo kind) sao deduplicados.
    """
    if not text:
        return []
    seen: set[tuple[int, str]] = set()
    out: list[tuple[int, str]] = []
    for m in VALUE_RE.finditer(text):
        cents = parse_brl_to_cents(m.group(1))
        if cents is None or cents <= 0:
            continue
        kind = classify_value_mention(text, m.start(), m.end())
        key = (cents, kind)
        if key in seen:
            continue
        seen.add(key)
        out.append((cents, kind))
    return out


def extract_values_cents(text: str) -> list[int]:
    """
    Retorna lista de valores (em centavos) mencionados no texto via R$.

    Retrocompat: API antiga que ignora o kind. Preserva ordem de
    primeira aparicao e **inclui duplicatas** no mesmo texto (o caller
    pode precisar). Para uso novo prefira `extract_value_mentions`.
    """
    if not text:
        return []
    values: list[int] = []
    for match in VALUE_RE.findall(text):
        cents = parse_brl_to_cents(match)
        if cents is not None and cents > 0:
            values.append(cents)
    return values


def _classify_match(
    mentioned_cents: int, target_cents: int,
) -> tuple[str, float] | None:
    """
    Classifica tipo de match. Retorna (correlation_type, confidence) ou
    None se fora de qualquer faixa de interesse.
    """
    diff = abs(mentioned_cents - target_cents)
    if diff < EXACT_TOLERANCE_CENTS:
        return (CorrelationType.MATH_VALUE_MATCH.value, 1.0)
    # Installment: metade ou dobro (tolerancia R$1 em cada ponta)
    half_diff = abs(mentioned_cents - target_cents // 2)
    double_diff = abs(mentioned_cents - target_cents * 2)
    if (half_diff < EXACT_TOLERANCE_CENTS
            or double_diff < EXACT_TOLERANCE_CENTS):
        return (CorrelationType.MATH_INSTALLMENT_MATCH.value, 0.8)
    # Divergencia suspeita: dentro da faixa mas nao bateu exato/parcial
    if (DIVERGENCE_LOWER * target_cents
            <= mentioned_cents
            <= DIVERGENCE_UPPER * target_cents):
        return (CorrelationType.MATH_VALUE_DIVERGENCE.value, 0.6)
    return None


def detect_math_relations(
    conn: sqlite3.Connection, obra: str,
) -> list[Correlation]:
    """
    Emite MATH_VALUE_MATCH / MATH_INSTALLMENT_MATCH / MATH_VALUE_DIVERGENCE.

    Uma Correlation por match (mesmo que varios valores de uma mesma
    classification batam — cada par vira uma linha).

    Financial_records sem valor_centavos ou timestamp sao ignorados.
    """
    frs = [fe for fe in fetch_financial_timestamps(conn, obra)
           if fe.valor_centavos is not None and fe.valor_centavos > 0
           and fe.timestamp is not None]
    if not frs:
        return []
    events = [e for e in fetch_event_texts(conn, obra)
              if e.timestamp is not None]
    if not events:
        return []

    # Pre-extrai (valor, kind) de cada classification. extract_value_mentions
    # ja dedupa (cents, kind) — divida #22 resolvida automaticamente.
    # Divida #26: ignoramos UNITARY aqui porque financial_record eh
    # sempre valor agregado (um PIX pago); um "R\$50/metro" nao pode
    # bater com o valor pago em um PIX.
    event_mentions: list[tuple[int, list[tuple[int, str]]]] = []
    for i, ev in enumerate(events):
        mentions = [
            (cents, kind) for cents, kind in extract_value_mentions(ev.text)
            if kind != VALUE_KIND_UNITARY
        ]
        if mentions:
            event_mentions.append((i, mentions))

    out: list[Correlation] = []
    for fr in frs:
        lo = fr.timestamp - WINDOW
        hi = fr.timestamp + WINDOW
        target = fr.valor_centavos
        assert target is not None  # garantido pelo filtro acima
        for idx, mentions in event_mentions:
            ev = events[idx]
            if ev.timestamp < lo or ev.timestamp > hi:
                continue
            # Uma classification pode mencionar varios valores — emite
            # uma Correlation por valor que bate.
            for v, kind in mentions:
                result = _classify_match(v, target)
                if result is None:
                    continue
                ctype, confidence = result
                # Divida #26: AMBIGUOUS mentions recebem penalidade
                # (nao temos certeza se o valor mencionado eh total).
                if kind == VALUE_KIND_AMBIGUOUS:
                    confidence = max(0.0, confidence - AMBIGUOUS_PENALTY)
                delta = int((ev.timestamp - fr.timestamp).total_seconds())
                mentioned_brl = v / 100
                target_brl = target / 100
                kind_tag = (
                    f" [kind={kind}]"
                    if kind != VALUE_KIND_AGGREGATE else ""
                )
                out.append(Correlation(
                    obra=obra,
                    correlation_type=ctype,
                    primary_event_ref=f"fr_{fr.financial_id}",
                    primary_event_source="financial_record",
                    related_event_ref=f"c_{ev.classification_id}",
                    related_event_source="classification",
                    time_gap_seconds=delta,
                    confidence=confidence,
                    rationale=(
                        f"valor mencionado R${mentioned_brl:.2f} vs pago "
                        f"R${target_brl:.2f} (delta={delta:+d}s)"
                        f"{kind_tag}"
                    ),
                    detected_by=DETECTOR_ID,
                ))
    return out


__all__ = [
    "AMBIGUOUS_PENALTY",
    "DETECTOR_ID",
    "DIVERGENCE_LOWER",
    "DIVERGENCE_UPPER",
    "EXACT_TOLERANCE_CENTS",
    "VALUE_CONTEXT_CHARS",
    "VALUE_KIND_AGGREGATE",
    "VALUE_KIND_AMBIGUOUS",
    "VALUE_KIND_UNITARY",
    "VALUE_RE",
    "WINDOW",
    "classify_value_mention",
    "detect_math_relations",
    "extract_value_mentions",
    "extract_values_cents",
    "parse_brl_to_cents",
]
