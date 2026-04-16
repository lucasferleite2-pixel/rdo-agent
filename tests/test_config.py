"""Teste básico de carregamento de config."""

from __future__ import annotations

from rdo_agent.utils import config


def test_settings_load() -> None:
    """Verifica que settings carrega sem erro mesmo sem .env preenchido."""
    settings = config.Settings.load()
    assert settings.claude_model  # tem default
    assert settings.vaults_root  # tem default
    assert settings.log_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def test_vault_path() -> None:
    settings = config.Settings.load()
    path = settings.vault_path("CODESC_75817")
    assert path.name == "CODESC_75817"
    assert path.parent == settings.vaults_root
