"""
rdo-agent — Agente Forense de RDO

Sistema multi-agente para geração de Relatórios Diários de Obra
a partir de exportações do WhatsApp, com cadeia de custódia auditável.

Arquitetura:
- Camada 1 (este pacote): ingestão + parser + temporal + extractor + orchestrator
- Camada 2: integrações OpenAI (Whisper + Vision) — Sprint 2
- Camada 3: agente-engenheiro via Claude API — Sprint 4

Documentação completa: docs/Blueprint_V3.docx
"""

__version__ = "0.1.0"
__author__ = "Lucas Ferreira Leite"
