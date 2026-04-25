# Sessão 8 — Eficiência custo (transcribe + classify)

**Início:** 2026-04-25 (noite)
**Término:** 2026-04-25
**Duração:** ~3h
**Meta:** Fechar 2 dívidas (#45 transcribe checkpoint, #46 classify
cache + dedup + batch). Reduzir custo OpenAI em 70-90% no estágio
textual, viabilizando corpus de produção (5GB+).
**Teto de custo:** US$ 0.20–0.80 (validação empírica)
**Tag pre-sessão:** `safety-checkpoint-pre-sessao8`
**Tag final:** `v1.4-efficient-classify`

## Resumo executivo

**2 dívidas fechadas + 1 dívida nova registrada (#59) + ADR-008
+ 8 premissas auditadas com 1 REFUTED crítica.**

| Item | Mecanismo entregue | Commit |
|---|---|---|
| #45 | `transcribe_pending` orchestrator com idempotência, checkpoint, integração GRUPO 2 | `a042b5d` |
| #46 nível 1 | `ClassifyCache` exact-match com normalização e versionamento | `1c7253b` |
| #46 nível 2 | `JaccardDedup` léxico com janela rolante (sem deps novas) | `1c7253b` |
| #46 nível 3 | `BatchClassifier` para OpenAI Batch API (50% desconto) | `1c7253b` |
| ADR-008 | rationale do pipeline 3-tier classify | (este) |
| #59 | upgrade para sentence-transformers se evidência de produção justificar | (registrada em PROJECT_CONTEXT) |

- Suite: 738 → **791 testes** (+53 novos: 10 + 30 + 13)
- Custo API: **US$ 0.00** (zero; tudo isolado via mocks)
- EVERALDO **intacto** (zero `--regenerate`).

## Phase 8.0 — Discovery (premissas auditadas)

| # | Premissa | Veredito |
|---|---|---|
| **P1** | Whisper LOCAL (não API), grátis | **REFUTED — premissa errada** |
| P2 | classifier OpenAI gpt-4o-mini, sem cache | CONFIRMED |
| P3 | sem embeddings/sentence-transformers | CONFIRMED |
| P4 | tasks tem TRANSCRIBE + CLASSIFY task_types | CONFIRMED |
| P5 | transcriptions sem UNIQUE em file_id | PARTIAL (guardrail manual existe) |
| P6 | classifications sem UNIQUE óbvio | CONFIRMED |
| P7 | `cost_event` existe mas não wired | CONFIRMED |
| P8 | `CostQuota` existe, não integrada em produção | CONFIRMED |

**P1 refutada**: `transcriber/__init__.py:9` declara "Chama API
externa (OpenAI Whisper)"; linha 41 `MODEL = "whisper-1"`; linha
45 `COST_USD_PER_MINUTE = 0.006`. Implicações: o plano original
incluía "modelo Whisper configurável (small/medium/large-v3)" —
isso não existe na API OpenAI (que serve apenas `whisper-1`). Drop
desse subitem do escopo após alinhamento com operador.

## Plano executado

| Fase | Descrição | Commit |
|---|---|---|
| 8.0 | Safety tag + discovery + report ao operador (pausa) | — |
| 8.1 | #45 `transcribe_pending` + 10 testes | `a042b5d` |
| 8.2 | #46 cache + Jaccard + batch + 43 testes | `1c7253b` |
| 8.3 | Validação empírica (3 mocks, EVERALDO intacto) | — |
| 8.4 | SESSION_LOG + ADR-008 + dívida #59 + docs | (este) |
| 8.5 | Release v1.4-efficient-classify | (próximo) |

## Decisões arquiteturais e desvios

### Drop de "modelo Whisper configurável" (Phase 8.1)

Premissa P1 refutada implica que `RDO_AGENT_WHISPER_MODEL` /
`--whisper-model` não fazem sentido — a API OpenAI Whisper só serve
`whisper-1`. O subitem "reprocessamento por troca de modelo" também
caiu (não há para onde trocar). Reprocessamento continua disponível
mas com motivação diferente: retry de falhas ou correção manual,
não troca de qualidade.

Whisper LOCAL (faster-whisper / openai-whisper open-source) fica
para uma dívida futura caso o operador queira opção self-hosted
gratuita — não é desta sessão.

### Wrapper sobre `transcribe_handler` (Phase 8.1)

`transcribe_pending` foi adicionado em `transcriber/__init__.py`
como **alto nível** sobre o `transcribe_handler` existente:

- Drena tasks via `PipelineStateManager.claim`
- Idempotência: query `SELECT id FROM transcriptions WHERE file_id=?`
  ANTES de chamar Whisper (poupa $0.006/min real, não só tempo)
- `force=True` ignora dedup; default `False` skipa silenciosamente
- Falha do handler → `state.fail` + `logger.stage_failed` + loop
  continua
- `CircuitOpenError` → `break` (não adianta tentar próximo)
- `QuotaExceededError` → `fail` + `break` (operador retoma quando
  quiser)
- Cost tracking: query `api_calls.cost_usd` da row recém-inserida,
  acumula, emite `cost_event(api=openai, model=whisper-1)`

Não duplica retry: `_call_whisper_with_retry` interno do handler
continua intacto. `CircuitBreaker openai_whisper` (singleton novo
em `resilience.py`) é camada superior cross-module, não substituição.

### Jaccard agora, embeddings depois (Phase 8.2)

Decisão tomada na Phase 8.0 com operador. Razões:

- `sentence-transformers` traz ~2GB de PyTorch como dep.
- Para corpus piloto (250 msg, $0.025 total) overhead não vale.
- Jaccard pega 80% do ganho semântico em corpus de WhatsApp pt-BR
  (mensagens curtas, vocabulário concreto, números/nomes).
- Embeddings vira dívida **#59** com critérios objetivos para
  ativação (hit rate < 15% em 50k+ mensagens, OU narrator V4
  reclamando de ruído classificacional, OU operador identificando
  falsos negativos em revisão).

### Validação NÃO destrutiva (Phase 8.3)

Plano original previa `rdo-agent classify --regenerate` em EVERALDO.
Substituído por 3 validações isoladas que **não tocam o vault de
produção**:

1. **Cache + Jaccard 3-tier em fixture sintético** (60 msg):
   38% redução de chamadas API com mensagens realistas pt-BR;
   91% em corpus mais homogêneo. Mecanismo validado.
2. **Batch lifecycle end-to-end via mock**: 5 requests submit →
   poll progressivo (validating → in_progress → completed) →
   fetch_results parseou todos com tokens extraídos.
3. **Transcribe checkpoint com crash simulado**: 3 tasks pending →
   `state.claim` 1 (running, sem finished_at) → `state.reset_running`
   detectou e reverteu → `transcribe_pending` retomou e processou
   3 tasks.

Custo total Phase 8.3: **US$ 0.00**. EVERALDO intacto (250
classifications preservadas).

### Wiring `classify_pending` orchestrator: adiado

O loop integrado das 3 camadas (cache → Jaccard → batch/sync) não
foi implementado nesta sessão. Razões:

- Caso real ainda não é Sessão 8 (que entrega primitivas).
- Wiring fino exige decisão sobre quando flushear o batch buffer
  (tamanho fixo? por timer? ao fim do drenamento?), e a heurística
  certa só aparece em Sessão 11 (validação em corpus 5GB+).
- As 3 primitivas isoladas já entregam valor: caller pode usar a
  combinação que faz sentido na sua estratégia.

A função `classify_pending` que une tudo será adicionada na sessão
que primeiro precisar dela em produção (provavelmente Sessão 11).

## Métricas finais

### Testes adicionados

| Arquivo | Testes |
|---|---|
| `tests/test_transcribe_pending.py` | 10 |
| `tests/test_classify_cache_and_jaccard.py` | 30 |
| `tests/test_batch_classifier.py` | 13 |
| **Total** | **53** |

Suite: 738 → 791 testes verde, ~63s execução completa.

### Custos

- Sessão 8 (este): **US$ 0.00** (mocks; sem chamadas reais)
- Acumulado projeto total: ~US$ 3.16 (inalterado)

### Dívida nova registrada

**#59** — Upgrade dedup semântico para embeddings se evidência
empírica (Sessão 11) mostrar Jaccard insuficiente. Triggers:

- hit rate Jaccard < 15% em 50k+ mensagens, OU
- narrator V4 reclamando de ruído classificacional, OU
- operador identifica falsos negativos em revisão.

Estimativa: 1 sprint pequena. Documentada em PROJECT_CONTEXT
section 9.10.

## Próximos passos (pós-v1.4)

GRUPO 3 (eficiência custo) **incompleto** — Sessão 9 fecha:

- **Sessão 9 → v1.5-efficient-vision**: #47 (vision filtro
  cascata), #48 (frames de vídeo), #49 (OCR roteamento).

GRUPO 3 inteiro completo, abre GRUPO 4 (escala analítica) na
Sessão 10.

## Custos da sessão

| Op | Descrição | Custo (USD) |
|---|---|---:|
| 8.0 | Discovery + investigação P1-P8 | 0.0000 |
| 8.1 | #45 transcribe_pending (puro código + mock testes) | 0.0000 |
| 8.2 | #46 cache + Jaccard + batch (puro código + mocks) | 0.0000 |
| 8.3 | Validação empírica (3 mocks, EVERALDO intacto) | 0.0000 |
| 8.4 | Docs + ADR-008 + #59 | 0.0000 |
| 8.5 | Release | 0.0000 |
| **Total sessão** | | **US$ 0.00** |

Teto autorizado: US$ 0.80. Usado: 0% (validação ficou inteira em
mocks).
