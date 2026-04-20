# SPRINT 3 PLAN — Classificação Semântica + Revisão Humana

- **Início:** 2026-04-20
- **Prazo-alvo:** 2026-05-04 (14 dias) — RDO piloto de 1 dia do vault EVERALDO
- **Backed by:** ADR-001 (transcrição), ADR-002 (classifications table), calibração manual

## Objetivo

Converter 105 transcrições Whisper da vault EVERALDO_SANTAQUITERIA em **classificações semânticas auditáveis**, com revisão humana por amostragem calibrada, prontas para serem agregadas pelo agente-engenheiro Claude na Sprint 4.

## Arquitetura em 4 camadas

    Entrada:  transcriptions (105) + visual_analyses (10)
        |
        v
    CAMADA 1 — Detector de Qualidade (gpt-4o-mini)
        Lê transcrição -> flag: coerente | suspeita | ilegivel
        Output: classifications.human_review_needed = 0|1
        Custo: ~$0.03 para 105 transcrições
        |
        +-- (review_needed=0) --> CAMADA 3 (direta)
        |                             Classificador gpt-4o-mini
        |                             sobre texto original
        |
        +-- (review_needed=1) --> CAMADA 2 — Revisão Humana CLI
                                      Lucas ouve áudio + corrige
                                      human_corrected_text
                                      human_reviewed = 1
                                      |
                                      v
                                  CAMADA 3 (pós-revisão)
                                      Classificador sobre texto
                                      human_corrected_text
        |
        v
    CAMADA 4 — Persistência: tabela `classifications`
        UNIQUE (obra, source_file_id)
        categories JSON array, primary-first
        Tags de proveniência: human_reviewed, model, source_sha256

## Estados válidos de `classifications`

| review_needed | reviewed | semantic_status | Interpretação |
|---|---|---|---|
| 0 | 0 | classified | Detector aprovou, classificador rodou, automatizado |
| 1 | 0 | pending_review | Detector suspeitou, aguardando Lucas |
| 1 | 1 | classified | Detector suspeitou, Lucas corrigiu, classificador re-rodou |
| 1 | 1 | rejected | Lucas ouviu e determinou áudio inaproveitável |
| 0 | 1 | classified | Edge case: Lucas revisou manualmente sem flag (override) |

## Fases

### Fase 1 — Schema + Detector de Qualidade (dias 1-3)

**Entregáveis:**
- Migration `005_add_classifications_table.sql` aplicada
- Módulo `classifier/quality_detector.py` seguindo padrão canônico (ver `transcriber/__init__.py`)
- Testes: `tests/test_quality_detector.py` (10+ casos)
- CLI: `rdo-agent detect-quality --obra EVERALDO_SANTAQUITERIA`

**Critério V1:** 105 transcrições passam pelo detector; flag `human_review_needed` populada; 29-50 marcadas (esperado ~30 pela calibração).

**Custo estimado:** ~$0.03 + 3 dias dev.

### Fase 2 — Interface de Revisão Humana (dias 4-7)

**Entregáveis:**
- CLI interativa `rdo-agent review --obra EVERALDO` com player de áudio + editor
- Testes: mock editor/player, validação de transições
- Docs: `SPRINT3_REVIEW_WORKFLOW.md`

**Critério V2:** todas transcrições pending_review processáveis via CLI; áudio reproduz.

**Custo estimado:** 0 financeiro + 3-4 dias dev + ~1h revisão real de Lucas.

### Fase 3 — Classificador Semântico (dias 8-11)

**Entregáveis:**
- Módulo `classifier/semantic_classifier.py` com 9 categorias + regras de fronteira + few-shot
- Contexto estável: Lucas=contratante Vale Nobre; Everaldo=prestador; obra EE Santa Quitéria (CODESC 75817); cobertura + granilite
- Testes: 20+ casos
- CLI: `rdo-agent classify --obra EVERALDO`

**Critério V3:** 105 classificadas; distribuição em ±50% da projeção empírica.

**Critério Q1 — Amostragem humana:** Lucas revisa 30 classificações aleatórias, acerto >= 22/30 (73%).

**Critério Q2 — Baseline:** 5 classificações "fáceis" (alta confidence pós-revisão, não-ilegivel), >= 4/5 acerto.

**Custo estimado:** ~$0.30 + 4 dias dev.

### Fase 4 — Integração E2E + RDO Piloto (dias 12-14)

**Entregáveis:**
- Script `scripts/generate_rdo_piloto.py`
- Docs `SPRINT3_RESULTS.md`
- RDO PDF + markdown gerados

**Critério V4:** RDO piloto existe; >= 80% do conteúdo rastreável; Lucas aprovou visualmente.

**Custo estimado:** 0 financeiro + 3 dias dev.

## Orçamento consolidado

| Item | Custo financeiro | Custo Lucas |
|---|---|---|
| Fase 1 (detector) | $0.03 | 3 dias dev |
| Fase 2 (revisão UI) | $0.00 | 3-4 dias dev + 1h revisão real |
| Fase 3 (classificador) | $0.30 | 4 dias dev |
| Fase 4 (integração + RDO) | $0.00 | 3 dias dev |
| **Total Sprint 3** | **~$0.35** | **13-14 dias** |

**Acumulado projetado fim Sprint 3:** $0.71 + $0.35 = **$1.06**.

## Dependências externas

- `gpt-4o-mini-2024-07-18` via OpenAI API
- `ffplay` (opcional, Fase 2)
- `pandoc` ou `weasyprint` (Fase 4)

## Riscos e mitigações

| Risco | Prob | Mitigação |
|---|---|---|
| Detector super-flaggeia (>50%) | Média | Recalibrar prompt na Fase 1 |
| Classificador força categoria em `ilegivel` | Alta | `ilegivel` explícito no prompt; Q1 detecta |
| Revisão cansativa leva a aceitar tudo sem ouvir | Média | CLI registra tempo/revisão |
| 105 transcrições não cobrem 1 dia suficientemente | Baixa | Fallback: RDO semanal |
| Custo >$0.50 (retries) | Baixa | Hard limit 3 retries |

## Saída da Sprint 3

- Tag `v0.3.0-sprint3`
- `classifications` populada (105 linhas)
- `reports/rdo_piloto_EVERALDO_YYYY-MM-DD.pdf`
- Vocabulário calibrado (ADR-002)
- CLI completa
- 40+ novos testes, 100% verdes
- `SPRINT3_RETROSPECTIVE.md`
