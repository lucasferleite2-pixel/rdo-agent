"""Testes de smart_truncate (Sessao 4, divida #36) e strip_emoji
(Sessao 4, divida #40).

Cobre:
  - 4 boundaries (paragrafo, frase, palavra, hard) + no-op + log
  - strip_emoji em emojis basicos, preservacao de PT-BR, contagem,
    edge cases
"""

from __future__ import annotations

import logging

import pytest

from rdo_agent.forensic_agent.text_utils import (
    TRUNCATION_MARKER,
    smart_truncate,
    strip_emoji,
)


def test_smart_truncate_under_limit_returns_unchanged():
    text = "Texto curto."
    assert smart_truncate(text, max_chars=100) == text


def test_smart_truncate_preserves_paragraphs():
    """Boundary preferencial: corte em \\n\\n."""
    text = (
        "Primeiro paragrafo curto.\n\n"
        "Segundo paragrafo que excede o limite e nao deve aparecer no "
        "resultado truncado de jeito nenhum."
    )
    out = smart_truncate(text, max_chars=50)
    assert out.endswith(TRUNCATION_MARKER)
    # Tudo antes do marker deve ser exatamente o primeiro paragrafo
    body = out[: -len(TRUNCATION_MARKER)]
    assert body == "Primeiro paragrafo curto."


def test_smart_truncate_falls_back_to_sentence():
    """Sem \\n\\n disponivel, corta em . ! ?"""
    text = (
        "Primeira frase. Segunda frase mais comprida. "
        "Terceira frase nao deveria aparecer porque excede limite."
    )
    out = smart_truncate(text, max_chars=60)
    assert out.endswith(TRUNCATION_MARKER)
    body = out[: -len(TRUNCATION_MARKER)]
    # Deve terminar com '.' (ultima frase preservada inteira)
    assert body.endswith(".")
    # E nao deve conter "Terceira frase"
    assert "Terceira frase" not in body


def test_smart_truncate_falls_back_to_word():
    """Sem boundaries de frase ou paragrafo, corta em espaco."""
    text = (
        "palavra1 palavra2 palavra3 palavra4 palavra5 "
        "palavra6 palavra7 palavra8 palavra9"
    )
    out = smart_truncate(text, max_chars=40)
    assert out.endswith(TRUNCATION_MARKER)
    body = out[: -len(TRUNCATION_MARKER)]
    # Nao deve cortar palavra ao meio
    assert not body.endswith(("palavra", "palavr", "palav"))
    # Deve terminar com palavra inteira (sem espaco trailing apos rstrip implicito)
    assert body.endswith(tuple(f"palavra{i}" for i in range(1, 10)))


def test_smart_truncate_hard_cut_when_no_boundary():
    """String sem espacos nem pontuacao: hard cut no caractere exato."""
    text = "x" * 200
    out = smart_truncate(text, max_chars=50)
    assert out.endswith(TRUNCATION_MARKER)
    body = out[: -len(TRUNCATION_MARKER)]
    assert len(body) == 50 - len(TRUNCATION_MARKER)
    assert body == "x" * len(body)


def test_smart_truncate_logs_warning(caplog):
    """smart_truncate deve logar warning quando trunca."""
    text = "Lorem ipsum dolor sit amet. " * 50
    with caplog.at_level(logging.WARNING, logger="rdo_agent.forensic_agent.text_utils"):
        smart_truncate(text, max_chars=100)
    assert any("smart_truncate aplicado" in rec.message for rec in caplog.records)


def test_smart_truncate_no_warning_when_under_limit(caplog):
    """No-op nao deve logar."""
    with caplog.at_level(logging.WARNING, logger="rdo_agent.forensic_agent.text_utils"):
        smart_truncate("texto curto", max_chars=100)
    assert not any("smart_truncate" in rec.message for rec in caplog.records)


def test_smart_truncate_max_chars_too_small_raises():
    """Limite menor que o marker nao tem espaco util."""
    with pytest.raises(ValueError, match="precisa ser >"):
        smart_truncate("qualquer texto", max_chars=5)


def test_smart_truncate_marker_value():
    """Marker e exatamente o esperado (string conhecida)."""
    assert TRUNCATION_MARKER == "\n\n[truncado por limite]"


# ============================================================
# strip_emoji  (Sessao 4 · divida #40)
# ============================================================


def test_strip_emoji_basic():
    """Emojis basicos sao removidos."""
    text = "Reuniao com cliente 😀 fechou bem 🎉👍"
    cleaned, n = strip_emoji(text)
    assert "😀" not in cleaned
    assert "🎉" not in cleaned
    assert "👍" not in cleaned
    assert "Reuniao com cliente" in cleaned
    assert "fechou bem" in cleaned
    assert n >= 1  # pelo menos 1 sequencia detectada


def test_strip_emoji_preserves_unicode_text():
    """Acentos PT-BR, c-cedilha, n-til ficam intactos."""
    text = "Acordo verbal: cronograma apertado, executar até quinta. Pendência: prazo do irmão."
    cleaned, n = strip_emoji(text)
    assert n == 0
    assert cleaned == text
    # Caracteres especificos preservados
    for char in "áéíóúâêôãõàçñ":
        # nao todos aparecem no texto, mas o ponto eh testar individualmente
        cleaned2, _ = strip_emoji(char)
        assert cleaned2 == char


def test_strip_emoji_returns_count():
    """Counter retorna numero de sequencias removidas (nao caracteres)."""
    text = "a 🎉🎊 b 👍 c"
    cleaned, n = strip_emoji(text)
    assert cleaned == "a  b  c"
    # 2 sequencias contiguas: '🎉🎊' (1) + '👍' (1)
    assert n == 2


def test_strip_emoji_empty_returns_empty():
    """String vazia devolve vazio + count 0."""
    cleaned, n = strip_emoji("")
    assert cleaned == ""
    assert n == 0


def test_strip_emoji_no_emoji_returns_unchanged():
    """Texto sem emoji passa intacto, count 0."""
    text = "Narrativa forense sem decoração."
    cleaned, n = strip_emoji(text)
    assert cleaned == text
    assert n == 0


def test_strip_emoji_handles_zwj_sequences():
    """Sequencias compostas com ZWJ (👨‍👩‍👧) sao removidas inteiras."""
    text = "Familia 👨‍👩‍👧 reunida"
    cleaned, _ = strip_emoji(text)
    assert "👨" not in cleaned
    assert "👩" not in cleaned
    assert "👧" not in cleaned
    assert "‍" not in cleaned  # ZWJ tambem some
    assert "Familia" in cleaned and "reunida" in cleaned


def test_strip_emoji_handles_misc_symbols():
    """Misc symbols ranges (☀, ★, ⚠) tambem sao tratados."""
    text = "Atenção ⚠ pendencia ★ sol ☀"
    cleaned, n = strip_emoji(text)
    assert "⚠" not in cleaned
    assert "★" not in cleaned
    assert "☀" not in cleaned
    assert n >= 3
    assert "Atenção" in cleaned
