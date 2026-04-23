# rdo-agent — Documento de Contexto Institucional

> **Propósito deste documento:** servir como briefing completo pra qualquer nova conversa de IA (Claude, GPT, etc) assumir o projeto sem perda de contexto. Leia de ponta a ponta antes de tomar qualquer decisão técnica ou arquitetural.
>
> **Última atualização:** 23/04/2026 — versão rdo-agent v0.8.0-forensic-complete

---

## 1. Visão do Produto

### 1.1 O que é o rdo-agent

Sistema forense que transforma **conversas de WhatsApp de canteiros de obra** em **laudos estruturados, auditáveis e juridicamente utilizáveis**. Opera sobre corpus real (áudios, imagens, textos, PDFs, comprovantes) e produz narrativas cronológicas com rastreabilidade completa.

### 1.2 Problema que resolve

Obras no Brasil geram **toneladas de comunicação informal via WhatsApp**: pagamentos por PIX, negociações de escopo, decisões técnicas, fotos do canteiro, comprovantes, renegociações, conflitos. Essa evidência digital é **juridicamente valiosa** mas **operacionalmente inacessível** — ninguém consegue auditar 500+ mensagens manualmente em prazo razoável.

O rdo-agent automatiza essa auditoria com qualidade de perícia.

### 1.3 Usuários-alvo (hierarquizado)

1. **Advogados em disputas contratuais** — construção, serviços, reformas, obras públicas
2. **Peritos judiciais** — laudos com evidência digital de WhatsApp
3. **Gestores de obras** — auditoria de contratos informais
4. **M&A especializado em distressed** — reconstituir histórico de empresas em RJ
5. **Empresas de construção** — due diligence interna, defesa em fiscalização pública

### 1.4 Operador principal atual

**Lucas Fernandes Leite** — empresário em Minas Gerais, atuando através de:
- **Vale Nobre Construtora e Imobiliária Ltda** — contratos públicos com SEE-MG / SRE Manhuaçu
- **HCF Investimentos e Participações** — holding, M&A, distressed

O rdo-agent atende demandas diretas do operador (disputas Vale Nobre × SEE-MG) e futuras aplicações em M&A (HCF).

---

## 2. Conceito Arquitetural Crítico: "Canal" vs "Obra"

### 2.1 A distinção

Uma **obra real** (ex: "Reforma da Escola Estadual Povoado de Santa Quitéria") tem **múltiplos canais de comunicação**:
OBRA REAL: EE Santa Quitéria (CODESC 75817)
│
├─ Canal 1: WhatsApp Lucas ↔ Everaldo (serralheiro)        ← vault atual
├─ Canal 2: WhatsApp Lucas ↔ Encarregado de obra
├─ Canal 3: WhatsApp Lucas ↔ Diretora da escola
├─ Canal 4: WhatsApp Lucas ↔ Fornecedores
├─ Canal 5: WhatsApp Lucas ↔ Engenheiro Luiz Carlos Corrêa
├─ Canal 6: Emails com fiscal SEE-MG Aldo dos Reis Fernandes
└─ ... (outros)

### 2.2 Nomenclatura oficial do projeto

- **"Canal"** = 1 linha de comunicação = 1 vault no rdo-agent
- **"Obra"** = agregação de N canais referentes ao mesmo trabalho real

### 2.3 Implicação arquitetural

**ATÉ v1.0:** o produto analisa **1 canal por vez, com alta qualidade**. Cada vault corresponde a um canal. O que hoje chamamos internamente de `--obra` no código é, na verdade, um **canal**.

**PÓS v1.0 (v2.0+):** módulo consolidador recebe N narrativas/correlações de canais já analisados e produz **relatório único da obra completa**, com:
- Cronologia unificada cross-canal
- Correlações entre canais (fiscal exigiu X em 01/04 → engenheiro respondeu em 02/04 → prestador cumpriu em 08/04)
- Detecção de divergências entre canais (versões contraditórias)
- Ledger financeiro consolidado
- Narrativa executiva unificada

**Valor comercial v2.0 = 10-20× valor v1.0** — produto vira laudo completo de obra, mercado pula de "caso pequeno" para "advocacia premium em grandes disputas".

---

## 3. Pipeline Técnico Atual (v0.6.1)

### 3.1 Fluxo de dados de ponta a ponta
INPUT: export WhatsApp (.txt + mídia) → pasta da vault
│
▼
[1] INGESTÃO
├─ parser WhatsApp → tabela messages
├─ files/ copiados + hash sha256 → tabela files
└─ estrutura SQLite montada
│
▼
[2] TRANSCRIÇÃO (áudio/vídeo)
├─ Whisper local large-v3 (NVIDIA GPU, PT-BR)
└─ tabela transcriptions
│
▼
[3] QUALITY CHECK + CLASSIFICAÇÃO
├─ GPT-4o-mini: avalia qualidade + flag pending_review
├─ CLI revisão humana: corrige transcrições problemáticas
├─ GPT-4o-mini: classificação semântica (categories)
└─ tabela classifications (250+ eventos EVERALDO)
│
▼
[4] ANÁLISE VISUAL
├─ GPT-4o Vision V2 com few-shot (11 GT humanos calibraram)
├─ OCR-first para comprovantes (PIX, NF, boletos)
├─ tabela visual_analyses (96 registros EVERALDO)
└─ tabela financial_records (4 PIX = R$12.530 EVERALDO)
│
▼
[5] NARRATIVA FORENSE (Sprint 5 Fase A)
├─ dossier_builder: monta JSON determinístico com contexto
├─ narrator.py: Sonnet 4.6 gera narrativa markdown
├─ prompts.py: NARRATOR_SYSTEM_PROMPT_V2_CORRELATIONS
├─ validator.py: checklist F3 pós-narrativa
├─ persistence.py: grava DB + arquivo
└─ tabela forensic_narratives (10+ narrativas EVERALDO)
│
▼
[6] CORRELATOR (Sprint 5 Fase B)
├─ detectors/temporal.py → TEMPORAL_PAYMENT_CONTEXT
├─ detectors/semantic.py → SEMANTIC_PAYMENT_SCOPE
├─ detectors/math.py → MATH_VALUE_MATCH / DIVERGENCE / INSTALLMENT
├─ correlator.py: orquestra 3 detectores
└─ tabela correlations (38 detectadas EVERALDO)
│
▼
[7] RDO (Relatório Diário de Obra)
├─ renderer markdown + PDF
├─ seção Ledger Financeiro
├─ seção Correlações Detectadas
└─ output: reports/rdo_piloto_*.md + .pdf

### 3.2 Stack tecnológico

- **Linguagem:** Python 3.12
- **Ambiente:** WSL Ubuntu 24.04, venv isolada
- **Database:** SQLite (portável, auditável, backupeado)
- **AI APIs:**
  - **Anthropic Claude Sonnet 4.6** — narrador forense
  - **OpenAI Whisper (local)** — transcrição
  - **OpenAI GPT-4o-mini** — classificação + quality
  - **OpenAI GPT-4o Vision** — análise de imagens
- **CLI:** typer + rich (tabelas formatadas)
- **Testes:** pytest (480 passando em v0.6.1)
- **Repositório:** `github.com/lucasferleite2-pixel/rdo-agent`

### 3.3 Estrutura de diretórios
~/projetos/rdo-agent/
├── src/rdo_agent/
│   ├── cli.py                          # comandos: ingest, classify, narrate, correlate
│   ├── ingestion/                      # parser WhatsApp + Whisper + file mgmt
│   ├── classification/                 # GPT-4o-mini + revisão humana
│   ├── vision/                         # Vision V2 + OCR-first
│   ├── financial/                      # extração PIX/NF/boletos
│   ├── rdo/                            # renderer markdown + PDF
│   ├── forensic_agent/                 # ← Sprint 5
│   │   ├── dossier_builder.py
│   │   ├── narrator.py                 # Sonnet 4.6
│   │   ├── prompts.py
│   │   ├── validator.py
│   │   ├── persistence.py
│   │   ├── correlator.py
│   │   ├── types.py
│   │   └── detectors/
│   │       ├── temporal.py
│   │       ├── semantic.py
│   │       └── math.py
│   ├── ground_truth/                   # Fase C (loader YAML, schema)
│   └── gt_extractor/                   # Fase D (entrevista simple + adaptive)
│
├── docs/
│   ├── PROJECT_CONTEXT.md              # ← ESTE ARQUIVO
│   ├── sessions/                       # SESSION_LOGs por sprint
│   └── ground_truth/
│       └── EVERALDO_SANTAQUITERIA.yml  # 1º GT estruturado
│
├── tests/                              # 480 testes
├── reports/
│   ├── narratives/                     # narrativas .md geradas
│   └── *.md + *.pdf                    # RDOs
│
└── ~/rdo_vaults/
└── EVERALDO_SANTAQUITERIA/
├── index.sqlite                # DB operacional (~4.7MB)
├── files/                      # mídia original
└── .bak-pre-                 # 6 backups de safety

---

## 4. Schema do Banco de Dados (SQLite)

Tabelas principais (simplificado):

```sql
-- Arquivos físicos ingestados
files (file_id, obra, file_path, file_type, sha256, timestamp_resolved, ...)

-- Mensagens de texto do WhatsApp
messages (message_id, obra, timestamp_whatsapp, sender, content, media_ref, ...)

-- Transcrições de áudio/vídeo
transcriptions (id, obra, file_id, text, language, confidence, ...)

-- Classificações semânticas
classifications (id, obra, source_file_id, source_message_id, source_type,
                 categories, quality_flag, human_reviewed, semantic_status, ...)

-- Análises visuais (Vision V2)
visual_analyses (id, obra, file_id, description, confidence, ...)
visual_analyses_active / visual_analyses_archive  -- versionamento

-- Comprovantes financeiros (PIX/NF/boleto)
financial_records (id, obra, source_file_id, doc_type, valor_centavos,
                   data_transacao, hora_transacao, pagador_nome, recebedor_nome,
                   descricao, confidence, ...)

-- Narrativas forenses geradas
forensic_narratives (id, obra, scope, scope_ref, narrative_text,
                     model_used, confidence, ...)

-- Correlações detectadas
correlations (id, obra, correlation_type, primary_event_ref, primary_event_source,
              related_event_ref, related_event_source, time_gap_seconds,
              confidence, rationale, detected_by, ...)

-- Metadados auxiliares
api_calls, events, clusters, tasks, documents, media_derivations
```

**Decisão arquitetural importante:** schema é **polimórfico** — `classifications.source_type` pode ser 'transcription', 'text_message' ou 'visual_analysis', com JOIN correspondente. `correlations` é **pairwise 1:1** (aresta de grafo), não cluster (mais queryável).

---

## 5. Histórico Completo de Entregas (cronológico)

### 5.1 Sprints 1-3 (março–início abril 2026) — FOUNDATION

Base arquitetural: setup repo, estrutura Python, ambiente WSL, integração SDKs, schema SQLite, parser WhatsApp, pipeline básica de ingestão.

### 5.2 Sprint 4 Op0-Op6 (início abril 2026) — CLASSIFICAÇÃO + VISION V1

- Whisper local para transcrição (NVIDIA GPU, PT-BR)
- GPT-4o-mini para quality check + classificação
- GPT-4o Vision V1 para análise de imagens
- 213 testes baseline

### 5.3 Sprint 4 Op7 (22/04, manhã) — `v0.4.1`

- Revisão humana de 6 pending_review
- Category summary no RDO
- Auditoria forense do ledger

### 5.4 Sprint 4 Op8 (22/04, tarde) — `v0.4.2`

- **Descoberta:** OCR-first pipeline revelou 4 comprovantes PIX = R$12.530
- Tabela `financial_records` criada
- Pipeline OCR antes de Vision

### 5.5 Sprint 4 Op9 (22/04, tarde) — `v0.4.3`

- Vision V2 calibrado com 11 amostras humanas ground truth
- Reprocessamento retroativo da vault EVERALDO
- `visual_analyses_active` + `visual_analyses_archive` (versionamento)

### 5.6 Sprint 4 Op10+Op11 (22/04, noite) — `v0.4.4`

- Ledger visível no RDO
- 4 dívidas técnicas resolvidas (#9, #10, #11, #12)

### 5.7 Sprint 5 Fase A (22/04 noite → 23/04 manhã) — `v0.5.0` e `v0.5.1`

- Módulo `forensic_agent/` completo
- Narrador Sonnet 4.6
- Dossier JSON determinístico
- Validator F3
- 5 narrativas forenses geradas EVERALDO
- Fix: MAX_TOKENS 4096→6144, timeout 60s→300s

### 5.8 Sprint 5 Fase B (23/04 tarde, ~40 min autônomas) — `v0.6.0`

- 3 detectores rule-based: TEMPORAL, SEMANTIC, MATH_*
- 38 correlações detectadas EVERALDO
- CLI `rdo-agent correlate`
- Narrator v2 cita correlações validadas
- +62 testes (418→480)

### 5.9 Case Validation (23/04 tarde) — `v0.6.1`

- Narrativa 08/04 gerada descobriu sozinha estrutura de 2 contratos
- Investigação manual confirmou achado do detector MATH_VALUE_DIVERGENCE
- Ground Truth YAML documentado: `docs/ground_truth/EVERALDO_SANTAQUITERIA.yml`

### 5.10 Sprint 5 Fase C (23/04 tarde, Sessão 1) — `v0.7.0-ground-truth-polish`

- 6 dívidas técnicas fechadas: #14, #19, #20, #22, #23, #28
- Módulo `src/rdo_agent/ground_truth/` (schema + loader + validação YAML)
- CLI `rdo-agent narrate --context docs/ground_truth/<obra>.yml`
- Narrator V3_GT: verifica corpus vs GT (CONFORME/DIVERGENTE/NÃO VERIFICÁVEL)
- 2 narrativas regeneradas com GT real (passed=YES)
- 480 → 515 testes; sessão ~35min; custo $0.38

### 5.11 Sprint 5 Fase D + E (23/04 final de tarde, Sessão 2) — `v0.8.0-forensic-complete`

- 5 dívidas técnicas fechadas: #24, #25, #26, #29, #30
- **Fase D**: módulo `src/rdo_agent/gt_extractor/` com entrevista interativa
  * Modo simple: questionário síncrono (zero API)
  * Modo adaptive: Claude conduz, detecta contradições, sugere campos
  * CLI: `rdo-agent extract-gt --obra X [--mode simple|adaptive]`
- **Fase E**: narrator V4_ADVERSARIAL
  * Nova seção "Contestações Hipotéticas" (3-5 argumentos da contraparte)
  * CLI: `rdo-agent narrate --adversarial` (combinável com `--context`)
  * Uso: preparar defesa em disputa judicial/administrativa
- Tuning SEMANTIC com time_decay + keyword weights (semantic_v2)
- MATH distingue valor unitário (R$/metro) de agregado (total)
- Overview inclui `correlations_sample_weak` para comentar padrões
- MAX_TOKENS 6144→10240 e MAX_BODY_CHARS 20000→40000 (overview completo)
- 3 narrativas EVERALDO regeneradas com GT + adversarial
- 515 → 565 testes; sessão ~50min; custo $0.85

---

## 6. Caso Piloto — EVERALDO_SANTAQUITERIA (ground truth)

### 6.1 Contexto real

**Obra real:** Reforma da Escola Estadual Povoado de Santa Quitéria (CODESC 75817)
- Município: Santana do Manhuaçu, MG
- Contratante público: SEE-MG / SRE Manhuaçu
- Contratada: Vale Nobre Construtora e Imobiliária Ltda

**Canal analisado:** WhatsApp entre:
- Parte A: **Lucas Fernandes Leite** (representante Vale Nobre)
- Parte B: **Everaldo Caitano Baia** (serralheiro, prestador de serviço)

### 6.2 Estrutura contratual (2 contratos)

| Contrato | Escopo | Valor | Status | Data Acordo |
|---|---|---|---|---|
| **C1** | Estrutura bruta: tesouras + terças + **esqueleto** do fechamento | R$ 7.000 | Quitado | 06/04/2026 |
| **C2** | Acabamento completo: telhado + fechamento lateral (tela/alambrado) | R$ 11.000 | 50% pago, em execução | 08/04/2026 |

### 6.3 Pagamentos registrados (R$ 12.530 total)

| Data | Hora | Valor | Contrato | Descrição |
|---|---|---|---|---|
| 06/04 | 11:13 | R$ 3.500 | C1 | Sinal 50% |
| 10/04 | 12:42 | R$ 3.500 | C1 | Saldo (quitado) |
| 14/04 | 13:43 | R$ 30 | — | Reembolso gasolina (fora dos contratos) |
| 16/04 | 10:17 | R$ 5.500 | C2 | Sinal 50% |

**Pendente:** R$ 5.500 (saldo C2, pago ao finalizar)

### 6.4 Insight forense importante

A descrição do PIX de 06/04 menciona *"fechamento"* referindo-se ao **esqueleto estrutural** (parte do C1), **não** ao fechamento completo com tela (que é C2). Essa nuance resolve a aparente divergência de R$ 1.500 que narrativas iniciais sinalizaram como ponto de atenção.

### 6.5 Problema técnico conhecido

Em 15/04/2026, retrabalho significativo do alambrado foi necessário porque **medidas foram feitas errado por terceiros antes da intervenção do Everaldo**. Impacto: atraso no cronograma. Responsabilidade: terceiros anteriores, não o Everaldo.

---

## 7. Roadmap Consolidado

### 7.1 CONCLUÍDO
✅ v0.4.4  — Pipeline completo + ledger financeiro + hardening
✅ v0.5.1  — Sprint 5 Fase A: agente narrador forense validado
✅ v0.6.0  — Sprint 5 Fase B: correlator rule-based (3 detectores)
✅ v0.6.1  — Case validation empírica em corpus real
✅ v0.7.0  — Sprint 5 Fase C + 6 dívidas (Sessão 1)
✅ v0.8.0  — Sprint 5 Fase D+E + 5 dívidas (Sessão 2)

### 7.2 CAMINHO ATÉ v1.0 (1 sessão restante)
🟡 SESSÃO 3 → v1.0-production-ready (estimativa 4-6h autônoma, custo $1-2)
├─ UI Web básica (FastAPI + HTML/htmx)
├─ Dashboard de narrativas + correlações
├─ Export PDF com letterhead HCF/Vale Nobre
├─ CSS profissional
└─ README + deployment docs

### 7.3 PÓS-v1.0 — CONSOLIDADOR MULTI-CANAL (v2.0+)
🔴 SESSÃO 4 → v2.0-alpha
├─ Refactoring semântico "obra vs canal" (breaking change)
├─ Módulo consolidador (recebe N narrativas de canais)
├─ Cronologia unificada cross-canal
└─ Correlator cross-faceta (mais complexo)
🔴 SESSÃO 5 → v2.1
├─ Detecção de divergências inter-canais
├─ Ledger consolidado (N canais → 1 ledger)
├─ Relatório executivo da obra
└─ Export PDF multi-seção
🔴 SESSÃO 6 → v2.2-full-production
├─ Ingestão batch (N canais simultâneos)
├─ CLI: rdo-agent consolidate --obra SANTA_QUITERIA
└─ Validação em caso real completo

### 7.4 Política de validação

**Corpus oficial de desenvolvimento:** EVERALDO_SANTAQUITERIA até v1.0 completa.

**Obras reais adicionais** (EE Milton Campos, outras) só entram **após v1.0** pra evitar contaminação de desenvolvimento por pressão de caso real.

---

## 8. Padrões Operacionais Estabelecidos

### 8.1 Commits

- Convenção: `<tipo>(<escopo>): <descrição>`
- Tipos comuns: feat, fix, docs, chore, refactor, test
- Escopos comuns: sprintN-faseX, detector-Y, narrator, validator, etc
- **Cada fase de trabalho = 1 commit** (nunca acumular)

### 8.2 Tags

- Versões: `v<major>.<minor>.<patch>-<identificador>` (ex: `v0.6.1-case-validated`)
- Safety checkpoints: `safety-checkpoint-pre-<operação>-<YYYYMMDD>`
- Sempre push com `--tags`

### 8.3 Backups

- Antes de sessão autônoma de médio-alto risco: backup do DB
- Formato: `index.sqlite.bak-pre-<operacao>-<YYYYMMDD-HHMM>`
- 6 backups atualmente preservados em EVERALDO_SANTAQUITERIA

### 8.4 Sessões autônomas (Claude Code)

- Comando: `claude --dangerously-skip-permissions`
- Prompt estruturado em fases numeradas
- Commits incrementais a cada fase
- Testes verde antes de cada commit
- Se bloqueio: para e reporta (não tenta "adivinhar e seguir")
- Velocidade observada: 8-12× mais rápida que estimativas tradicionais

### 8.5 Validação de entregas autônomas

**Nunca confiar em "✅ CONCLUÍDA" da IA sem validação empírica:**
1. Git log + status (não ficou nada pendente)
2. Pytest (testes passando de verdade)
3. Query no DB (dados novos existem)
4. Sample de output (qualidade visível)

---

## 9. Dívidas Técnicas Registradas

### 9.1 Resolvidas

- ~~#6~~: OCR plantas CAD (parcial, corpus limitado)
- ~~#9~~: Timeout ocr_extractor
- ~~#10~~: Archive move-style com superseded_by
- ~~#11~~: Roteamento video frames
- ~~#12~~: Retry JSON truncados
- ~~#15~~: Gerar 4 narrativas restantes Fase A

### 9.2 Resolvidas em Sessão 1 (v0.7.0)

- ~~#14~~: Validator regex horário (aceita HH:MM + HHhMM + segundos)
- ~~#19~~: `--skip-cache` invalida cache existente (force=True)
- ~~#20~~: Cost zero quando API descartada por cache
- ~~#22~~: MATH dedup de linhas idênticas
- ~~#23~~: MATH janela 7d → 48h
- ~~#28~~: Overview prioriza dias densos (top-5 + first-N + last-N)

### 9.3 Resolvidas em Sessão 2 (v0.8.0)

- ~~#24~~: SEMANTIC tuning (time_decay + keyword weights semantic_v2)
- ~~#25~~: `--min-correlation-conf` threshold configurável
- ~~#26~~: MATH distingue UNITARY / AGGREGATE / AMBIGUOUS
- ~~#29~~: Prompt com regra de ancoragem de correlações
- ~~#30~~: Overview inclui `correlations_sample_weak`

### 9.4 Pendentes (pós-v0.8.0)

- **#13**: Rename "Pagamentos" → "Discussões financeiras" no RDO (cosmética)
- **#16**: Streaming no narrator (solução arquitetural vs fix de timeout)
- **#27**: Detector futuro CONTRACT_RENEGOTIATION
- **#31**: Validator `MAX_BODY_CHARS` — considerar tiers (critical vs informational)
- **#32**: MAX_TOKENS dinâmico por scope (overview precisa de mais que day)
- **#33**: Warning file_ids 50% em overview adversarial — diretriz pode conflitar

---

## 10. Decisões Arquiteturais Importantes

### 10.1 Schema de correlations é pairwise 1:1

**Decisão:** cada linha é aresta de grafo, não cluster 1→N. Facilita queries, agregações e versionamento de detectores.

### 10.2 Ground Truth é "orientativo", não "aditivo"

**Descoberta em 23/04:** o corpus bruto do WhatsApp **contém a evidência** — o rdo-agent apenas precisa de **direcionamento estrutural** pra interpretar corretamente (ex: "separe contratos", "identifique partes"). GT não é pra "fornecer dados ausentes" mas pra "orientar interpretação".

### 10.3 Dossier JSON determinístico entre pipeline e narrator

**Decisão:** o narrator nunca lê o DB diretamente. Recebe sempre um JSON do dossier_builder. Isso permite:
- Testar narrativa sem acessar DB
- Versionar dossier independente do código
- Reprocessar narrativa com mesmo dossier (consistência)

### 10.4 Validator como "checklist F3", não "aprovador binário"

**Decisão:** o validator retorna lista de warnings, não "pass/fail absoluto". Operador decide se warning é crítico ou aceitável. Preserva julgamento humano.

### 10.5 Zero chamadas a API no correlator

**Decisão:** os 3 detectores são **rule-based puros**. Motivos:
- Custo zero por correlação
- Determinístico (mesma entrada = mesma saída)
- Testável 100% unit
- Auditável (regras explícitas, não "caixa preta")

Se futuro exigir semântica sofisticada, fica **Fase B.2** com fallback Claude — mas hoje rule-based cobre os casos principais.

### 10.6 Preservação de narrativas no DB mesmo ao regenerar

**Decisão:** regerar narrativa cria **nova row** no DB (ID incrementado), não sobrescreve. Histórico forense preservado. Arquivo .md pode ser sobrescrito, mas DB mantém versões.

---

## 11. Métricas Atuais (v0.6.1)
Corpus EVERALDO_SANTAQUITERIA (vault piloto):
messages:           78
files:              ~200+
transcriptions:     117
classifications:    250
visual_analyses:    96 (44 active + 52 archive)
financial_records:  4 (R$ 12.530)
forensic_narratives: 10+
correlations:       38 (10 validadas conf ≥0.70)
Código:
Commits totais:       ~40+
Tags publicadas:      5 versões + 5 safety checkpoints
Testes passando:      480
Arquivos Python:      ~35+
Linhas de código:     ~5.500+
Custos acumulados até v0.6.1:
Desenvolvimento:      ~US$ 1.50
Geração narrativas:   ~US$ 0.40
Total:                ~US$ 2.00 (≈ R$ 10)

---

## 12. Ecossistema Lucas (contexto estendido)

### 12.1 Entidades empresariais

- **Vale Nobre Construtora e Imobiliária Ltda** — obras públicas SEE-MG
- **HCF Investimentos e Participações** — holding, M&A, distressed
- **MOUVI / Moderfit** — marketing/suplementos (contexto paralelo)

### 12.2 Stack pessoal relevante

- **OB1/Jarvis** — sistema de memória semântica via Supabase + pgvector + OpenRouter
- **Canteiro Inteli** — ERP React/TypeScript/Supabase pra obras Vale Nobre
- **Obsidian** — PKM principal (555 notas Zettelkasten)
- **Claude Code** — ferramenta principal de execução autônoma

### 12.3 Aplicações diretas pendentes do rdo-agent

**Alta prioridade:**
- **EE Milton Campos** — disputa judicial Vale Nobre × SEE-MG (rescisão 10% + impedimento 2 anos). Laudo forense cruzado com evidência WhatsApp é peça-chave pra contestação administrativa/judicial.
- **Outras obras SEE-MG** — auditoria preventiva de contratos Vale Nobre.

**Média prioridade:**
- **Rubinella M&A (HCF)** — auditoria pós-aquisição, cruzamento de emails e documentos.
- **Delta Citrus / Frigorífico Bolson** — análise de distressed corporativo.

**Futuro:**
- **Integração com Canteiro Inteli** — ERP fornece GT automaticamente ao rdo-agent.

---

## 13. Instruções pra Nova Conversa de IA

Se você é um novo Claude/IA assumindo este projeto, leia isso **antes de tomar qualquer ação**:

### 13.1 Protocolo de orientação

1. **Leia este documento inteiro** (PROJECT_CONTEXT.md)
2. **Cheque estado atual** do git: `git log --oneline -5 && git tag -l`
3. **Cheque estado do DB**: consulte tabela `forensic_narratives` e `correlations` pra ver últimas gerações
4. **Leia o SESSION_LOG** mais recente em `docs/sessions/`
5. **Confirme com o operador** qual a sessão atual antes de editar código

### 13.2 Nunca faça sem autorização

- Rodar `git reset --hard` ou `git push --force`
- Apagar vault, DB ou backups
- Rodar pipeline em obra **outra que não EVERALDO** até v1.0
- Mudar schema do DB sem migration incremental
- Abandonar convenção de nomenclatura "canal vs obra"
- Sobrescrever narrativas existentes (sempre criar row nova)

### 13.3 Sempre faça

- Safety checkpoint (tag + backup DB) antes de sessão de médio-alto risco
- Commits incrementais (1 fase = 1 commit)
- Testes verde antes de commits
- Validação empírica pós-sessão autônoma (git + pytest + DB + amostra)
- Preservar ID de narrativa ao regenerar (criar nova, não sobrescrever)

### 13.4 Operador tem prioridade absoluta

**Lucas é arquiteto do produto, revisor humano e tomador de decisões.** Sua palavra final vale mais que qualquer análise técnica. Dúvida estratégica? Consulta.

Reconheça dois modos dele:
- **"Autônomo approved"** — ele deu aval pra sessão longa
- **"Step by step"** — ele quer validar cada output antes de seguir

### 13.5 Erro aceitável, fingir que funcionou não

Se algo dá errado, **para e reporta com precisão**. Pior que erro é IA concluir "✅ OK" quando não está.

---

## 14. Versões Futuras Prováveis de Este Documento

Este arquivo será atualizado a cada versão major:
- v0.7.0 → adicionar seção Fase C
- v0.8.0 → adicionar Fases D e E
- v1.0.0 → adicionar UI + produção
- v2.0.0 → reescrever seção 2 (multi-canal formalizado)

**Responsável por atualização:** operador (Lucas) + Claude em conjunto, ao fim de cada sessão autônoma.

---

## 15. Contato e Links

- **Repo GitHub:** https://github.com/lucasferleite2-pixel/rdo-agent
- **Issue tracker:** não formal (usa dívidas numeradas neste doc)
- **Operador:** Lucas Fernandes Leite (Minas Gerais, BR)
- **Empresas operadoras:** Vale Nobre Construtora e HCF Investimentos

---

**FIM DO DOCUMENTO DE CONTEXTO.**

> Se você leu até aqui como IA, você tem **contexto suficiente** pra assumir o projeto sem perda de qualidade. Boa sessão.

