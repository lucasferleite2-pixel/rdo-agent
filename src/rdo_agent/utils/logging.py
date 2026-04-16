"""
Logging configurado com Rich.

Uso:
    from rdo_agent.utils.logging import get_logger
    log = get_logger(__name__)
    log.info("Processando %s", path)
"""

from __future__ import annotations

import logging

from rich.logging import RichHandler

from rdo_agent.utils import config


_configured = False


def _configure_root() -> None:
    """Configura o logger raiz uma única vez."""
    global _configured
    if _configured:
        return

    settings = config.get()
    logging.basicConfig(
        level=settings.log_level,
        format="%(message)s",
        datefmt="[%Y-%m-%d %H:%M:%S]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Retorna um logger configurado para o módulo informado."""
    _configure_root()
    return logging.getLogger(name)
