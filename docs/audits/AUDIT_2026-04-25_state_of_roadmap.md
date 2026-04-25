# Auditoria do estado do roteiro — `rdo-agent` / Vestígio

**Data da auditoria:** 25/04/2026
**Branch:** `main` (clean)
**Última tag:** `v1.0.1-markdown-fix`
**Auditor:** Claude Code (Opus 4.7) sob direção do operador (Lucas)
**Escopo:** leitura completa de `docs/PROJECT_CONTEXT.md`, `docs/brand/INTEGRATION_PLAN.md`, todos os `docs/sessions/SESSION_LOG_*.md`, `README.md`, `pyproject.toml`, `git log/tag`, e DB SQLite operacional do corpus piloto (EVERALDO_SANTAQUITERIA).

> **Nota:** este documento é o registro fiel do relatório executivo gerado em 25/04/2026, salvo no repo como artefato versionado para servir de baseline da Sprint de Higiene Documental que seguirá (`v1.0.2-docs-sync`).

---

## 1. Estado atual do produto

**Última tag estável:** `v1.0.1-markdown-fix` (24/04, commit `05eacd8`).
**Última tag major:** `v1.0-vestigio-integrated` (23/04 noite) — primeiro marco v1.x.

**Pipeline forense — módulos prontos** (todos confirmados em `src/rdo_agent/`):

| Módulo | Estado | Origem |
|---|---|---|
| `ingestor/`, `parser/`, `temporal/`, `extractor/`, `orchestrator/`, `utils/` | Camada 1 completa | Sprints 1-3 |
| `transcriber/` (Whisper local) | OK | Sprint 4 |
| `classifier/` (GPT-4o-mini) | OK | Sprint 4 |
| `visual_analyzer/`, `ocr_extractor/`, `financial_ocr/`, `document_extractor/` | OK (Vision V2 calibrado + OCR-first) | Sprint 4 Op8/Op9 |
| `forensic_agent/` (dossier + narrator + validator + correlator + 3 detectores) | OK (Sonnet 4.6, regra ancoragem, semantic_v2 com time_decay) | Sprint 5 Fases A+B+C+E |
| `ground_truth/` (loader YAML + schema) | OK | Sprint 5 Fase C |
| `gt_extractor/` (modos simple e adaptive) | OK | Sprint 5 Fase D |
| `laudo/` (LaudoGenerator + adapter + templates + fontes + markdown→HTML) | OK | Sessão 3 + Sessão 3.8 |
| `web/` | **só `static/`, sem FastAPI app** | placeholder p/ Sessão 4 |

**Outputs implementados:**

- **RDO** markdown + PDF (`generate-rdo` CLI) — Sprint 4 Op5/Op6.
- **Narrativa forense** (.md persistida em `forensic_narratives` + arquivo) — Sprint 5 Fase A.
- **Laudo PDF Vestígio** (50-52 páginas, identidade visual completa) — Sessão 3/3.8.
- **YAML de Ground Truth** (extração interativa) — Sprint 5 Fase D.

**Métricas (verificadas no DB hoje):**

- Suite de testes (último log): **598 passando** (Sessão 3.8). PROJECT_CONTEXT seção 11 ainda diz 480 — desatualizada.
- Dívidas técnicas: 19 fechadas, **11 abertas** (pendentes pós-v1.0): #13, #16, #27, #31, #32, #33, #34, #36, #37, #39, #40.
- Custo cumulativo do projeto: ~US$ 2.85.

---

## 2. Arquitetura do ecossistema

**Repo único `rdo-agent` carrega três camadas de identidade:**

1. **Núcleo técnico** (`src/rdo_agent/`) — package Python, CLI typer (`cli.py` ~1300 linhas, 12 comandos), SQLite blackboard.
2. **Produto comercial Vestígio** — identidade visual, brandbook, design skill, laudo generator (`docs/brand/` + `src/rdo_agent/laudo/`).
3. **Caso operacional Vale Nobre** — corpus piloto e contratos reais (vault EVERALDO_SANTAQUITERIA).

**Mapeamento dos diretórios principais:**

- `src/rdo_agent/` — código de produção (16 subpackages reais).
- `docs/PROJECT_CONTEXT.md` — briefing institucional (fonte canônica).
- `docs/sessions/` — 10 SESSION_LOGs cronológicos (Sprint 4 Op8 → Sessão 3.8).
- `docs/brand/` — referência institucional Vestígio (PDFs, SVGs, design-skill, laudos amostra).
- `docs/ground_truth/EVERALDO_SANTAQUITERIA.yml` — 1º GT estruturado real.
- `docs/ADR-001..004.md` — decisões travadas: transcrição model, classifications schema (v1 e v2), markdown rendering laudo.
- `~/rdo_vaults/EVERALDO_SANTAQUITERIA/` — DB operacional (4.7MB) + mídias + 6 backups.
- `reports/` — RDOs gerados (untracked por política).

**Decisões arquiteturais com SHA travado:**

- Schema `correlations` pairwise 1:1 (commit `f1c305d` em diante, ADR informal seção 10.1).
- `classifications.source_message_id` polimórfico (ADR-002 `4130c6b`, ADR-003 `c92b085`).
- Dossier JSON determinístico narrator↔pipeline (Sprint 5 Fase A, `e5ad914` período).
- Validator F3 como checklist, não pass/fail (Sprint 5 Fase A).
- Correlator rule-based puro, zero API (Sprint 5 Fase B, `5892d9b`-`81d05a1`).
- GT é "orientativo, não aditivo" (descoberta 23/04, Sessão 1).
- Markdown→HTML no adapter, não no template (ADR-004, `14fbc11`).

---

## 3. O que JÁ FOI ENTREGUE

**Tags com release** (cronológica):

| Tag | Conteúdo |
|---|---|
| `v0.2.0-sprint2` | OpenAI integrations |
| `v0.3.0-sprint3-code` | Base de conhecimento + classificador |
| `v0.4.0`–`v0.4.4` | Sprint 4 completo: Vision V2, OCR-first, ledger financeiro, hardening |
| `v0.5.0`/`v0.5.1` | Sprint 5 Fase A: narrador forense Sonnet 4.6 + validator F3 |
| `v0.6.0` | Sprint 5 Fase B: correlator rule-based, 3 detectores |
| `v0.6.1` | Case validation empírica (descobriu 2 contratos no corpus) |
| `v0.7.0-ground-truth-polish` | Fase C + 6 dívidas (Sessão 1) |
| `v0.8.0-forensic-complete` | Fases D+E + 5 dívidas (Sessão 2): GT extractor + V4 adversarial |
| `v1.0-vestigio-integrated` | Laudo Generator integrado (Sessão 3) |
| `v1.0.1-markdown-fix` | Markdown→HTML no laudo (Sessão 3.8, fecha #38) |

**Features funcionais verificáveis** (12 comandos CLI confirmados em `cli.py`):
`ingest`, `status`, `generate-rdo`, `process`, `detect-quality`, `ocr-images`, `classify`, `review`, `narrate` (com `--context`, `--adversarial`, `--min-correlation-conf`, `--skip-cache`), `extract-gt` (`--mode simple|adaptive`), `correlate`, `export-laudo` (`--adversarial`, `--certified`, `--context`, `--config`).

**Identidade visual e ativos institucionais** (em `docs/brand/`):

- Brandbook PDF 34p, design-system.html, showcase.html.
- Wordmarks V01-V06, monogramas M01/M03, lockups L01-L03.
- Paleta sem azul (bordô #6B0F1A, ink, graphite, paper, gold).
- 3 famílias de fonte self-hosted (EB Garamond, Inter, JetBrains Mono).
- Letterhead DOCX, Deck PPTX, social/favicons completos.
- Design-skill agentic (SKILL.md + tokens + UI Kit React).
- 2 laudos amostra: `Laudo-Exemplo-Santa-Quiteria.pdf` (referência) e `Laudo-Real-EVERALDO-v1.0.1.pdf` (50-52 páginas, dados 100% reais).

---

## 4. O que está NO ROTEIRO mas AINDA NÃO FOI FEITO

**Sessões futuras documentadas mas não executadas:**

- **Sessão 4 / v1.1-web-ui** — UI Web (FastAPI + Jinja/htmx), dashboard de narrativas + correlações, deploy `vestigio.legal`. Diretório `src/rdo_agent/web/` existe **só com `static/`**, sem app. Estimativa: 4-6h, $1-2.
- **Sessão 4 / v2.0-alpha (CONSOLIDADOR)** — refactoring semântico "obra↔canal" (breaking change), módulo consolidador multi-canal. **[INCONSISTÊNCIA: dois itens diferentes carregam o rótulo "Sessão 4" em 7.2 e 7.3]**.
- **Sessão 5 / v2.1** — divergências inter-canais, ledger consolidado, relatório executivo da obra.
- **Sessão 6 / v2.2-full-production** — ingestão batch, CLI `consolidate`, validação em obra completa.

**Dívidas técnicas pendentes** (consolidando seção 9.5 + descobertas 3.8):

| # | Descrição curta | Tipo |
|---|---|---|
| #13 | Rename "Pagamentos" → "Discussões financeiras" no RDO | cosmético |
| #16 | Streaming no narrator vs fix de timeout | arquitetural |
| #27 | Detector futuro CONTRACT_RENEGOTIATION | feature |
| #31 | Validator MAX_BODY_CHARS — segmentar em tiers | refactor |
| #32 | MAX_TOKENS dinâmico por scope (overview > day) | arquitetural |
| #33 | Warning file_ids 50% conflita com modo adversarial | bug menor |
| #34 | Documentar deps libpango/libcairo no README | docs |
| #36 | Truncamento inteligente para narrativa V4 > 40k chars | resiliência |
| #37 | pdfplumber quebra section-marks; usar pyMuPDF na validação | testing |
| #39 | Tabelas longas / code blocks markdown sem CSS Vestígio | cosmético |
| #40 | Strip de emoji em narrativa V4 (proibido pelo brandbook) | qualidade |

**Itens conceituais sem código:**

- **Certificação digital real ICP-Brasil** (`--certified` hoje só desenha selo visual; assinatura cripto fica para v2.0).
- **Tabela `events`**: existe no schema, **0 rows** — o adapter usa fallback (`financial_records` + top classifications). Dívida arquitetural latente.
- **Integração Canteiro Inteli → rdo-agent** (mencionada como "futuro" em 12.3).

---

## 5. Casos próprios disponíveis para validação

**Único vault ativo em `~/rdo_vaults/`:** `EVERALDO_SANTAQUITERIA` (+ um backup `.sqlite preE2E_backup_20260419`).

**Estado do corpus EVERALDO** (verificado no DB hoje):

| Métrica | Valor real |
|---|---|
| messages | **226** |
| files | 482 |
| transcriptions | 119 |
| classifications | 250 |
| visual_analyses | 96 |
| financial_records | 4 (R$ 12.530) |
| forensic_narratives | **16** (4 v1 · 7 v2_correlations · 2 v3_gt · 3 v4_adversarial) |
| correlations | **28** (não 38) — 9 com confidence ≥ 0.70 |
| events | 0 |

**Pipeline completo já executado:** ingestão ✅ · transcrição ✅ · classificação ✅ · vision V2 ✅ · OCR financeiro ✅ · narrativa forense ✅ (16 versões) · correlator ✅ · ground truth YAML ✅ · adversarial ✅ · laudo PDF ✅ (v1.0 + v1.0.1 amostras preservadas em `docs/brand/`).

**Outros casos mencionados como alta prioridade mas ainda não ingeridos:** EE Milton Campos (Vale Nobre × SEE-MG), outras obras SEE-MG, Rubinella M&A, Delta Citrus, Frigorífico Bolson. Política de validação trava: nada novo até v1.0 completa — agora liberada.

---

## 6. Inconsistências detectadas

1. **[INCONSISTÊNCIA]** PROJECT_CONTEXT seção 11 declara `correlations: 38 (10 validadas)`. **DB real tem 28**. Sessão 2 (`v0.8.0`) retunou semantic_v2 e o número caiu para 28. Seção 3.1 também repete "38 detectadas EVERALDO". Ambos pontos estão desatualizados.

2. **[INCONSISTÊNCIA]** PROJECT_CONTEXT seção 11 diz `messages: 78`. **DB real tem 226**. O 78 era a contagem de "mensagens texto puro classificáveis" (Sprint 4 Op1), não o total — descrito como total na métrica.

3. **[INCONSISTÊNCIA]** PROJECT_CONTEXT seção 11 ainda diz `Testes passando: 480` (baseline v0.6.1). Último log (Sessão 3.8) reporta **598**.

4. **[INCONSISTÊNCIA]** PROJECT_CONTEXT cabeçalho: "Última atualização: 23/04/2026 — versão v1.0-vestigio-integrated". Mas o doc menciona Sessão 3.8 e dívida #38 fechada (commit 05eacd8 de 24/04). **Versão atual real: v1.0.1-markdown-fix.**

5. **[INCONSISTÊNCIA]** Numeração interna em PROJECT_CONTEXT 5.x: a Sessão 3 (Laudo) aparece como **5.12** e antecede a Sessão 2 (Fases D+E) que está como **5.11**. Ordem cronológica e numérica invertida.

6. **[INCONSISTÊNCIA]** Roadmap seção 7.2 mapeia "SESSÃO 4 → v1.1-web-ui". Seção 7.3 mapeia "SESSÃO 4 → v2.0-alpha". O mesmo rótulo Sessão 4 carrega dois trabalhos distintos.

7. **[INCONSISTÊNCIA]** README.md, seção "Roadmap" (linhas 134-140), ainda diz `Sprint 1 (atual) — Camada 1 completa`. Está congelado pré-Sprint 2 enquanto o resto do README já foi parcialmente atualizado para v1.0 (seção `export-laudo`).

8. **[INCONSISTÊNCIA]** README.md cita "ver `docs/Blueprint_V3.docx`" como spec de referência, mas o blueprint está em `docs/Blueprint_V3_Agente_Forense_RDO.docx`. Path inválido.

9. **[INCONSISTÊNCIA]** `pyproject.toml` declara `version = "0.1.0"`. Tags do repo vão até `v1.0.1`. Versão do package nunca foi bumpada.

10. **[INCONSISTÊNCIA]** README seção Setup pede `python3.11` (`apt install python3.11`). PROJECT_CONTEXT 3.2 diz "Python 3.12". `pyproject.toml` exige `>=3.11`. Inconsistência baixa, mas presente.

11. **[INCONSISTÊNCIA]** PROJECT_CONTEXT seção 3.3 lista subpackages sob nomes que **não correspondem** ao layout real do `src/`. Doc menciona `ingestion/`, `classification/`, `vision/`, `financial/`, `rdo/`. No código os diretórios reais são `ingestor/`, `classifier/`, `visual_analyzer/`, `financial_ocr/` (não há `rdo/`).

12. **CHANGELOG.md** — referenciado no prompt da auditoria como leitura, **não existe** no repo. Histórico vive nos SESSION_LOGs e em `git log`.

13. **Tag órfã sem doc próprio:** `safety-checkpoint-pre-combo-12` (22/04). Não citada em nenhum SESSION_LOG existente em `docs/sessions/` (provavelmente Sprint 4 OP10/OP11 mas não nomeada explicitamente).

14. **Documento órfão na raiz:** `SESSION_LOG_AUTONOMOUS.md` (~9KB, 20/04) está em `~/projetos/rdo-agent/` raiz, fora do `docs/sessions/`. Outros logs migraram, este ficou.

15. **Sinal de regressão:** `events` table existe no schema mas **nunca foi populada** — o pipeline original previa popular essa tabela (descrita em ADR-002, schema.sql). Adapter de laudo trabalha em torno disso. Não chega a ser regressão (nunca rodou), mas é dívida estrutural.

---

## Próximas ações sugeridas (sem prioridade atribuída)

1. **Atualizar PROJECT_CONTEXT.md** para refletir v1.0.1: corrigir métricas (correlations 28, testes 598, narrativas 16), renumerar 5.11/5.12, desambiguar "Sessão 4" entre web-ui e consolidador, atualizar cabeçalho de versão.
2. **Sincronizar README.md** — apagar bloco de Roadmap obsoleto, corrigir path do Blueprint, alinhar Python 3.11 vs 3.12, bumpar `pyproject.toml` para 1.0.1.
3. **Disparar Sessão 4 (UI Web)** — ativos visuais já estão prontos em `docs/brand/design-skill/` e `src/rdo_agent/web/static/`; falta o app FastAPI e o port das views React→Jinja.
4. **Iniciar segundo caso** — agora que v1.0 está liberada, ingestar EE Milton Campos validaria o pipeline em corpus diferente sem mais contaminar EVERALDO; revela quanto do código está hardcoded ao caso piloto.
5. **Endereçar dívida arquitetural #35 (events)** — decidir se popula a tabela ou se remove do schema; o fallback do adapter masca o problema mas não resolve.

---

## Maior incoerência detectada

A documentação institucional (PROJECT_CONTEXT.md + README.md) ainda se apresenta como projeto Sprint 1/v0.6.1 em várias seções — métricas, roadmap, estrutura de pastas, versão do package — enquanto o código real entrega um produto v1.0.1 com laudo forense completo, cinco sprints encerrados e identidade visual Vestígio integrada; quem chega novo lendo os docs subestima drasticamente o que já foi construído.

---

## Decisão downstream

Com base nesta auditoria, foi aberta a Sprint de Higiene Documental (`v1.0.2-docs-sync`) que resolve as 15 inconsistências em commits atômicos por fase. Esta sprint **não toca código de produção** — é puramente alinhamento de markdown + 1 linha em `pyproject.toml`.
