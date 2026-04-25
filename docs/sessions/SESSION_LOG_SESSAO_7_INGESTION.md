# Sessão 7 — Pre-flight check + ingestão segura

**Início:** 2026-04-25 (noite)
**Término:** 2026-04-25
**Duração:** ~2h
**Meta:** Fechar 3 dívidas + 1 ADR pendentes (#41, #42, #55, ADR-006)
preparando o pipeline pra processar corpus de produção (5GB+) sem
estourar RAM, sem extrair tudo no início, e com custo conhecido.
**Teto de custo:** US$ 0.00–0.20
**Tag pre-sessão:** `safety-checkpoint-pre-sessao7`
**Tag final:** `v1.3-safe-ingestion`

## Resumo executivo

**3 dívidas fechadas + ADR-006 resolvido + validação empírica em
corpus real.** Fim do GRUPO 2 (resiliência) do roadmap reformulado.

| # | Mudança | Commit |
|---|---|---|
| ADR-006 | tabela `events` REMOVIDA (opção B) | `44f730a` |
| #41 | ingestão streaming + iter_chat_messages | `3a575bc` |
| #42 | mídia copy-on-demand via MediaSource | `2bfc83b` |
| #55 | pre-flight check + CLI `estimate` | `6e0e772` |

- Suite: 698 → **738 testes** (+40 novos: 12 streaming + 16 media + 12 preflight)
- Custo API: **US$ 0.00**
- Validação empírica: estimate em ZIP real do EVERALDO previu 226
  mensagens (= DB real), streaming ingestou 226 em 29ms com dedup
  duplo ativo.

## Phase 7.0 — Discovery (premissas)

| # | Premissa | Veredito |
|---|---|---|
| P1 | chat.txt lido inteiro em RAM | **CONFIRMED** — `parser._read_text` faz `read_text()` eager |
| P2 | Mídias copiadas eager via `extractall` | **CONFIRMED** — `ingestor:193` `z.extractall(media_dir)` |
| P3 | Sem pre-flight estimate | **CONFIRMED** — grep retornou vazio, `~/.rdo-agent/` não existia |
| P4 | events table 0 rows + adapter usa fallback | **CONFIRMED** — schema define, código nunca lê/escreve |

Investigação extra para ADR-006: `grep` exaustivo em `src/` e `tests/`
confirmou **zero referências** de produção à tabela `events`. Único
"fallback" no adapter é a única implementação real desde v1.0.

## Plano executado

| Fase | Descrição | Commit |
|---|---|---|
| 7.0 | Safety tag + discovery + report ao operador | — |
| 7.1 | ADR-006: REMOVE table events (opção B) | `44f730a` |
| 7.2 | #41 streaming parser + write_messages_streaming + 12 testes | `3a575bc` |
| 7.3 | #42 MediaSource copy-on-demand + 16 testes | `2bfc83b` |
| 7.4 | #55 preflight_check + CLI `estimate` + 12 testes | `6e0e772` |
| 7.5 | Validação empírica EVERALDO (estimate + streaming end-to-end) | — |
| 7.6 | SESSION_LOG + ADR-006 atualizado + README | (este) |
| 7.7 | Release v1.3-safe-ingestion | (próximo) |

## Decisões arquiteturais e desvios

### ADR-006 — REMOVE em vez de POPULATE (Phase 7.1)

Decisão tomada com base em evidência:

- 0 rows + 0 INSERT/SELECT no codebase + 0 referências em testes.
- Adapter constrói cronologia desde v1.0 via `classifications +
  financial_records`; nunca foi "fallback" — sempre foi a
  implementação.
- Manter tabela dormente perpetua confusão sobre "qual é a fonte
  da verdade" em qualquer leitor novo do schema.
- Se demanda futura aparecer (consolidador multi-canal Sessão 12),
  desenhar do zero é melhor que ressuscitar legacy de 9 colunas
  nunca validada contra caso real.

Migration `_migrate_sessao7_drop_events_table` é idempotente
(`DROP IF EXISTS`). `schema.sql` perde o bloco `CREATE TABLE
events`; comentário-lápide preserva a pista para arqueologia
futura.

### Streaming preserva contrato eager (Phase 7.2)

`parse_chat_file()` virou wrapper de uma linha:
`return list(iter_chat_messages(...))`. Todos os 20 testes existentes
em `test_parser.py` passam sem mudança. O caller que precisar de
streaming chama `iter_chat_messages` direto. Decisão: **não quebrar
ninguém**, apenas oferecer caminho lazy ao lado.

`_detect_encoding` faz probe nos primeiros 64KB (não materializa o
arquivo de GBs) para escolher utf-8 vs latin-1. Detecção de formato
(dash vs bracket) faz primeira passada parando na primeira linha
que casa, segunda passada reabre o arquivo já com formato fixado —
custo desprezível (1 abertura extra) e cleaner que carregar buffer.

### MediaSource não troca o ingest atual (Phase 7.3)

`ingest_zip` continua fazendo `extractall` por enquanto.
`MediaSource` é a **primitiva** que permite handlers individuais
(transcribe, vision, ocr) migrarem para copy-on-demand em sessões
futuras (8/9). Esta sessão entrega a fundação; wiring fino fica
para quando os handlers forem otimizados.

Política de materialização documentada no docstring (audio →
materialize, image → bytes, etc.) mas não enforced no MediaSource —
cada handler decide.

### Preflight standalone, sem auto-wiring no ingest (Phase 7.4)

Plano original sugeria que `rdo-agent ingest` rodasse pre-flight
automaticamente e exigisse confirmação interativa quando custo > $50
ou disco crítico. Decisão de escopo: **desta sessão, só o comando
`estimate` standalone**. Auto-wiring com prompt interativo no ingest
introduz ergonomia que merece sua própria iteração e ainda há
chance de mudar a heurística antes (Sessão 8 vai trazer dados de
custo real). O comando standalone já entrega o valor — operador
pode rodar `estimate` antes de `ingest` manualmente.

`estimate` sai com exit code `3` se `disk_ok=False` ou
`cost > $50` — sinal pra scripts CI checarem sem ler stdout.

### Resumabilidade não foi adicionada nesta sessão

Plano sugeria flag `--resume` no ingest. Decisão: a **dedup duplo
existente** (PK message_id determinístico + content_hash UNIQUE) já
oferece resumabilidade *de facto* — re-rodar ingest sobre vault
parcial silenciosamente skipa o que já existe. Validado
empiricamente: re-ingest dos 226 messages do EVERALDO retornou
`(0 inserted, 226 skipped)`. Flag explícita `--resume` agrega zero
valor sobre isso e fica fora de escopo.

## Métricas finais

### Testes adicionados

| Arquivo | Testes |
|---|---|
| `tests/test_streaming_ingest.py` | 12 |
| `tests/test_media_source.py` | 16 |
| `tests/test_preflight.py` | 12 |
| **Total** | **40** |

Suite: 698 → 738 testes verde, ~60s execução completa.

### Validação empírica

**Estimate em ZIP real EVERALDO** (`teste_ingest.zip`, 50MB):

| Métrica | Estimado | DB real | Match |
|---|---|---|---|
| Mensagens | 226 | 226 | exato |
| Áudios | 105 | 119 transcriptions | + 14 reprocessamentos |
| Imagens | 10 | 96 visual_analyses | + frames de vídeo |
| Vídeos | 12 | 12 | exato |
| PDFs | 1 | 1 | exato |

Custos estimados ($0.34 ±$0.5) bateram com a ordem de grandeza dos
gastos reais por canal único. Range conservador (±50%) acomoda
incertezas até a calibração de produção (Sessão 8+).

**Streaming end-to-end** (mesmo ZIP, vault fresh):

```
chat.txt extraído: 21,059 bytes
Streaming: 226 inserted, 0 skipped em 29ms
Batches emitidos: 5 (50, 50, 50, 50, 26)
Re-ingest: 0 inserted, 226 skipped (dedup ativo)
```

Confirma: streaming funciona, batch=50 dispara progress callback
corretamente, dedup duplo skipa re-ingest sem falha.

### Custos

- Sessão 7 (este): **US$ 0.00**
- Acumulado projeto total: ~US$ 3.16 (inalterado)

## Próximos passos (pós-v1.3)

GRUPO 2 (resiliência) **completo**. Próximas sessões do roadmap
reformulado abrem GRUPO 3 (eficiência custo):

- **Sessão 8 → v1.4-efficient-classify**: #45 (transcribe
  checkpoint), #46 (classify cache + dedup + batch).
- **Sessão 9 → v1.5-efficient-vision**: #47 (vision filtro
  cascata), #48 (frames de vídeo), #49 (OCR roteamento).

UI Web continua DESLOCADA até Sessão 15+ conforme PROJECT_CONTEXT
addendum 25/04.

## Custos da sessão

| Op | Descrição | Custo (USD) |
|---|---|---:|
| 7.0 | Discovery (sqlite + grep local) | 0.0000 |
| 7.1 | ADR-006 + DROP TABLE migration | 0.0000 |
| 7.2 | #41 streaming (puro código + parser refactor) | 0.0000 |
| 7.3 | #42 MediaSource (puro código) | 0.0000 |
| 7.4 | #55 preflight (puro código) | 0.0000 |
| 7.5 | Validação (estimate + streaming local) | 0.0000 |
| 7.6 | Docs (puro markdown) | 0.0000 |
| 7.7 | Release | 0.0000 |
| **Total sessão** | | **US$ 0.00** |

Teto autorizado: US$ 0.20. Usado: 0%.
