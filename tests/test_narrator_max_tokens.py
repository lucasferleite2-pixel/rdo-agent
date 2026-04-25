"""Testes de _max_tokens_for_scope (Sessao 5, divida #32)."""

from __future__ import annotations

import pytest

from rdo_agent.forensic_agent.narrator import (
    MAX_TOKENS,
    MAX_TOKENS_BY_SCOPE,
    _max_tokens_for_scope,
)


def test_max_tokens_day_scope():
    assert _max_tokens_for_scope("day") == 6144
    assert _max_tokens_for_scope("day") == MAX_TOKENS_BY_SCOPE["day"]


def test_max_tokens_overview_scope():
    """Overview e obra_overview ambos pegam 16k tokens."""
    assert _max_tokens_for_scope("overview") == 16384
    assert _max_tokens_for_scope("obra_overview") == 16384


def test_max_tokens_week_and_month_scopes():
    """Scopes preparados para Sessao 6+ tambem retornam valores definidos."""
    assert _max_tokens_for_scope("week") == 8192
    assert _max_tokens_for_scope("month") == 10240


def test_max_tokens_unknown_scope_fallback():
    """Scope desconhecido cai no fallback MAX_TOKENS conservador."""
    assert _max_tokens_for_scope("inexistente") == MAX_TOKENS
    assert _max_tokens_for_scope("") == MAX_TOKENS


def test_max_tokens_env_override(monkeypatch):
    """Env var RDO_AGENT_MAX_TOKENS_OVERRIDE_<SCOPE> sobrescreve."""
    monkeypatch.setenv("RDO_AGENT_MAX_TOKENS_OVERRIDE_DAY", "12000")
    assert _max_tokens_for_scope("day") == 12000

    # Casing: env e' pelo scope em UPPER
    monkeypatch.setenv("RDO_AGENT_MAX_TOKENS_OVERRIDE_OVERVIEW", "20000")
    assert _max_tokens_for_scope("overview") == 20000


def test_max_tokens_env_override_invalid_falls_back(monkeypatch, caplog):
    """Env var nao-int loga warning e cai no default."""
    import logging

    monkeypatch.setenv("RDO_AGENT_MAX_TOKENS_OVERRIDE_DAY", "nao-numero")
    with caplog.at_level(logging.WARNING, logger="rdo_agent.forensic_agent.narrator"):
        assert _max_tokens_for_scope("day") == 6144
    assert any("override" in rec.message for rec in caplog.records)


def test_max_tokens_env_override_only_affects_target_scope(monkeypatch):
    """Override em DAY nao afeta OVERVIEW."""
    monkeypatch.setenv("RDO_AGENT_MAX_TOKENS_OVERRIDE_DAY", "12000")
    assert _max_tokens_for_scope("day") == 12000
    assert _max_tokens_for_scope("overview") == 16384  # original
