# Sprint 2 — Plano Executável

Handlers de processamento (Whisper, GPT-4 Vision, pdfplumber) para as tasks enfileiradas pelo ingestor da Sprint 1. Consolidado a partir do planejamento de 2026-04-17.

## Contexto de entrada

Após Sprint 1 hardened, o sistema gera vault com:

- Mensagens parseadas (parser iOS + Android)
- Files hasheados e classificados
- Tasks PENDING enfileiradas por tipo: TRANSCRIBE (áudios), VISUAL_ANALYSIS (imagens), EXTRACT_AUDIO (vídeos, já implementado), EXTRACT_DOCUMENT (PDFs/docs, a implementar)

Sprint 2 implementa os handlers que consomem essas tasks e produzem conteúdo textual estruturado, alimentando as Sprints 3 (classificação) e 4 (geração de RDO).

## Decisões arquiteturais

### 1. Escopo

Todos os 3 novos handlers:

- `extract_document_handler` (pdfplumber)
- `transcribe_handler` (Whisper API)
- `visual_analysis_handler` (GPT-4 Vision)

O `extract_audio_handler` da Sprint 1 permanece — só precisa ser registrado no worker.

### 2. Ordem de implementação

1. **EXTRACT_DOCUMENT primeiro** — zero custo API, código simples, estabelece padrão de handler com persistência
2. **TRANSCRIBE depois** — maior volume de tasks, maior risco de custo descontrolado, exige observação atenta
3. **VISUAL_ANALYSIS por último** — reusa padrão de API calls já debugado em TRANSCRIBE

Cada handler fecha suas 4 camadas de teste antes do próximo começar.

### 3. Modelos e orçamento

| Componente | Modelo | Custo estimado |
|---|---|---|
| Whisper | `whisper-1` (padrão maduro) | US$ 0.006/min |
| Vision | `gpt-4o-mini` (econômico) | ~US$ 0.003/imagem |
| Documentos | pdfplumber (local) | zero |

Modelos configuráveis via `.env` (`WHISPER_MODEL`, `VISION_MODEL`) — troca com 1 linha.

Orçamento: caso a caso, sem teto rígido. Projeção total da Sprint: R$ 10-14 em desenvolvimento + operação.

### 4. Estratégia de teste (4 camadas)

| Camada | Quando | Custo |
|---|---|---|
| Unitários com mock | Toda lógica interna, em cada `pytest` | Zero |
| Golden fixture real | 1 captura única por handler, reusada | ~R$ 0.05 total |
| Smoke manual | Antes de commit grande | ~R$ 0.20/rodada |
| E2E contra obra real | Fechamento de cada handler | ~R$ 3-4/rodada |

**Fixtures commitadas no repo** (`tests/fixtures/*_golden_response.json`). São dados de teste legítimos, pequenos, sem PII sensível (áudio/imagem de teste criados para este fim).

### 5. Critérios de fechamento

**Objetivos (binários, verificáveis por SQL):**

- **V1 — Cobertura:** zero tasks em status `pending` ou `failed` após E2E. Todas viram `done` com `result_ref` preenchido.
- **V2 — Integridade:** outputs em `/20_transcriptions/`, `/30_visual/`. `api_calls` populada com request_hash + response_hash + cost_usd por chamada.
- **V3 — Custo:** soma de `cost_usd` em `api_calls` ≤ R$ 5 por rodada E2E.

**Qualidade (métricas + amostragem):**

- **Q1 — Métricas automáticas:**
  - Transcrições: média (caracteres / minuto áudio) entre 300-600; confidence médio ≥ 0.7; zero `text IS NULL`
  - Visual: JSON respeita schema obrigatório; campos mínimos `elementos_construtivos`, `atividade_em_curso`, `condicoes_ambiente`; length 200-2000 chars
  - Ambos: zero timeouts sem retry, zero `response_hash IS NULL`
- **Q2 — Amostragem humana (15%, threshold 87%):**
  - Transcrições: revisar 15 aleatórias, aceitável se ≥ 13/15 capturam sentido
  - Visual: revisar 3 aleatórias + foto, aceitável se ≥ 2/3 descrevem utilmente

Script `scripts/quality_gate.sh` automatiza Q1; `scripts/sample_for_review.sh` seleciona amostra para Q2.

### 6. Fora do escopo explícito

- Agente classificador (Sprint 3)
- Agente engenheiro gerador RDO (Sprint 4)
- CLI `status`/`generate-rdo` completas
- Retry com backoff exponencial e circuit breaker (retry simples no handler é OK)
- Paralelização de chamadas (sequencial basta)
- Fine-tuning de modelos
- Prompt engineering avançado além do necessário para V1+Q1

## Roadmap em fases

### Fase 1 — EXTRACT_DOCUMENT (pdfplumber)

**Entrega:** handler que extrai texto de PDF/DOCX/XLSX e persiste em `/20_transcriptions/`.

Checklist:

- [ ] Adicionar `pdfplumber>=0.11` ao `pyproject.toml`
- [ ] Implementar `extract_document_handler(task, conn)` em novo módulo `src/rdo_agent/document_extractor/`
- [x] Schema: tabela `documents` dedicada (decisão fechada; ver `schema.sql` linha ~170)
- [ ] Novo `TaskType.EXTRACT_DOCUMENT` no orchestrator
- [ ] Ingestor enfileira `EXTRACT_DOCUMENT` quando `file_type == "document"`
- [ ] Fixtures: PDF digital simples + PDF escaneado (para validar fallback)
- [ ] ~8 testes unitários cobrindo: extração OK, PDF sem texto, PDF corrompido, idempotência
- [ ] **Sentinel para PDF sem texto extraível** (descoberto no E2E): handler escreve marcador estruturado no `.txt` derivado contendo `source_file_id`, `source_path` e `source_sha256` quando `pdfplumber.extract_text()` retorna vazio. Resolve colisão de `file_id` entre múltiplos PDFs escaneados (sha256 de `""` é constante). Contrato do banco preservado: `documents.text` permanece `""` — sentinel vive apenas em disco para auditoria humana.
- [ ] Golden fixture capturada se usar biblioteca externa (pdfplumber é local, não precisa)
- [ ] Smoke: roda contra o PDF real da vault EVERALDO (plantas da escola)
- [ ] Critério: PDF da vault real é processado, texto extraído, task vira done

### Fase 2 — TRANSCRIBE (Whisper)

**Entrega:** handler que transcreve áudio via Whisper e persiste em `/20_transcriptions/`.

Checklist:

- [ ] Adicionar dependência OpenAI no `pyproject.toml` (se ainda não tem)
- [ ] Validar presença de `OPENAI_API_KEY` no `config.py` (falha explícita se ausente)
- [ ] Implementar `transcribe_handler(task, conn)` em novo módulo `src/rdo_agent/transcriber/`
- [ ] Logging em `api_calls`: request_hash, response_hash, tokens, cost_usd, latência, model usado
- [ ] Output em `/20_transcriptions/` + INSERT em `transcriptions` table
- [ ] Retry simples em erro de rede (1-2 tentativas)
- [ ] Fixtures: áudio sintético curto (~3s de silêncio + tom, gerado via ffmpeg no conftest)
- [ ] Golden fixture: captura JSON real de 1 transcrição curta, commita em `tests/fixtures/whisper_golden_response.json`
- [ ] ~10 testes unitários cobrindo: resposta válida, idioma detectado, segmentos, erro de rede, retry, timeout, API key ausente
- [ ] Teste com golden fixture: lê JSON, processa como se fosse resposta real
- [ ] Smoke: roda contra 1 áudio real pequeno da vault EVERALDO (~10s)
- [ ] Métricas Q1 calculáveis após E2E
- [ ] Critério: 105 áudios da vault transcritos, métricas Q1 passam, amostra 15/15 revisada com ≥ 13 OK

### Fase 3 — VISUAL_ANALYSIS (GPT-4 Vision)

**Entrega:** handler que analisa imagem via Vision e persiste JSON estruturado em `/30_visual/`.

Checklist:

- [ ] Implementar `visual_analysis_handler(task, conn)` em novo módulo `src/rdo_agent/visual_analyzer/`
- [ ] Prompt engineering: schema JSON obrigatório para resposta (elementos_construtivos, atividade_em_curso, condicoes_ambiente, observacoes_tecnicas)
- [ ] System prompt específico para contexto de obra (foto de canteiro, identificação técnica)
- [ ] Reusa infra de `api_calls` de TRANSCRIBE (logging idêntico)
- [ ] Output em `/30_visual/` + INSERT em `visual_analyses` table
- [ ] Validação de schema: response deve ser JSON parseável com campos obrigatórios
- [ ] Fixtures: imagem sintética 64x64 com formas (via PIL no conftest)
- [ ] Golden fixture: captura JSON real de 1 análise, commita em `tests/fixtures/vision_golden_response.json`
- [ ] ~8 testes unitários cobrindo: resposta válida, JSON mal-formado, retry, rate limit, imagem muito grande
- [ ] Smoke: roda contra 2 imagens reais da vault EVERALDO
- [ ] Métricas Q1 calculáveis
- [ ] Critério: 10 imagens da vault analisadas, Q1 passa, amostra 3/3 revisada com ≥ 2 OK

### Fase 4 — Orquestração E2E

**Entrega:** comando CLI `rdo-agent process --obra X` que registra os handlers no `run_worker` e processa toda a fila da obra.

Checklist:

- [ ] Registrar os 4 handlers (3 novos + extract_audio) no CLI `process`
- [ ] Usar `stop_when_empty=True` no worker (processa até fila vazia, sai)
- [ ] Rich progress bar mostrando task atual + contagem done/failed
- [ ] Script `scripts/quality_gate.sh` valida V1+V2+V3+Q1 via SQL
- [ ] Script `scripts/sample_for_review.sh` seleciona amostra Q2 e aguarda julgamento
- [ ] E2E completo: apaga vault, re-ingere, processa, valida
- [ ] Commit de fechamento da Sprint 2 quando todos os critérios passarem

## Riscos e mitigações

**R1 — Whisper não entende jargão de construção regional**

Mitigação: aceita a qualidade do whisper-1 padrão. Sprint 3 usa contexto de mensagens próximas para corrigir ambiguidade. Se grave, anota em `SPRINT3_BACKLOG.md` para fine-tuning futuro.

**R2 — Vision alucina descrevendo coisas que não estão na foto**

Mitigação: system prompt explícito ("descreva APENAS o que é visível; se não tem certeza, marque como 'não identificado'"). Schema obrigatório força output estruturado. Amostragem Q2 detecta alucinação grosseira.

**R3 — Custo da API estoura durante desenvolvimento**

Mitigação: arquitetura de 4 camadas (mock 90% do tempo). `DRY_RUN=true` no .env desabilita chamadas reais temporariamente. Configurar spend limit na OpenAI em US$ 40 dá alerta se passar.

**R4 — API key vaza durante desenvolvimento**

Mitigação: `.env` no `.gitignore` (já validado). `.env.example` só com placeholders. Revisão de cada commit para confirmar que nenhuma chave entrou no diff. Rotação de chaves se suspeita.

## Dependências técnicas a adicionar

```toml
# pyproject.toml [project] dependencies
"openai>=1.30",        # Whisper + Vision
"pdfplumber>=0.11",    # Extração PDF
"httpx>=0.27",         # Se precisar de controle de timeout/retry fino
```

## Referências

- Sprint 1 baseline: commit `9c5cad1` (Sprint 1 hardened)
- Backlog descoberto: `docs/SPRINT2_BACKLOG.md`
- Blueprint arquitetural: `docs/Blueprint_V3_Agente_Forense_RDO.docx` §5.1-5.3, §6.4
