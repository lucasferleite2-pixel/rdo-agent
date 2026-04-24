"""
rdo_agent.laudo — Gerador de laudo forense Vestígio

Módulo que converte estado do rdo-agent em laudo PDF com identidade Vestígio.

Uso típico:
    from rdo_agent.laudo import LaudoGenerator, LaudoData
    gen = LaudoGenerator()
    gen.generate(data, "laudo.pdf")
"""
from rdo_agent.laudo.adapter import (
    CorpusNotFoundError,
    rdo_to_vestigio_data,
)
from rdo_agent.laudo.vestigio_laudo import (
    Correlacao,
    EventoCronologia,
    LaudoData,
    LaudoGenerator,
    SecaoNarrativa,
)

__all__ = [
    "Correlacao",
    "CorpusNotFoundError",
    "EventoCronologia",
    "LaudoData",
    "LaudoGenerator",
    "SecaoNarrativa",
    "rdo_to_vestigio_data",
]
