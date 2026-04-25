# Sessão 10 — Escala analítica (correlator paralelo + narrator hierárquico + cache narrativas)

**Início:** 2026-04-25 (noite)
**Término:** 2026-04-25
**Duração:** ~3h
**Meta:** Fechar 3 dívidas analíticas (#50 correlator paralelo +
janela, #51 narrator hierárquico, #52 cache narrativas) abrindo
GRUPO 4 (escala analítica).
**Teto de custo:** US$ 0.30–1.50
**Tag pre-sessão:** `safety-checkpoint-pre-sessao10`
**Tag final:** `v1.6-scale-analytics`

## Resumo executivo

**3 dívidas fechadas + 3 novas registradas (#61, #62, #63) +
3 ADRs novos.**

| Item | Mecanismo entregue | Commit |
|---|---|---|
| #50 | Correlator paralelo (4 workers ProcessPool) + janela by-detector configurável | `b1c94c0` |
| #51 | Narrator hierárquico cascade day→week→month→overview + migration relax CHECK | `73dedc0` |
| #52 | Cache binário de narrativas (prompt_template_hash + dossier_hash) + invalidate | `87b7e03` |
| ADR-010 | Correlator paralelo inter-detector + critério para #61 | (este) |
| ADR-011 | Narrator hierárquico + file_ids preservation + relax CHECK | (este) |
| ADR-012 | Cache binário, fuzzy adiado em #62 | (este) |
| #61 (registrada) | Paralelismo intra-detector, sob trigger | (PROJECT_CONTEXT) |
| #62 (registrada) | Cache fuzzy de narrativas, sob trigger | (PROJECT_CONTEXT) |
| #63 (registrada) | narrate/correlate como task_types, sob trigger | (PROJECT_CONTEXT) |

- Suite: 848 → **899 testes** (+51 novos: 14 + 18 + 19)
- Custo API: **US$ 0.00** (validação não-destrutiva, 1 narrativa
  real planejada foi pulada porque cache + cascade tests já
  cobriram; mock + read-only EVERALDO suficiente)
- EVERALDO **intacto** (17 narrativas + 28 correlations
  preservadas)

## Phase 10.0 — Discovery (premissas auditadas)

| # | Premissa | Veredito |
|---|---|---|
| **P1** | Correlator é O(N²) em loop único | **REFUTED** — orquestrador thin, complexidade está dentro dos detectores |
| P2 | 4 detectores existentes | CONFIRMED |
| **P3** | Sem janela temporal | **REFUTED** — cada detector já tem WINDOW próprio (TEMPORAL=30min, SEMANTIC=3d, MATH=48h, RENEGOTIATION=30d) |
| P4 | Single-threaded | CONFIRMED |
| **P5** | Sem hierarquia | **CONFIRMED + LIMITAÇÃO** — schema CHECK trava `scope IN ('day', 'obra_overview')` |
| P6 | UNIQUE em (obra, scope, scope_ref, dossier_hash) | CONFIRMED — cache implícito por evidência já existe |
| P7 | Sem prompt_template_hash | CONFIRMED |
| P8 | tasks sem narrate/correlate | CONFIRMED |

**Defensivas Q1-Q4**:
- Q1 ✓ multiprocessing.Pool funciona em WSL2
- Q2 ✓ SQLite WAL já habilitado
- Q3 ✓ forensic_agent/{correlator.py, narrator.py, detectors/, ...}
- Q4 ✓ schema confirmado, CHECK identificado

P1 e P3 refutadas mudaram a estratégia: paralelismo inter-detector
(ganho 4× fácil) em vez de re-arquitetar O(N²); janela by-detector
configurável (cada detector mantém seu WINDOW).

## Plano executado

| Fase | Descrição | Commit |
|---|---|---|
| 10.0 | Safety tag + discovery + report (pausa) | — |
| 10.1 | #50 correlator paralelo + janela + 14 testes | `b1c94c0` |
| 10.2 | #51 narrator hierárquico + relax CHECK + 18 testes | `73dedc0` |
| 10.3 | #52 cache binário + 19 testes | `87b7e03` |
| 10.4 | Validação empírica não-destrutiva | — |
| 10.5 | SESSION_LOG + 3 ADRs + 3 dívidas registradas | (este) |
| 10.6 | Release v1.6-scale-analytics | (próximo) |

## Decisões arquiteturais e desvios

### Drop O(N²) refactor → paralelismo inter-detector (Phase 10.1)

P1 refutou a premissa fundamental do plano. Em vez de "refatorar
correlator para reduzir O(N²)", entregamos:

- Paralelismo **inter-detector**: 4 workers (1 por detector) via
  `ProcessPoolExecutor`. Em corpus grande: até 4× speedup.
- Janela configurável por detector via `DetectorWindows` dataclass
  + helper `all_days(N)`.
- ADR-010 trava o rationale + critério de reabertura para
  paralelismo **intra-detector** (dívida #61).

### Relax CHECK constraint (Phase 10.2)

Schema legado tinha `CHECK (scope IN ('day', 'obra_overview'))`.
Migration `_migrate_sessao10_relax_narratives_scope_check` recria
a tabela sem CHECK, preservando dados + UNIQUE + índices. Single
source of truth de scopes válidos passa para `narrator.VALID_SCOPES`
(Python).

### Cache binário, fuzzy adiado (Phase 10.3)

Mesma filosofia da Sessão 8 (Jaccard agora, sentence-transformers
depois) e Sessão 9 (heurística agora, CLIP depois): hash binário
agora, similarity fuzzy quando triggers ativarem.

ADR-012 documenta os triggers de #62: typo-induced re-pagamento,
3+ ocorrências, custo agregado > $5.

### Adiamento de narrate/correlate como task_types

Plano original mencionava "adicionar task_type='correlate' em
tasks". Operador alinhou: **adiado** com dívida #63 explícita
(triggers: crash mid-correlate sem recovery automático, corpus
grande com narrate > 30min). State machine atual continua
cobrindo transcribe/classify/vision; correlate e narrate ficam
como CLI direto até trigger ativar.

### Validação empírica reduzida ($0.00 em vez de $0.02)

Plano original previa 1 week-narrative real (~$0.02). Decidi
**pular** porque:

- Cascade já é testado com mock narrate_fn em
  `test_narrate_hierarchy_cascade_calls_levels_in_order`.
- file_ids preservation testado em
  `test_compose_preserves_file_ids` + validado
  empiricamente em EVERALDO read-only (Phase 10.4 mostrou 7
  children da W15 com file_ids intactos).
- Promessa não-destrutiva: gerar 1 week-narrative em EVERALDO
  adicionaria row no DB de produção, exigindo cleanup manual.

Custo total Phase 10.4: **$0.00**. EVERALDO intacto.

## Métricas finais

### Testes adicionados

| Arquivo | Testes |
|---|---|
| `tests/test_parallel_correlator.py` | 14 |
| `tests/test_narrator_hierarchy.py` | 18 |
| `tests/test_narrative_cache.py` | 19 |
| **Total** | **51** |

Suite: 848 → 899 testes verde, ~83s execução completa.

### Validação empírica

**Correlator paralelo vs sequential em EVERALDO read-only**:
- Sequential: 29 correlations em 37ms
- Parallel (4 workers): 29 correlations em 41ms (0.90× — overhead
  de spawn supera ganho em corpus pequeno)
- **Resultado IDÊNTICO** ao sequential (regressão zero)
- 4 detectores reportaram seus counts via `CorrelationStats`

**Janela by-detector**:
- 0 dias: 0 correlations (esperado)
- Default (cada detector seu WINDOW): 29 correlations
- 60 dias para todos: 235 correlations (mais matches mas com
  confidences mais baixas — janela mais larga gera ruído)

**Hierarchy buckets em EVERALDO real (read-only)**:
- 6 day-narratives → 2 week buckets (W15 + W16)
- W15: 7 day-narratives children, file_ids preservados
- 0 week-narratives criadas (não chamamos narrate real)

**Cache binário (fixture sintética)**:
- Hit perfeito (5 dimensões batem): OK
- Miss por typo no prompt: OK
- Miss por dossier_hash diferente: OK
- `invalidate` força miss em runs futuros sem deletar narrativa
- `stats(obra)`: 1 total, 1 with_hash, 0 legacy

### Custos

- Sessão 10 (este): **US$ 0.00** (zero chamadas API)
- Acumulado projeto total: ~US$ 3.16 (inalterado)

### Dívidas novas registradas

- **#61** — Paralelismo intra-detector. Triggers: corpus 100k+ com
  detector individual dominando tempo, profiling identifica
  bottleneck.
- **#62** — Cache fuzzy de narrativas. Triggers: typo-induced
  re-pagamento, 3+ ocorrências documentadas, custo agregado > $5.
- **#63** — narrate/correlate como task_types em state machine.
  Triggers: crash mid-correlate sem recovery, corpus 50k+ com
  narrate/correlate > 30min, operador reporta retomada manual.

## Próximos passos (pós-v1.6)

- **Sessão 11 → v1.7-validated-at-scale**: validação real em
  corpus grande (5GB+). Espera-se que paralelismo paguem em corpus
  > 10k mensagens. Triggers de #61/#62/#63 podem ativar baseado
  no que aparecer.
- GRUPO 4 fica completo após Sessão 11 — corpus de 5GB+ deve
  caber em $30-60 (vs $200-450 sem cache + paralelismo).

## Custos da sessão

| Op | Descrição | Custo (USD) |
|---|---|---:|
| 10.0 | Discovery (sqlite + grep + multiprocessing test) | 0.0000 |
| 10.1 | #50 correlator paralelo (puro código + mocks) | 0.0000 |
| 10.2 | #51 hierarchy + migration relax CHECK | 0.0000 |
| 10.3 | #52 cache binário + migration | 0.0000 |
| 10.4 | Validação empírica (read-only EVERALDO + fixtures) | 0.0000 |
| 10.5 | Docs + 3 ADRs + 3 dívidas | 0.0000 |
| 10.6 | Release | 0.0000 |
| **Total sessão** | | **US$ 0.00** |

Teto autorizado: US$ 1.50. Usado: 0% (validação real foi pulada
em favor de testes mock + read-only EVERALDO).
