# Sessão 6 — Resiliência core (state machine + crash recovery)

**Início:** 2026-04-25 (tarde/noite)
**Término:** 2026-04-25
**Duração:** ~3h
**Meta:** Fechar 4 dívidas pendentes pós-v1.1 (#43, #44, #53, #54)
preparando o pipeline pra escala (5GB+ corpus em sessões futuras).
**Teto de custo:** US$ 0.00–0.20
**Tag pre-sessão:** `safety-checkpoint-pre-sessao6`
**Tag final:** `v1.2-resilient-pipeline`

## Resumo executivo

**4 dívidas fechadas + 5 premissas auditadas + crash recovery
validado contra EVERALDO real.**

| Dívida | Tipo | Commit |
|---|---|---|
| #44 | PipelineStateManager (wrapper sobre `tasks`) | `0651819` |
| #43 | dedup defensivo via content_hash em messages | `de4c33e` |
| #53 | logging JSON estruturado + watch + stats CLI | `7c26dae` |
| #54 | circuit breaker + rate limiter + cost quota | `a254bbd` |

- Suite: 643 → **698 testes** (+55 novos: 14 + 10 + 10 + 21)
- Custo API: **US$ 0.00** (tudo é código + validação local; sem
  chamadas a APIs externas)

## Phase 6.0 — Discovery (premissas auditadas)

Antes de qualquer edit em produção, as 5 premissas do plano foram
verificadas. Resultado:

| # | Premissa | Veredito | Evidência |
|---|---|---|---|
| P1 | DB tem 9 tabelas alvo + adicionar processing_jobs/pipeline_state/error_log | **CONFIRMED**, mas processing_jobs não foi adicionada (ver P2) | `.tables` no DB |
| P2 | Não há state machine unificada | **REFUTED** | `tasks` existe com 675 rows e schema completo |
| P3 | Logs hoje são print/logging plano; sem `~/.rdo-agent/logs/` | **CONFIRMED** | dir não existe; json.dumps usado só para api_calls table |
| P4 | Não há retry/backoff sistematizado | **PARTIAL** | retry per-module existe (narrator/transcriber/visual_analyzer); falta circuit breaker + rate limiter cross-module |
| P5 | Re-ingestão duplica registros | **PARTIAL** | há dedup via PK determinístico (msg_id + sha256-based file_id), mas content_hash defensivo agrega resiliência |

A descoberta P2 mudou o plano da Fase 6.1 (ver ADR-007).

## Plano executado

| Fase | Descrição | Commit |
|---|---|---|
| 6.0 | Safety tag + discovery + report ao operador (pausa) | — |
| 6.1 | #44 PipelineStateManager wrapper + CLI + 14 testes | `0651819` |
| 6.2 | #43 messages.content_hash + UNIQUE + INSERT OR IGNORE + 10 testes | `de4c33e` |
| 6.3 | #53 StructuredLogger + watch/stats CLI + 10 testes | `7c26dae` |
| 6.4 | #54 CircuitBreaker + RateLimiter + CostQuota + 21 testes | `a254bbd` |
| 6.5 | Validação empírica EVERALDO (crash + recovery + logger) | — |
| 6.6 | SESSION_LOG + ADR-007 + atualiza PROJECT_CONTEXT + README | (este) |
| 6.7 | Release v1.2-resilient-pipeline | (próximo) |

## Decisões arquiteturais e desvios

### Wrapper sobre `tasks` em vez de tabela nova (#44)

Documentado em **ADR-007**. Resumo: `tasks` existe, populada,
testada. Wrapper ergonômico entrega o valor pedido (status,
recovery helpers, CLI) sem migration. Plano original previa nova
tabela `processing_jobs` — refutado por discovery.

### Dedup em **2 camadas** (#43)

Não 1 só camada como o plano sugeria. Camadas:

1. **PK determinístico** (já existia): `message_id =
   msg_{obra}_L{line}` para messages, `file_id = f_{sha256[:12]}`
   para files. Re-ingest de ZIP **idêntico** falha com IntegrityError
   (messages) ou silenciosamente skipa (files).
2. **content_hash** (novo nesta sessão): `sha256(timestamp || sender
   || content)` truncado em 16 hex chars, com UNIQUE(obra,
   content_hash) parcial. Re-ingest de ZIP **editado** (linhas
   deslocadas) ainda dedupa.

Comportamento de inserção: `INSERT OR IGNORE` em `_write_messages_to_db`,
retornando `(inserted, skipped)` para o caller logar.

Backfill aplicado em vault EVERALDO: 226/226 messages backfilled
durante o `init_db()` na primeira chamada após a migration.

### Retry per-module preservado (#54)

Decisão explícita de **não duplicar** o retry que já funciona em
`narrator.py` (`ANTHROPIC_MAX_RETRIES=3`, `RETRY_DELAYS_SEC`),
`transcriber/__init__.py` e `visual_analyzer/__init__.py`. As
primitivas centralizadas (CircuitBreaker, RateLimiter, CostQuota)
são **complementares**: ficam **acima** do retry per-module e
adicionam quebra global quando o serviço inteiro está degradado.

Singletons cross-module: `get_openai_circuit()`,
`get_anthropic_circuit()`, `get_openai_rate_limiter()`,
`get_anthropic_rate_limiter()`. Nenhum integration wiring nesta
sessão — primitivas estão prontas para uso em sessões futuras
quando precisarem ser ativadas (Sessão 8+, processamento em escala).

### Watch sem follow-tail (#53)

Plano sugeria `rdo-agent watch` em modo tail-follow. Implementação
inicial é **snapshot e sai** — print dos últimos N registros e
encerra. Tail-follow real (com poll loop, Ctrl+C, etc) fica como
evolução futura — escopo desta sessão é entregar a fundação
JSONL + helpers de leitura.

## Métricas finais

### Testes adicionados

| Arquivo | Testes |
|---|---|
| `tests/test_pipeline_state_manager.py` | 14 |
| `tests/test_dedup_content_hash.py` | 10 |
| `tests/test_structured_logger.py` | 10 |
| `tests/test_resilience_primitives.py` | 21 |
| **Total** | **55** |

Suite: 643 → 698 testes verde, ~60s execução completa.

### Validação empírica

Crash + recovery testado contra vault EVERALDO real:

```python
# 1. Forçou task #1 (originalmente done) para running sem finished_at
# 2. PipelineStateManager.status() detectou: resumable count=1
# 3. reset_running() devolveu para pending (1 task afetada)
# 4. Estado pos-reset: 0 resumable, 674 done + 1 pending
# 5. Estado original restaurado (re-marcado como done com timestamp)
```

Logger end-to-end também validado: emitiu 5 events demo, `watch`
mostrou no terminal formatado, `stats` agregou counts/cost/durations/
falhas em tabelas rich. Demo logs limpos depois.

### Custos

- Sessão 6 (este): **US$ 0.00**
- Acumulado projeto total: ~US$ 3.16 (inalterado vs Sessão 5)

## Próximos passos (pós-v1.2)

Roadmap reformulado (PROJECT_CONTEXT addendum 25/04 noite):

- **Sessão 7 → v1.3-safe-ingestion**: #41 (ingestão streaming),
  #42 (mídia copy-on-demand), #55 (pre-flight check), ADR-006
  (decisão sobre tabela `events`).
- **Sessões 8+**: escala custo, escala analítica, multi-canal,
  outputs modulares. Apenas então UI Web (Sessão 15+).

## Custos da sessão

| Op | Descrição | Custo (USD) |
|---|---|---:|
| 6.0 | Discovery (puro grep + sqlite reads) | 0.0000 |
| 6.1 | #44 PipelineStateManager (puro código) | 0.0000 |
| 6.2 | #43 dedup content_hash (puro código + migration) | 0.0000 |
| 6.3 | #53 logger JSON (puro código) | 0.0000 |
| 6.4 | #54 resilience primitives (puro código) | 0.0000 |
| 6.5 | Validação empírica (queries SQLite locais) | 0.0000 |
| 6.6 | Docs (puro markdown) | 0.0000 |
| 6.7 | Release | 0.0000 |
| **Total sessão** | | **US$ 0.00** |

Teto autorizado: US$ 0.20. Usado: 0%.
