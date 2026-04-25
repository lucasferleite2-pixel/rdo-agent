"""
OCR router — Sessão 9 / dívida #49.

Coordenador único que decide qual extractor especializado chamar
para cada arquivo, e cacheia resultados para evitar reprocessamento.

Os 3 módulos OCR existentes mantêm-se intactos e cada um continua
responsável pelo seu domínio:

- ``rdo_agent.ocr_extractor``      — texto genérico (Vision API)
- ``rdo_agent.financial_ocr``      — comprovantes PIX/NF (Vision API)
- ``rdo_agent.document_extractor`` — PDFs (pdfplumber)

O router **não substitui** nenhum deles. Apenas:

1. **Detecta presença de texto** via Tesseract local (rápido, $0).
   Quando ausente ou idioma `por` não instalado, faz **fail-open**
   (assume "tem texto" — caller decide).
2. **Roteia** para o extractor apropriado com base em:
   - hint da Camada 3 do vision cascade (``RoutingDecision.target``);
   - heurísticas de file_type / extensão (PDF → document; etc).
3. **Cacheia** resultado em tabela ``ocr_cache`` para skip em
   re-execução.

Decisão consciente: zero ML aqui. Pre-classify zero-shot via CLIP é
a dívida #60 (também aplicável a routing).
"""

from __future__ import annotations

from rdo_agent.ocr_router.router import (
    OCR_TARGETS,
    OCRRouter,
    OCRTarget,
    migrate_ocr_cache,
)

__all__ = [
    "OCR_TARGETS",
    "OCRRouter",
    "OCRTarget",
    "migrate_ocr_cache",
]
