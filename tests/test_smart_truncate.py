"""Testes de smart_truncate (Sessao 4, divida #36).

Cobre os 4 boundaries (paragrafo, frase, palavra, hard) e o no-op
quando texto cabe no limite.
"""

from __future__ import annotations

import logging

import pytest

from rdo_agent.forensic_agent.text_utils import (
    TRUNCATION_MARKER,
    smart_truncate,
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
