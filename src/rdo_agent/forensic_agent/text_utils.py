"""
Utilities textuais para o agente forense.

Funções:

- ``smart_truncate``: truncamento por boundary (parágrafo > frase >
  palavra) com fallback. Adicionado na Sessão 4 como salvaguarda
  defensiva — auditoria mostrou que truncamento dumb (corte no
  caractere N) **não existia em produção** quando a dívida #36 foi
  originalmente registrada. Função fica disponível para callsites
  futuros (ex: persistência defensiva, fallback de API limit) sem ser
  invocada hoje.

- ``strip_emoji``: remove emojis de qualquer string. Defesa contra o
  modelo "furar" a regra do brandbook Vestígio (sem emojis em
  narrativa forense). Adicionado na Sessão 4 (#40). Aplicado pelo
  narrator antes de persistir e pelo adapter como rede para
  narrativas legacy.

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


# ---------------------------------------------------------------
# strip_emoji  (Sessão 4 · dívida #40)
# ---------------------------------------------------------------

# Ranges Unicode cobrindo as principais classes de emoji + símbolos
# decorativos. Compilado uma vez no módulo para perf.
#
# Cobertura:
#   - U+1F300–1F5FF  Misc symbols and pictographs
#   - U+1F600–1F64F  Emoticons (😀 etc)
#   - U+1F680–1F6FF  Transport and map symbols
#   - U+1F700–1F77F  Alchemical
#   - U+1F780–1F7FF  Geometric shapes extended
#   - U+1F800–1F8FF  Supplemental arrows-C
#   - U+1F900–1F9FF  Supplemental symbols and pictographs
#   - U+1FA00–1FA6F  Chess + symbols
#   - U+1FA70–1FAFF  Symbols and pictographs extended-A
#   - U+2600–26FF    Misc symbols (☀ ☁ ★)
#   - U+2700–27BF    Dingbats
#   - U+FE0F         Variation selector-16 (forces emoji rendering)
#   - U+200D         Zero-width joiner (em sequências como família 👨‍👩‍👧)
_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001f5ff"
    "\U0001f600-\U0001f64f"
    "\U0001f680-\U0001f6ff"
    "\U0001f700-\U0001f77f"
    "\U0001f780-\U0001f7ff"
    "\U0001f800-\U0001f8ff"
    "\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001fa6f"
    "\U0001fa70-\U0001faff"
    "☀-⛿"
    "✀-➿"
    "️"
    "‍"
    "]+",
    flags=re.UNICODE,
)


def strip_emoji(text: str) -> tuple[str, int]:
    """
    Remove emojis de ``text``. Retorna ``(texto_limpo, n_removidos)``.

    Conta cada *match* das ranges Unicode (sequências contíguas de
    emoji contam como 1 ocorrência cada). Caracteres normais PT-BR
    (acentos, ç, ñ, etc) ficam intactos.

    Notes
    -----
    Implementação por regex de ranges Unicode, não lista hardcoded.
    Pega ZWJ + variation selectors junto pra não deixar resíduo
    ("‍", "️") quando uma sequência composta de emoji é
    removida.
    """
    if not text:
        return text, 0
    matches = _EMOJI_RE.findall(text)
    if not matches:
        return text, 0
    cleaned = _EMOJI_RE.sub("", text)
    return cleaned, len(matches)
