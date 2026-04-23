"""Testes do tuning #24 do SEMANTIC detector (Sessao 2, Sprint 5)."""

from __future__ import annotations

import pytest

from rdo_agent.forensic_agent.detectors.semantic import (
    CONFIDENCE_SATURATION_WEIGHTED,
    HIGH_SPECIFICITY_STEMS,
    LOW_SPECIFICITY_STEMS,
    TIME_DECAY_FLOOR,
    TOKEN_WEIGHT_DEFAULT,
    TOKEN_WEIGHT_HIGH,
    TOKEN_WEIGHT_LOW,
    WINDOW,
    _time_decay,
    _token_weight,
    _weighted_confidence,
)

WINDOW_S = int(WINDOW.total_seconds())


# ---------------------------------------------------------------------------
# _token_weight
# ---------------------------------------------------------------------------


def test_token_weight_high_specificity_stems_return_high():
    # stem de vocabulario domínio-especifico (contrato / serralheria)
    for stem in ("sinal", "saldo", "telh", "serralheria", "pix"):
        assert stem in HIGH_SPECIFICITY_STEMS
        assert _token_weight(stem) == TOKEN_WEIGHT_HIGH


def test_token_weight_low_specificity_stems_return_low():
    for stem in ("servico", "trabalh", "obra", "coisa"):
        assert stem in LOW_SPECIFICITY_STEMS
        assert _token_weight(stem) == TOKEN_WEIGHT_LOW


def test_token_weight_unknown_stem_returns_default():
    # stem que nao esta em nenhuma das listas
    assert _token_weight("xyz123") == TOKEN_WEIGHT_DEFAULT


# ---------------------------------------------------------------------------
# _time_decay
# ---------------------------------------------------------------------------


def test_time_decay_zero_gap_is_full():
    assert _time_decay(0, WINDOW_S) == pytest.approx(1.0)


def test_time_decay_at_window_limit_reaches_floor():
    assert _time_decay(WINDOW_S, WINDOW_S) == pytest.approx(TIME_DECAY_FLOOR)
    # negativo idem (abs)
    assert _time_decay(-WINDOW_S, WINDOW_S) == pytest.approx(TIME_DECAY_FLOOR)


def test_time_decay_half_window_mid():
    # linear: meia janela => 1.0 - 0.5 * (1.0 - FLOOR) = 1 - 0.25 = 0.75
    expected = 1.0 - 0.5 * (1.0 - TIME_DECAY_FLOOR)
    assert _time_decay(WINDOW_S // 2, WINDOW_S) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# _weighted_confidence — matriz de casos
# ---------------------------------------------------------------------------


def test_weighted_conf_two_high_terms_gap_zero_validates():
    """Meta #24: 2 HIGH stems em gap pequeno >= 0.70."""
    conf = _weighted_confidence({"telh", "serralheria"}, 0, WINDOW_S)
    # 2 * 1.5 / 4.0 = 0.75, decay 1.0 => 0.75
    assert conf >= 0.70
    assert conf == pytest.approx(
        (2 * TOKEN_WEIGHT_HIGH / CONFIDENCE_SATURATION_WEIGHTED), rel=1e-3,
    )


def test_weighted_conf_two_low_terms_stays_below_validation():
    """LOW/LOW stems NAO devem validar (ruido)."""
    conf = _weighted_confidence({"servico", "trabalh"}, 0, WINDOW_S)
    # 2 * 0.7 / 4.0 = 0.35
    assert conf < 0.70
    assert conf == pytest.approx(
        (2 * TOKEN_WEIGHT_LOW / CONFIDENCE_SATURATION_WEIGHTED), rel=1e-3,
    )


def test_weighted_conf_decay_pulls_down_distant_match():
    """Mesmo match ponderado, gap no limite reduz para FLOOR do decay."""
    close = _weighted_confidence({"telh", "serralheria"}, 0, WINDOW_S)
    far = _weighted_confidence({"telh", "serralheria"}, WINDOW_S, WINDOW_S)
    assert far < close
    assert far == pytest.approx(close * TIME_DECAY_FLOOR, rel=1e-3)


def test_weighted_conf_saturates_at_1_0():
    """Muitos HIGH stems saturam em 1.0."""
    big = {"telh", "serralheria", "sinal", "saldo", "pix"}
    conf = _weighted_confidence(big, 0, WINDOW_S)
    assert conf == 1.0


def test_weighted_conf_mixed_high_and_low():
    """1 HIGH + 1 LOW: (1.5+0.7)/4.0 = 0.55 — abaixo do limiar de validacao."""
    conf = _weighted_confidence({"telh", "servico"}, 0, WINDOW_S)
    assert conf < 0.70
    assert conf == pytest.approx(
        ((TOKEN_WEIGHT_HIGH + TOKEN_WEIGHT_LOW)
         / CONFIDENCE_SATURATION_WEIGHTED),
        rel=1e-3,
    )
