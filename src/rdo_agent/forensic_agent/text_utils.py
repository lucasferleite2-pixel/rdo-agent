"""
Utilities textuais para o agente forense.

Implementação inicial: ``smart_truncate``, truncamento por boundary
(parágrafo > frase > palavra) com fallback. Adicionado na Sessão 4
como salvaguarda defensiva — auditoria mostrou que truncamento dumb
(corte no caractere N) **não existia em produção** quando a dívida #36
foi originalmente registrada. Função fica disponível para callsites
futuros (ex: persistência defensiva, fallback de API limit) sem ser
invocada hoje.

Critério de boundary, em ordem de preferência:

1. **Parágrafo**: corta no último ``\\n\\n`` antes do limite.
2. **Frase**: corta no último ``. `` / ``! `` / ``? `` antes do limite,
   preservando o terminador.
3. **Palavra**: corta no último espaço antes do limite.
4. **Hard**: corte no caractere exato (último recurso).

Sempre acrescenta marker ``\\n\\n[truncado por limite]`` no fim quando
o corte ocorreu, para sinalizar visualmente no laudo/RDO.
"""

from __future__ import annotations

import re

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

TRUNCATION_MARKER: str = "\n\n[truncado por limite]"

# Boundaries de frase: ponto/exclamação/interrogação seguido de espaço
# ou fim de linha. Mantém o terminador no resultado truncado.
_SENTENCE_BOUNDARY = re.compile(r"[.!?](?=\s|$)")


def smart_truncate(text: str, max_chars: int) -> str:
    """
    Trunca ``text`` para no máximo ``max_chars`` caracteres preservando
    boundary natural quando possível.

    Returns
    -------
    str
        Texto truncado (com ``TRUNCATION_MARKER`` apenso) se corte foi
        necessário; texto inalterado se já está dentro do limite.

    Raises
    ------
    ValueError
        Se ``max_chars`` for menor que ``len(TRUNCATION_MARKER)``
        (não há espaço pra colocar o marker).
    """
    if max_chars <= len(TRUNCATION_MARKER):
        raise ValueError(
            f"max_chars ({max_chars}) precisa ser > len(marker) "
            f"({len(TRUNCATION_MARKER)})"
        )

    if len(text) <= max_chars:
        return text

    # Espaço útil descontando o marker que será apenso.
    budget = max_chars - len(TRUNCATION_MARKER)
    head = text[:budget]
    boundary, kind = _find_boundary(head)
    truncated = head[:boundary]
    log.warning(
        "smart_truncate aplicado: %d -> %d chars (boundary=%s)",
        len(text), len(truncated) + len(TRUNCATION_MARKER), kind,
    )
    return truncated + TRUNCATION_MARKER


def _find_boundary(head: str) -> tuple[int, str]:
    """
    Procura o melhor boundary em ``head`` (já cortado no budget máximo).

    Retorna ``(index, kind)`` onde ``kind`` ∈
    {'paragraph', 'sentence', 'word', 'hard'}.
    """
    # 1. Parágrafo: último \n\n
    idx = head.rfind("\n\n")
    if idx > 0:
        return idx, "paragraph"

    # 2. Frase: último . / ! / ? seguido de espaço/fim
    sentence_idx = -1
    for m in _SENTENCE_BOUNDARY.finditer(head):
        sentence_idx = m.end()  # inclui o terminador
    if sentence_idx > 0:
        return sentence_idx, "sentence"

    # 3. Palavra: último espaço
    idx = head.rfind(" ")
    if idx > 0:
        return idx, "word"

    # 4. Hard cut (string sem nenhum boundary natural).
    return len(head), "hard"
