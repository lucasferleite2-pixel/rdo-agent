"""
Configuração central do rdo-agent.

Carrega variáveis de ambiente via python-dotenv e expõe settings tipadas.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Configurações globais do sistema, carregadas do .env."""

    # APIs
    openai_api_key: str
    anthropic_api_key: str

    # Modelos
    claude_model: str

    # Paths
    vaults_root: Path

    # Comportamento
    log_level: str
    dry_run: bool

    @classmethod
    def load(cls) -> Settings:
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
            vaults_root=Path(os.getenv("RDO_VAULTS_ROOT", "./rdo_vaults")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
        )

    def vault_path(self, obra: str) -> Path:
        """Retorna o path da vault de uma obra específica."""
        return self.vaults_root / obra


# Singleton global — acessar via config.get()
_settings: Settings | None = None


def get() -> Settings:
    """Retorna as settings globais (lazy load)."""
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings
