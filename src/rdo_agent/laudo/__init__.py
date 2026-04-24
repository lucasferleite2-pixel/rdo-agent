"""
rdo_agent.laudo — Gerador de laudo forense Vestígio

Módulo que converte estado do rdo-agent em laudo PDF com identidade Vestígio.

Uso típico:
    from rdo_agent.laudo import LaudoGenerator, LaudoData
    gen = LaudoGenerator()
    gen.generate(data, "laudo.pdf")
"""
from .vestigio_laudo import (
    LaudoGenerator,
    LaudoData,
    SecaoNarrativa,
    EventoCronologia,
    Correlacao,
)

__all__ = [
    "LaudoGenerator",
    "LaudoData",
    "SecaoNarrativa",
    "EventoCronologia",
    "Correlacao",
]
