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

# Overlap saturation: overlap >= 5 => confidence 1.0
CONFIDENCE_OVERLAP_MAX = 5

# Minimo de overlap pra emitir correlation
MIN_OVERLAP = 2

DETECTOR_ID = "semantic_v1"


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
) -> list[Correlation]:
    """
    Emite SEMANTIC_PAYMENT_SCOPE para a obra inteira.

    Para cada financial_record com descricao e timestamp (precisa da
    data pra janela +-3d), compara conjunto-de-termos com classifications
    do periodo. Overlap >=2 => emite Correlation.
    """
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

    out: list[Correlation] = []
    for fr in frs:
        fr_tokens = tokenize(fr.descricao or "")
        if len(fr_tokens) < MIN_OVERLAP:
            # descricao com <2 termos uteis: impossivel overlap >=2
            continue
        lo = fr.timestamp - WINDOW
        hi = fr.timestamp + WINDOW
        for idx, cls_tokens in event_tokens:
            ev = events[idx]
            if ev.timestamp < lo or ev.timestamp > hi:
                continue
            shared = fr_tokens & cls_tokens
            n = len(shared)
            if n < MIN_OVERLAP:
                continue
            confidence = min(n / CONFIDENCE_OVERLAP_MAX, 1.0)
            delta = int((ev.timestamp - fr.timestamp).total_seconds())
            shared_sorted = sorted(shared)
            sample = shared_sorted[:5]
            rationale = (
                f"overlap de {n} termo(s): {','.join(sample)}"
                f"{'...' if n > 5 else ''} (delta={delta:+d}s)"
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
    "CONFIDENCE_OVERLAP_MAX",
    "DETECTOR_ID",
    "MIN_OVERLAP",
    "MIN_TOKEN_LEN",
    "STOPWORDS",
    "WINDOW",
    "detect_semantic_payment_scope",
    "tokenize",
]
