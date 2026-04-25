# Histórico de tags do projeto

Catálogo cronológico de todas as tags do repositório, com link para
o SESSION_LOG correspondente quando existir.

Atende inconsistência #13 da auditoria de 25/04/2026 — em particular,
a tag `safety-checkpoint-pre-combo-12` que estava órfã sem documento
de sessão associado.

---

## Tags de release

| Data | Tag | Sessão / SESSION_LOG | Conteúdo |
|---|---|---|---|
| 2026-04-20 | `v0.2.0-sprint2` | Sprint 2 (sem log dedicado) | Handlers + CLI orquestração |
| 2026-04-20 | `v0.3.0-sprint3-code` | [SESSION_LOG_AUTONOMOUS_2026-04-20.md](SESSION_LOG_AUTONOMOUS_2026-04-20.md) | Sprint 3 fases 1-4 |
| 2026-04-22 | `v0.4.0-sprint4-ingestao` | [SESSION_LOG_AUTONOMOUS_V2.md](SESSION_LOG_AUTONOMOUS_V2.md) | Sprint 4 Op0-6: ingestão completa |
| 2026-04-22 | `v0.4.1-sprint4-post-review` | (continuação V2) | Op7 + revisão humana |
| 2026-04-22 | `v0.4.2-sprint4-op8-ocr-first` | [SESSION_LOG_SPRINT4_OP8.md](SESSION_LOG_SPRINT4_OP8.md) | OCR-first + `financial_records` |
| 2026-04-22 | `v0.4.3-sprint4-op9-vision-calibrated` | [SESSION_LOG_SPRINT4_OP9.md](SESSION_LOG_SPRINT4_OP9.md) | Vision V2 calibrado + reprocess |
| 2026-04-22 | `v0.4.4-sprint4-closure` | [SESSION_LOG_SPRINT4_OP10_OP11.md](SESSION_LOG_SPRINT4_OP10_OP11.md) | Op10 ledger no RDO + Op11 4 dívidas |
| 2026-04-22 | `v0.5.0-sprint5-fase-a` | [SESSION_LOG_SPRINT5_FASE_A.md](SESSION_LOG_SPRINT5_FASE_A.md) | Narrador Sonnet 4.6 + esqueleto correlator |
| 2026-04-23 | `v0.5.1-fase-a-validated` | (mesmo log) | 5 narrativas excepcionais validadas |
| 2026-04-23 | `v0.6.0-correlator` | [SESSION_LOG_SPRINT5_FASE_B.md](SESSION_LOG_SPRINT5_FASE_B.md) | Correlator rule-based, 3 detectores |
| 2026-04-23 | `v0.6.1-case-validated` | (mesmo log) | Caso real reconstruiu renegociação contratual |
| 2026-04-23 | `v0.7.0-ground-truth-polish` | [SESSION_LOG_SESSAO_1_POLIMENTO_GT.md](SESSION_LOG_SESSAO_1_POLIMENTO_GT.md) | Sessão 1: 6 dívidas + Fase C GT |
| 2026-04-23 | `v0.8.0-forensic-complete` | [SESSION_LOG_SESSAO_2_FASE_D_E.md](SESSION_LOG_SESSAO_2_FASE_D_E.md) | Sessão 2: Fase D (GT extractor) + Fase E (adversarial) |
| 2026-04-23 | `v1.0-vestigio-integrated` | [SESSION_LOG_SESSAO_3_LAUDO.md](SESSION_LOG_SESSAO_3_LAUDO.md) | Sessão 3: Laudo Generator Vestígio integrado |
| 2026-04-24 | `v1.0.1-markdown-fix` | [SESSION_LOG_SESSAO_3_8_MARKDOWN_FIX.md](SESSION_LOG_SESSAO_3_8_MARKDOWN_FIX.md) | Sessão 3.8: markdown→HTML no laudo (dívida #38) |
| 2026-04-25 | `v1.0.2-docs-sync` | (este sprint) | Higiene documental: 15 inconsistências resolvidas |

---

## Safety checkpoints

Tags `safety-checkpoint-*` são marcadores de estado pré-sessão de
médio/alto risco. Não correspondem a release — servem para revert
rápido se a sessão falhar.

| Data | Tag | Contexto | Sprint relacionada |
|---|---|---|---|
| 2026-04-20 | `safety-checkpoint-pre-sprint4` | Antes da Sprint 4 (ingestão completa) | Pré-Sprint 4 Op0-6 |
| 2026-04-22 | `safety-checkpoint-pre-op7` | Antes da revisão humana / Op7 (resumo RDO categoria total) | Sprint 4 Op7 |
| 2026-04-22 | `safety-checkpoint-pre-combo-12` | Aponta para o commit do Op7 (`9985f38` — resumo do RDO inclui categoria total). Marca o início do "combo de operações" subsequente que culminou em Op8 (OCR-first / `financial_records`). Sem SESSION_LOG dedicado — referenciado implicitamente por SESSION_LOG_SPRINT4_OP8 | Sprint 4, transição Op7→Op8 |
| 2026-04-22 | `safety-checkpoint-pre-op9` | Antes do Op9 (Vision V2 calibrado) | Sprint 4 Op9 |
| 2026-04-22 | `safety-checkpoint-pre-op10-11` | Antes do Op10 (ledger no RDO) + Op11 (dívidas técnicas) | Sprint 4 Op10/11 |
| 2026-04-22 | `safety-checkpoint-pre-sprint5` | Antes da Sprint 5 (forensic agent) | Pré-Sprint 5 |
| 2026-04-23 | `safety-checkpoint-pre-sprint5-fase-b` | Antes do correlator | Sprint 5 Fase B |
| 2026-04-23 | `safety-checkpoint-pre-sessao1-polimento-gt` | Antes da Sessão 1 (polimento + GT) | Sessão 1 |
| 2026-04-23 | `safety-checkpoint-pre-sessao2-fase-d-e` | Antes da Sessão 2 (GT extractor + adversarial) | Sessão 2 |
| 2026-04-23 | `safety-checkpoint-pre-sessao3` | Antes da Sessão 3 (Laudo Vestígio) | Sessão 3 |
| 2026-04-23 | `safety-checkpoint-pre-sessao3-8` | Antes da Sessão 3.8 (markdown fix) | Sessão 3.8 |
| 2026-04-25 | `safety-checkpoint-pre-higiene` | Antes da Sprint de Higiene Documental | v1.0.2-docs-sync |

---

## Convenção

- **Releases**: `v<major>.<minor>.<patch>-<identificador-curto>` (ex: `v0.6.1-case-validated`).
- **Safety**: `safety-checkpoint-pre-<operação>[-<YYYYMMDD>]`.
- Toda release deve ter SESSION_LOG correspondente em `docs/sessions/`.
- Toda safety checkpoint deve estar referenciada neste arquivo
  (criar entrada ao tagar).

> Convenção formalizada na Sprint de Higiene (`v1.0.2-docs-sync`).
> Tags pré-existentes que não seguiam a convenção foram catalogadas
> retroativamente neste documento.
