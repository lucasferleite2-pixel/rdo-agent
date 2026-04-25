"""
Detector SEMANTIC_PAYMENT_SCOPE — Sprint 5 Fase B.

Regra: para cada financial_record com `descricao` nao-vazia, tokeniza +
stemma + remove stopwords -> conjunto S_fr. Para cada classification
com timestamp em +-3 dias da data do financial_record, tokeniza o texto
-> S_cls. Se |S_fr & S_cls| >= 2, emite Correlation com confidence
proporcional ao overlap.

Stack de normalizacao (tudo stdlib, zero deps extras):
  - `unicodedata.normalize('NFKD', s)` + strip latin accents
  - lower()
  - split em nao-alfanumericos via regex
  - remove stopwords PT (lista curta custom)
  - stemming trivial: strip de sufixos PT comuns
    ('cao','coes','mento','mentos','dade','dades','ar','er','ir',
     'ando','endo','indo','ada','ado','ados','adas','s','ns')

A janela +-3 dias existe pra contexto conversacional largo:
discussoes de escopo frequentemente antecedem o pagamento por dias e
se estendem depois.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import timedelta

from rdo_agent.forensic_agent.correlator import Correlation
from rdo_agent.forensic_agent.detectors._common import (
    fetch_event_texts,
    fetch_financial_timestamps,
)
from rdo_agent.forensic_agent.types import CorrelationType

WINDOW = timedelta(days=3)

# Pequena lista PT de stopwords focada no dominio (mensagens WhatsApp
# curtas, objetivas). Nao eh NLTK-completa — so as mais comuns que
# poluiriam overlaps.
STOPWORDS: frozenset[str] = frozenset({
    "a", "o", "as", "os", "um", "uma", "uns", "umas",
    "e", "ou", "mas", "que", "de", "do", "da", "dos", "das",
    "em", "no", "na", "nos", "nas", "para", "por", "com", "sem",
    "ao", "aos", "pelo", "pela", "pelos", "pelas",
    "eu", "tu", "ele", "ela", "nos", "voces", "eles", "elas",
    "meu", "minha", "seu", "sua", "ser", "ter", "estar",
    "foi", "era", "sao", "esta", "estao", "tem", "tinha",
    "ja", "nao", "sim", "so", "muito", "mais", "menos", "tudo",
    "isso", "isto", "aquilo", "esse", "essa", "este", "esta",
    "aqui", "ali", "la", "agora", "hoje", "ontem", "amanha",
    "se", "aquele", "aquela", "como", "quando", "onde",
    "qual", "quanto", "quantos", "quantas",
    "vai", "vou", "vem", "vou", "va", "foi",
    "fiz", "faz", "fazer", "ficar", "pode", "poder",
    "entao", "ainda", "sempre", "nunca", "so",
    "tao", "bem", "mal", "talvez", "ate", "bom", "boa", "otimo",
    "tipo", "coisa", "pessoa", "jeito", "vez", "hora", "dia",
    # tokens curtos frequentes que poluem overlap
    "pra", "pro", "de", "te", "me", "lhe", "nos", "vos",
})

# Sufixos PT comuns — ordem IMPORTA (maior primeiro, sufixos compostos
# antes de sufixos simples)
_SUFFIXES: tuple[str, ...] = (
    "mentos", "mento",
    "coes", "cao",
    "dades", "dade",
    "ados", "adas", "idos", "idas", "ada", "ado", "ida", "ido",
    "ando", "endo", "indo",
    "aram", "eram", "iram",
    "ar", "er", "ir",
    "ns", "s",
)

# Tokens muito curtos pos-stemming sao rejeitados (ruido)
MIN_TOKEN_LEN = 3

# Divida #24: tuning com keyword weights + time decay
#
# Problema baseline: conf media 0.50 (concentrada em 0.4 quando overlap=2).
# Apenas 16.7% das correlacoes SEMANTIC validavam (>=0.70).
#
# Fix: tokens especificos do dominio (pagamento, contrato, materiais de
# obra) pesam mais; tokens genericos (servico, trabalho, coisa) pesam
# menos; correlacoes mais longe temporalmente decaem linearmente.

# Peso padrao (tokens "medios")
TOKEN_WEIGHT_DEFAULT = 1.0
# Peso de tokens de ALTA especificidade (indicam estrutura financeira/contratual
# ou atividade tecnica concreta da obra)
TOKEN_WEIGHT_HIGH = 1.5
# Peso de tokens de BAIXA especificidade (comercio/trabalho genericos —
# qualquer conversa sobre obra usa)
TOKEN_WEIGHT_LOW = 0.7

# Tokens stemmed de alta especificidade (apos passar por `_stem`). A lista
# eh curta e reflete vocabulario diretamente mapeavel ao domínio: atividade
# de construcao (serralheria, telhado, alambrado, instalar), pagamento
# (sinal, saldo, metade, pix, comprovant) e acabamentos (fechament,
# tesoura, terca, ripament).
HIGH_SPECIFICITY_STEMS: frozenset[str] = frozenset({
    # pagamento / contrato
    "sinal", "saldo", "metad", "pix", "comprovant", "parcel",
    # atividade tecnica
    "serralheria", "telh", "alambrad", "tesoura", "terca", "ripament",
    "fecha",  # stemma de 'fechamento' / 'fechar' (contratual/estrutural)
    "instal",  # 'instalar', 'instalacao'
    "sub",     # 'subir', relevante em serralheria
    "cobertur", "estrutur", "esquelet",
})

# Tokens stemmed de baixa especificidade (ruido contextual — qualquer
# conversa de obra menciona)
LOW_SPECIFICITY_STEMS: frozenset[str] = frozenset({
    "servico", "trabalh", "obra", "material", "pessoa", "equip",
    "coisa", "jeit", "parte", "situacao", "negoci",
})

# Saturation ponderada: somatorio de weights atinge essa marca => 1.0
# base (antes do decay). Com MIN_OVERLAP=2 HIGH (2x1.5=3.0), isso da
# base 3.0/4.0=0.75 — combinado com decay ~1 perto do centro cruza o
# threshold de validacao (0.70).
CONFIDENCE_SATURATION_WEIGHTED = 4.0

# Time decay: linear de 1.0 (gap 0) a TIME_DECAY_FLOOR (gap=WINDOW).
# Calibrado em 0.7 apos medir corpus real (deltas tipicos 1.5-1.9 dias
# em janela de 3 dias); decay 0.5 eh severo demais e impede validacao
# de matches semanticamente fortes com gap moderado.
TIME_DECAY_FLOOR = 0.7

# Minimo de overlap pra emitir correlation (inalterado)
MIN_OVERLAP = 2

DETECTOR_ID = "semantic_v2"  # bump versão pra refletir tuning (#24)


def _strip_accents(s: str) -> str:
    """NFKD + remove combining marks (acentos latinos)."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _stem(token: str) -> str:
    """Stemming PT trivial: strip longest matching suffix de _SUFFIXES."""
    for suf in _SUFFIXES:
        if len(token) > len(suf) + 2 and token.endswith(suf):
            return token[: -len(suf)]
    return token


def _token_weight(stem: str) -> float:
    """Peso de um token stemmed para calculo de confidence (#24)."""
    if stem in HIGH_SPECIFICITY_STEMS:
        return TOKEN_WEIGHT_HIGH
    if stem in LOW_SPECIFICITY_STEMS:
        return TOKEN_WEIGHT_LOW
    return TOKEN_WEIGHT_DEFAULT


def _time_decay(delta_seconds: int, window_seconds: int) -> float:
    """
    Fator de decay linear no intervalo [TIME_DECAY_FLOOR, 1.0].
    |delta|=0 => 1.0; |delta|=window => TIME_DECAY_FLOOR.
    """
    frac = min(abs(delta_seconds) / window_seconds, 1.0)
    return 1.0 - (1.0 - TIME_DECAY_FLOOR) * frac


def _weighted_confidence(
    shared_stems: set[str], delta_seconds: int, window_seconds: int,
) -> float:
    """
    Divida #24: confidence = (soma de pesos) / SATURATION * time_decay,
    cap em 1.0.
    """
    total_weight = sum(_token_weight(t) for t in shared_stems)
    base = min(total_weight / CONFIDENCE_SATURATION_WEIGHTED, 1.0)
    return base * _time_decay(delta_seconds, window_seconds)


def tokenize(text: str) -> set[str]:
    """
    Retorna conjunto de tokens normalizados (accent-stripped, lower,
    stemmed, sem stopwords, len>=MIN_TOKEN_LEN).

    Publico pra testes e pra reusar em outros detectores se preciso.
    """
    if not text:
        return set()
    plain = _strip_accents(text).lower()
    raw = _TOKEN_RE.findall(plain)
    out: set[str] = set()
    for tok in raw:
        if tok in STOPWORDS or len(tok) < MIN_TOKEN_LEN:
            continue
        stem = _stem(tok)
        if len(stem) >= MIN_TOKEN_LEN:
            out.add(stem)
    return out


def detect_semantic_payment_scope(
    conn: sqlite3.Connection, obra: str,
    *, window: timedelta | None = None,
) -> list[Correlation]:
    """
    Emite SEMANTIC_PAYMENT_SCOPE para a obra inteira.

    Para cada financial_record com descricao e timestamp (precisa da
    data pra janela +-window), compara conjunto-de-termos com classifications
    do periodo. Overlap >=2 => emite Correlation.

    Args:
        window: override do WINDOW default (3 dias). Sessao 10 (#50).
            time_decay (linear de 1.0 → TIME_DECAY_FLOOR=0.7) escala
            com a janela escolhida.
    """
    effective_window = window if window is not None else WINDOW

    frs = [fe for fe in fetch_financial_timestamps(conn, obra)
           if fe.timestamp is not None and (fe.descricao or "").strip()]
    if not frs:
        return []
    events = [e for e in fetch_event_texts(conn, obra)
              if e.timestamp is not None]
    if not events:
        return []

    # Pre-tokeniza classifications uma vez (reuso entre financial_records)
    event_tokens: list[tuple[int, set[str]]] = [
        (i, tokenize(ev.text)) for i, ev in enumerate(events)
    ]

    window_seconds = int(effective_window.total_seconds())
    out: list[Correlation] = []
    for fr in frs:
        fr_tokens = tokenize(fr.descricao or "")
        if len(fr_tokens) < MIN_OVERLAP:
            # descricao com <2 termos uteis: impossivel overlap >=2
            continue
        lo = fr.timestamp - effective_window
        hi = fr.timestamp + effective_window
        for idx, cls_tokens in event_tokens:
            ev = events[idx]
            if ev.timestamp < lo or ev.timestamp > hi:
                continue
            shared = fr_tokens & cls_tokens
            n = len(shared)
            if n < MIN_OVERLAP:
                continue
            delta = int((ev.timestamp - fr.timestamp).total_seconds())
            confidence = _weighted_confidence(
                shared, delta, window_seconds,
            )
            shared_sorted = sorted(shared)
            sample = shared_sorted[:5]
            # Explica quais sao HIGH/LOW pra auditabilidade forense
            shared_annotated = ",".join(
                f"{t}*" if t in HIGH_SPECIFICITY_STEMS
                else f"{t}~" if t in LOW_SPECIFICITY_STEMS
                else t
                for t in sample
            )
            rationale = (
                f"overlap de {n} termo(s): {shared_annotated}"
                f"{'...' if n > 5 else ''} "
                f"(delta={delta:+d}s, "
                f"decay={_time_decay(delta, window_seconds):.2f})"
            )
            out.append(Correlation(
                obra=obra,
                correlation_type=CorrelationType.SEMANTIC_PAYMENT_SCOPE.value,
                primary_event_ref=f"fr_{fr.financial_id}",
                primary_event_source="financial_record",
                related_event_ref=f"c_{ev.classification_id}",
                related_event_source="classification",
                time_gap_seconds=delta,
                confidence=confidence,
                rationale=rationale,
                detected_by=DETECTOR_ID,
            ))
    return out


__all__ = [
    "CONFIDENCE_SATURATION_WEIGHTED",
    "DETECTOR_ID",
    "HIGH_SPECIFICITY_STEMS",
    "LOW_SPECIFICITY_STEMS",
    "MIN_OVERLAP",
    "MIN_TOKEN_LEN",
    "STOPWORDS",
    "TIME_DECAY_FLOOR",
    "TOKEN_WEIGHT_DEFAULT",
    "TOKEN_WEIGHT_HIGH",
    "TOKEN_WEIGHT_LOW",
    "WINDOW",
    "detect_semantic_payment_scope",
    "tokenize",
]
