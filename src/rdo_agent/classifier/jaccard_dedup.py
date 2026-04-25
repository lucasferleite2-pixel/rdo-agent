"""
JaccardDedup — dedup léxico simples para classify (Sessão 8 / #46).

Substitui sentence-transformers (dep pesada de ~2GB) por similaridade
de Jaccard sobre tokens. Pega 80% do ganho do dedup semântico em
corpus de WhatsApp pt-BR sem nenhuma dep nova.

Jaccard captura:

- Repetições com pontuação diferente: "ok", "ok!", "ok." → mesmo token set
- Variações de ordem: "ok blz", "blz ok" → mesmo set
- Pequenas adições: "ok" vs "ok valeu" → similaridade 0.5

Não captura paráfrases ("vou aí" vs "estou indo") — esse é o trabalho
da dívida #59 (sentence-transformers, registrada para futuro).

Threshold default 0.80 funciona bem para mensagens curtas; threshold
mais baixo (0.6) gera falsos positivos.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_THRESHOLD = 0.80

_TOKEN_RE = re.compile(r"[a-z0-9áéíóúâêôãõàç]+", re.IGNORECASE)

# Tokens muito comuns em português que não diferenciam intenção.
# Lista mínima — extender ofuscaria similaridade real em mensagens curtas.
_STOPWORDS: frozenset[str] = frozenset({
    "a", "o", "as", "os", "de", "do", "da", "dos", "das",
    "e", "ou", "que", "no", "na", "em", "para", "com",
    "eu", "tu", "ele", "ela", "voce", "vc",
})


def tokenize(text: str) -> set[str]:
    """
    Tokeniza string em set de tokens normalizados (lower, sem
    stopwords, len>=2). Determinístico — duas chamadas com mesmo
    input retornam mesmo set.
    """
    if not text:
        return set()
    raw = _TOKEN_RE.findall(text.lower())
    return {t for t in raw if len(t) >= 2 and t not in _STOPWORDS}


def jaccard(a: str, b: str) -> float:
    """
    Coeficiente de Jaccard entre dois textos. Retorna 0.0 se algum
    for vazio.
    """
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


# ---------------------------------------------------------------------------
# JaccardDedup com janela rolante
# ---------------------------------------------------------------------------


@dataclass
class _CandidateEntry:
    """Texto + label cacheada para lookup de similaridade."""

    text: str
    tokens: frozenset[str]
    label: object  # CachedLabel; tipo livre pra evitar import circular


class JaccardDedup:
    """
    Mantém **janela rolante** de últimas N mensagens classificadas
    e busca similaridade alta antes de chamar API.

    Args:
        threshold: similaridade mínima para considerar match (default 0.80).
        max_pool: tamanho da janela rolante. Default 500 — equilíbrio
            entre cobertura e custo de busca linear (busca é O(n) por query).
    """

    def __init__(
        self, *,
        threshold: float = DEFAULT_THRESHOLD,
        max_pool: int = 500,
    ):
        if not 0 < threshold <= 1.0:
            raise ValueError(f"threshold deve estar em (0,1]: {threshold}")
        if max_pool < 1:
            raise ValueError(f"max_pool deve ser >= 1: {max_pool}")
        self.threshold = threshold
        self.max_pool = max_pool
        self._pool: list[_CandidateEntry] = []

    def add(self, text: str, label) -> None:
        """Adiciona ``(text, label)`` ao pool. Evicta o mais antigo se cap."""
        if not text:
            return
        tokens = frozenset(tokenize(text))
        if not tokens:
            return  # texto vazio pos-tokenização; não vale guardar
        self._pool.append(
            _CandidateEntry(text=text, tokens=tokens, label=label),
        )
        if len(self._pool) > self.max_pool:
            self._pool.pop(0)

    def find_similar(self, text: str) -> object | None:
        """
        Busca melhor match no pool. Retorna ``label`` do candidato com
        maior similaridade ≥ threshold, ou ``None``.

        O(n) sobre pool — para max_pool=500 é trivial; se precisar
        escalar, dívida #59 (embeddings + ANN) substitui.
        """
        if not text:
            return None
        ta = tokenize(text)
        if not ta:
            return None

        best_label = None
        best_sim = self.threshold
        for entry in self._pool:
            inter = ta & entry.tokens
            if not inter:
                continue
            union = ta | entry.tokens
            sim = len(inter) / len(union)
            if sim >= best_sim:
                # Empate: o mais recente vence (já que é a iteração linear,
                # o último com sim >= best_sim acaba ganhando)
                best_sim = sim
                best_label = entry.label
        return best_label

    def size(self) -> int:
        return len(self._pool)

    def warm_from(
        self, entries: Iterable[tuple[str, object]],
    ) -> int:
        """
        Pré-popular o pool com entradas (texto, label). Útil pra
        hidratar o pool de uma sessão anterior. Retorna quantidade
        adicionada (texto vazio é ignorado).
        """
        n = 0
        for text, label in entries:
            self.add(text, label)
            if text:
                n += 1
        return n


__all__ = [
    "DEFAULT_THRESHOLD",
    "JaccardDedup",
    "jaccard",
    "tokenize",
]
