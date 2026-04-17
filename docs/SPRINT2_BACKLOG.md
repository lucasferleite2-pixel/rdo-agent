# Backlog para Sprint 2

Dívidas técnicas e escopo descoberto durante o teste E2E da Sprint 1 
(ingest do zip real `EVERALDO_SANTAQUITERIA` em 2026-04-17).

## Processamento de documentos (PDFs e afins)

**Decisão:** Sprint 1 marca PDFs como `file_type="document"` com 
`semantic_status="awaiting_document_processing"`, mas não processa. 
Sprint 2 implementa extração.

**Abordagem escolhida: extração de texto pura via pdfplumber.**

Justificativa:
- PDFs de obra são tipicamente digitais (memoriais, cronogramas, 
  ofícios gerados em Word/Excel, plantas exportadas do AutoCAD)
- Extração de texto pura é gratuita, rápida e suficiente para esses casos
- PDFs escaneados (raros em fluxo digital de obra) ficam com texto 
  vazio ou ruim — decide fallback se/quando aparecer volume real

Não fazer agora:
- GPT-4 Vision sobre PDFs (custo desnecessário para PDFs digitais)
- OCR de PDFs escaneados (Tesseract) — adicionar só se surgir demanda
- Conversão de docx/xlsx para texto — marca como `document` mas 
  processa só quando aparecerem

**Implementação esperada (Sprint 2):**
- Novo handler `extract_document_text_handler` (task_type `EXTRACT_DOCUMENT`)
- Input: `file_path` do documento em `10_media/`
- Output: `file_path` de um `.txt` gerado em `20_transcriptions/`
- Registra em `files` com `derived_from` do documento original
- Enfileira task downstream se o texto for relevante para o RDO 
  (decisão de classificador na Sprint 3)

## Outros itens descobertos no teste E2E

(acrescentar conforme forem aparecendo)
