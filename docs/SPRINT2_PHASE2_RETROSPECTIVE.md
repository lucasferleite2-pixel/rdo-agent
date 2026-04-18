# Sprint 2 — Fase 2 (TRANSCRIBE/Whisper) — Retrospectiva

Documento de fechamento da Fase 2 do Sprint 2, consolidando decisões, resultados empíricos e dívidas técnicas após a implementação do handler `TRANSCRIBE` (OpenAI Whisper). Escrito em 2026-04-18, após captura da golden fixture e 124/124 testes verdes.

Ver também: `SPRINT2_PLAN.md` (plano original), `SPRINT2_BACKLOG.md` (dívidas pré-sprint).

## Contexto de saída

Após Fase 2 fechada (~95%), o sistema tem:

- Handler `transcribe_handler(task, conn)` totalmente funcional em `src/rdo_agent/transcriber/__init__.py`
- 10 testes unitários + 1 teste com golden fixture real (10/10 passando)
- Schema `api_calls` estendido com `latency_ms`, `model`, `error_type` (migração idempotente)
- Golden fixture commitada em `tests/fixtures/whisper_golden_response.json` (SHA-256 `3ecd3260...44427ce`)
- Suite completa: **124/124 verde** (antes: 123 + 1 skip)
- Primeiro gasto real de API registrado: US$ 0.0003

Lacuna remanescente: validação E2E contra áudios reais da vault `EVERALDO_SANTAQUITERIA` (dados no WSL, não sincronizado com Mac ainda).

## Decisões arquiteturais tomadas

### 1. SDK `openai>=1.30` fixado

A versão 1.30 introduziu `response_format="verbose_json"` que retorna `avg_logprob` por segmento. Confidence derivado disso via `exp(avg_logprob)`. Versões anteriores não teriam acesso ao logprob, inviabilizando threshold de qualidade.

Rejeitado: `requests` puro (perderia tipagem), httpx manual (reinventa wheel).

### 2. `language="pt"` fixo, `temperature=0`

100% dos áudios vêm de obras em MG. Auto-detect de idioma introduz variância desnecessária e é evidência forense fraca (poderia variar entre chamadas idênticas).

Temperatura zero garante reprodutibilidade — mesmo áudio, mesma transcrição. Requisito inegociável para laudo probatório.

### 3. ALTER TABLE `api_calls` (schema evolution incremental)

Colunas adicionadas: `latency_ms INTEGER`, `model TEXT`, `error_type TEXT`.

Função `_migrate_api_calls_sprint2_phase2` no `orchestrator/__init__.py` usa `PRAGMA table_info` para decidir se aplica cada ALTER — idempotente por construção. Permite migrar vaults antigos sem quebrar.

Regra seguida: **1 ALTER TABLE por Sprint no máximo** (Fase 1 já tinha adicionado tabela `documents`; Fase 2 só estendeu `api_calls` existente).

### 4. Sentinel textual para transcrições vazias

Whisper pode retornar `text=""` em áudios silenciosos ou ruído puro. Se o handler salvasse `""` no `.txt`, o `source_sha256` colidiria com qualquer outro arquivo vazio (hash de string vazia é constante).

**Solução:** quando `text == ""`, escrever:[SENTINEL: transcription_empty] source_sha256=<hash_do_audio_original>
Mesma política aplicada no `document_extractor` da Fase 1 para PDFs escaneados. Padrão unificado do projeto: **qualquer handler que gere arquivo derivado tem sentinel para conteúdo vazio**.

### 5. Classificação de erros retryable vs permanente

| Exception OpenAI | Retryable? | Rationale |
|---|---|---|
| `APIConnectionError` | sim | Rede transiente |
| `RateLimitError` | sim | Quota temporária, backoff resolve |
| `APITimeoutError` | sim | Carga do servidor |
| `AuthenticationError` | não | Chave inválida, retry repete o erro |
| `BadRequestError` | não | Input ruim, problema é na origem |
| `NotFoundError` | não | Modelo não existe, estrutural |

Delays de retry: `(1.0s, 3.0s)`. Total máximo: 3 tentativas (original + 2 retries).

Cada tentativa gera uma linha em `api_calls` — retries ficam rastreáveis. Uma task que sucede no 3º retry tem 3 rows (1 success + 2 fails com `error_type` preenchido).

### 6. Transacionalidade: `api_calls` é log, `files`/`transcriptions` é dado

`api_calls` registra **eventos que aconteceram e custaram dinheiro** — é log de auditoria.

`files`, `transcriptions`, `media_derivations` são **dado operacional** — all-or-nothing por design.

**Consequência intencional:** após 2 invocações do mesmo handler sobre o mesmo áudio, temos 2 rows em `api_calls` (2 gastos reais) mas apenas 1 em `files` (arquivo único). Teste `test_handler_is_idempotent` valida essa separação.

Query forense típica: `SELECT SUM(cost_usd) FROM api_calls WHERE obra_id = 'X'` dá o custo real total, mesmo contando retries que falharam.

## Schema de `api_calls` após Fase 2

18 colunas. Destaque para as 3 adicionadas na migração:

| Coluna | Tipo | Origem | Descrição |
|---|---|---|---|
| `id` | INTEGER PK | Sprint 1 | Autoincremento |
| `obra_id` | TEXT | Sprint 1 | Isolamento por obra |
| `task_id` | INTEGER | Sprint 1 | FK para tasks |
| `provider` | TEXT | Sprint 1 | `openai`, `anthropic` |
| `endpoint` | TEXT | Sprint 1 | `audio/transcriptions`, etc. |
| `input_tokens` | INTEGER | Sprint 1 | Whisper não reporta tokens |
| `output_tokens` | INTEGER | Sprint 1 | idem |
| `input_size_bytes` | INTEGER | Sprint 1 | Tamanho do áudio |
| `output_size_bytes` | INTEGER | Sprint 1 | Tamanho da resposta |
| `cost_usd` | REAL | Sprint 1 | Cálculo em USD |
| `request_id` | TEXT | Sprint 1 | ID da API |
| `http_status` | INTEGER | Sprint 1 | 200, 429, 401 |
| `success` | INTEGER | Sprint 1 | 1 / 0 |
| `error_message` | TEXT | Sprint 1 | Exception msg |
| `started_at` | TEXT | Sprint 1 | ISO-8601 UTC |
| `ended_at` | TEXT | Sprint 1 | ISO-8601 UTC |
| **`latency_ms`** | **INTEGER** | **Fase 2** | **Diferença em ms** |
| **`model`** | **TEXT** | **Fase 2** | **`whisper-1`, etc.** |
| **`error_type`** | **TEXT** | **Fase 2** | **`connection`, `auth_error`, etc.** |

## Fluxos de execução

### Caminho feliz

1. `transcribe_handler(task, conn)` invocado pelo worker
2. Busca `file_id` do áudio via `tasks.input_ref`
3. Cliente OpenAI lazy-init (valida `OPENAI_API_KEY`, raise se ausente)
4. Chama `_call_whisper_with_retry` → HTTP 200
5. Calcula `confidence = exp(avg_logprob_médio)`
6. Se `text == ""`: escreve sentinel; senão escreve texto normal
7. Salva `.txt` em `/20_transcriptions/`, INSERT em `files` (status=`transcribed`)
8. INSERT em `transcriptions` + `media_derivations`
9. INSERT em `api_calls` (success=1, latency_ms, model, cost_usd)
10. `mark_done(task)` com `result_ref` apontando para o `.txt`

### Caminho com retry (sucesso na 3ª tentativa)

1-3. idêntico
4a. Tentativa 1: `APIConnectionError` → INSERT `api_calls` (success=0, `error_type=connection`) → sleep 1s
4b. Tentativa 2: `RateLimitError` → INSERT `api_calls` (success=0, `error_type=rate_limit`) → sleep 3s
4c. Tentativa 3: HTTP 200 → retorna resposta
5-10. idêntico caminho feliz

Total: 3 rows em `api_calls` para 1 task concluída com sucesso.

### Caminho com falha permanente

1-3. idêntico
4. Tentativa 1: `AuthenticationError` (não-retryable) → INSERT `api_calls` (success=0, `error_type=auth_error`) → re-raise
5. Handler captura exception no topo
6. `mark_failed(task, error_message)` — task vai para status `failed`

## Economia e custos

### Pricing observado

Whisper-1 a **US$ 0.006 por minuto** de áudio, com cobrança em incrementos de 1 segundo (não arredonda minuto).

### Projeção operacional

Premissas conservadoras:

- Obra típica: ~30 áudios WhatsApp/mês
- Duração média: ~1 min por áudio
- Total: ~30 min áudio/mês

Custo estimado: **30 × US$ 0.006 = US$ 0.18/obra/mês** (~R$ 0.95).

Escala para 10 obras paralelas: US$ 1.80/mês total. Irrelevante para o orçamento.

### Custo real até agora

US$ 0.0003 (golden fixture, 3 segundos sintéticos). Primeira e única chamada paga.

### Salvaguardas pendentes

- [ ] Spend limit na OpenAI (soft $20 / hard $50) — PRÉ-REQUISITO antes do E2E

## Golden fixture

### Captura

- Data: 18 de abril de 2026, 18:19 BRT
- Ambiente: macOS Tahoe (`lucasfleite@MacBook-Air-de-Lucas`)
- SHA-256: `3ecd3260f01b6d9cc439f0b4c62749c7cc5b65334ee2db61d006ddbea44427ce`
- Custo: US$ 0.0003

### Input sintético (ffmpeg)

```bash
ffmpeg -f lavfi -i "sine=frequency=440:duration=3" -ar 16000 -ac 1 synthetic.wav
```

Senoidal 440 Hz, 3 segundos, 16 kHz mono. Sem fala.

### Output capturado

```json
{
    "duration": 3.0,
    "language": "portuguese",
    "text": "",
    "segments": [],
    "usage": {
        "seconds": 3.0,
        "type": "duration"
    },
    "words": null,
    "task": "transcribe"
}
```

### Insight empírico

O Whisper retornou `text: ""` e `segments: []` para o beep. Comportamento **correto**: sem fala, sem transcrição.

Isso **valida empiricamente a decisão 4 (sentinel pattern)**. O cenário hipotético que motivou a lógica de sentinel (transcrição vazia em áudio sem fala) foi confirmado pelo primeiro contato real com a API. Os 10 testes mockados haviam previsto o caso antes da validação empírica.

### Propósito do commit

A fixture serve como **referência de contrato**: garante que a forma da resposta da API não mude silenciosamente entre versões do SDK ou do modelo. Se a OpenAI mudar o schema do JSON, o teste `test_transcribe_handler_success_with_golden_fixture` quebra na próxima execução.

## Checklist de fechamento (Fase 2 do SPRINT2_PLAN.md)

Comparação com o plano original (`SPRINT2_PLAN.md §Fase 2`):

- [x] Adicionar dependência OpenAI no `pyproject.toml`
- [x] Validar presença de `OPENAI_API_KEY` no `config.py`
- [x] Implementar `transcribe_handler(task, conn)` em `src/rdo_agent/transcriber/`
- [x] Logging em `api_calls`: request_id, cost_usd, latência, model, error_type
- [x] Output em `/20_transcriptions/` + INSERT em `transcriptions` table
- [x] Retry em erro de rede (2 tentativas extras, delays 1s/3s)
- [x] Fixtures: áudio sintético curto (ffmpeg lavfi)
- [x] Golden fixture: captura JSON real, commitada em `tests/fixtures/whisper_golden_response.json`
- [x] 10 testes unitários cobrindo todos os casos
- [x] Teste com golden fixture funcionando (passou de skip → pass)
- [ ] Smoke: roda contra 1 áudio real pequeno da vault EVERALDO (~10s) — **pendente, requer WSL**
- [ ] Métricas Q1 calculáveis após E2E — **pendente**
- [ ] Critério: 105 áudios da vault transcritos, métricas Q1 passam, amostra 15/15 revisada com ≥ 13 OK — **pendente**

## Métricas finais

| Métrica | Valor |
|---|---|
| Linhas em `transcriber/__init__.py` | ~475 |
| Linhas em `test_transcriber.py` | ~479 |
| Testes na fase | 10 (10/10 verde) |
| Testes no projeto completo | **124/124 verde** |
| Commits específicos da fase | 5 |
| Commit final (fixture) | `33c4495` |
| Tempo de desenvolvimento | ~6h (17-18 de abril) |
| Custo em API até o fechamento | US$ 0.0003 |

## Lições aprendidas

### 1. Sentinel pattern é política do projeto

Aplicado em duas fases consecutivas (Fase 1 PDF escaneado, Fase 2 áudio silencioso). Vale codificar: **todo handler que gera arquivo derivado deve ter sentinel para o caso de conteúdo vazio do provedor**. Fase 3 (VISUAL_ANALYSIS) herda esse padrão automaticamente.

### 2. Validação programática > inspeção visual de diff

Durante a fase, o renderer do TUI duas vezes mostrou "linhas duplicadas" que, ao validar via `ast.parse` + `grep -c`, eram artefato visual. **Política estabelecida:** rejeição de código só após confirmação programática.

### 3. Migração Python > SQL bruto em schema incremental

`_migrate_api_calls_sprint2_phase2` usa `PRAGMA table_info` para aplicar cada ALTER só se necessário. Idempotente, rodável 100 vezes sem efeito cumulativo. SQL `CREATE TABLE IF NOT EXISTS` não funciona para ALTER — precisa da lógica Python.

### 4. Chave API por ambiente aumenta rastreabilidade

Mac e WSL usam chaves OpenAI separadas. No dashboard, consigo saber qual ambiente gerou qual chamada. Reduz o raio de impacto em caso de vazamento.

### 5. Identidade git configurada ANTES do primeiro commit

O commit inicial da fase (`3a647b3`) saiu com `lucasfleite@MacBook-Air-de-Lucas.local` por default. Corrigido via `git commit --amend --reset-author` + `--force-with-lease`. **Em novos ambientes, sempre rodar `git config --global user.*` como primeira coisa.**

## Dívidas técnicas mapeadas

Itens conhecidos não resolvidos na Fase 2, agrupados por sprint-destino.

### Para Fase 3 (próxima)

Nenhuma dívida da Fase 2 bloqueia a Fase 3. O padrão arquitetural está maduro para replicar.

### Para Sprint 5 (hardening)

- **Timeout explícito na chamada Whisper.** Atualmente usa default do SDK (~10 min). Áudios de 2-5 min em situação de lentidão podem pendurar o worker. Propor `timeout=60` como default, override via env.
- **Logging estruturado.** INFO solto não parseia fácil para auditoria. Migrar para `structlog` com JSON output.
- **Dead letter queue.** Quando retry esgota, task fica em `failed` sem mecanismo automático de revisão. Precisa: worker dedicado de retry manual + dashboard de tasks failed.

### Dependente de dados reais

- **Validação E2E contra `teste_ingest.zip` (EVERALDO_SANTAQUITERIA).** Zip está no WSL. Necessário para completar métricas Q1 (caracteres/minuto áudio, confidence médio ≥ 0.7) e amostragem Q2 (15 transcrições revisadas humanamente).

## Riscos monitorados

### R1 (do plano original) — Whisper não entende jargão regional

**Estado:** não validado empiricamente. Sem áudio real, não dá para afirmar nem negar. Segue como risco aberto até o E2E.

### R3 (do plano original) — Custo estoura em desenvolvimento

**Estado:** mitigado parcialmente. Arquitetura de testes mockados evitou 99% dos gastos potenciais. Spend limit na OpenAI ainda pendente de configuração. Gasto real: US$ 0.0003.

### R4 (do plano original) — API key vaza

**Estado:** mitigado. `.env` + wildcard `.env.*` no `.gitignore` (commit `a74c7b2`). Incidente prévio de chave exposta em screenshot resultou em rotação + política de nunca-screenshot-com-chave. `sk-proj-*T5MA` (WSL) e `sk-proj-*AnUA` (Mac) separadas por ambiente.

## Próximos passos

### Para fechar Fase 2 em 100%

1. Configurar spend limit OpenAI (soft $20 / hard $50)
2. Sincronizar WSL com a fixture via `git pull`
3. Rodar E2E contra `~/teste_ingest.zip` no WSL (~R$ 3-5, ~1h)
4. Documentar resultados do E2E (adendo a este arquivo)

### Para iniciar Fase 3 (VISUAL_ANALYSIS)

Template a seguir (baseado na Fase 2):

- Módulo `src/rdo_agent/visual_analyzer/__init__.py`
- Testes mockados (~8, conforme plano)
- Golden fixture (1 imagem sintética via PIL no conftest)
- Schema: nova tabela `visual_analyses` + `TaskType.ANALYZE_IMAGE`
- Decisões a tomar antes: modelo (`gpt-4o-mini` vs `gpt-4o`), schema JSON de resposta, prompt pt-BR para canteiro

## Referências

- `SPRINT2_PLAN.md` — plano executável original da Sprint 2
- `SPRINT2_BACKLOG.md` — backlog pré-sprint com decisões de arquitetura
- `Blueprint_V3_Agente_Forense_RDO.docx` §5.2 — contrato do pipeline de transcrição
- Whisper API: https://platform.openai.com/docs/guides/speech-to-text
- OpenAI SDK Python: https://github.com/openai/openai-python
- Commit final da fase: `33c4495`
