# ADR-007 — State machine no DB: wrapper sobre `tasks` (não tabela nova)

**Data:** 25/04/2026 (noite)
**Status:** ACEITO
**Sprint:** Sessão 6 — Resiliência core (`v1.2-resilient-pipeline`)
**Referência:** `docs/audits/AUDIT_2026-04-25_state_of_roadmap.md`,
plano da Sessão 6 (dívida #44)

## Contexto

A dívida #44 do roadmap reformulado (Addendum 25/04 PROJECT_CONTEXT)
pediu uma **state machine no DB** para o pipeline de processamento,
permitindo crash recovery e visibilidade do que está em curso. A
proposta original do plano:

- Criar tabela nova `processing_jobs` com colunas
  `(corpus_id, source_type, source_id, stage, status, error_msg,
  retry_count, started_at, completed_at)`.
- Construir `PipelineStateManager` operando sobre essa tabela.
- Instrumentar todos os módulos do pipeline para enqueue/claim/
  complete/fail.

## Descoberta na Phase 6.0 (discovery)

Auditoria do schema atual revelou que **a state machine já existe**
na tabela `tasks`, populada pelo orchestrator desde a Sprint 1. O
schema cobre exatamente o que `processing_jobs` cobriria:

```sql
CREATE TABLE tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type     TEXT NOT NULL,        -- ex: transcribe, classify, vision
    payload       TEXT NOT NULL,        -- JSON
    status        TEXT NOT NULL CHECK (status IN
                  ('pending', 'running', 'done', 'failed')),
    depends_on    TEXT NOT NULL DEFAULT '[]',  -- JSON array (DAG)
    obra          TEXT NOT NULL,
    priority      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    error_message TEXT,
    result_ref    TEXT
);
```

Vault EVERALDO tem **675 rows** populadas, distribuídas em 7
`task_type`s (transcribe, classify, visual_analysis, extract_audio,
extract_document, detect_quality, ocr_first), todas em `done`.

A API de runtime (em `src/rdo_agent/orchestrator/__init__.py`)
também já existe:

- `enqueue(conn, task) -> int`
- `next_pending(conn, obra) -> Task | None`
- `mark_running(conn, task_id)`
- `mark_done(conn, task_id, result_ref=None)`
- `mark_failed(conn, task_id, error)`

O que **não** existia:

- API ergonômica encapsulando esses helpers
- Helpers de *recovery* pós-crash (reset_running, reset_failed)
- CLI expondo o estado para o operador
- Detecção de tasks "running sem finished_at" (crash candidates)

## Decisão

**Construir `PipelineStateManager` como wrapper ergonômico sobre a
tabela `tasks` existente.** Não criar tabela nova `processing_jobs`.

Razões:

1. **Zero schema migration.** Tabela já existe, populada, indexada,
   testada em produção. Criar outra tabela com schema quase
   idêntico geraria duplicação confusa.
2. **Zero ruptura.** Orchestrator continua escrevendo em `tasks`
   sem mudança. Pipeline atual é preservado intacto.
3. **Aproveitamento de design existente.** `depends_on` já modela
   DAG (que o plano original não cobria). `priority` já está lá.
4. **Risco baixo.** Wrapper só adiciona métodos de leitura/recovery
   helpers — não muda comportamento de runtime existente.
5. **Migração futura permanece aberta.** Se em algum momento
   precisarmos renomear `tasks` → `processing_jobs` (por convenção
   ou clareza semântica), fica como refactor isolado em uma sessão
   dedicada — não bloqueia este trabalho.

## Consequências

### Positivas

- `PipelineStateManager` entrega valor real (status report agregado,
  detecção de crash candidates, reset_running, reset_failed) sem
  introduzir débito arquitetural.
- CLI `pipeline-status` e `pipeline-reset` ficam disponíveis
  imediatamente, operando sobre os 675 rows existentes do EVERALDO.
- `claim()` no manager pode no futuro receber task_type filter para
  workers especializados — extensível sem mudança de schema.

### Compromissos aceitos

- **Nome da tabela permanece `tasks`** mesmo que `processing_jobs`
  fosse mais auto-explicativo. Trade-off: clareza nominal pequena vs
  custo de migration grande. Aceito.
- **`payload` é JSON em texto** (legacy). Algum caller futuro pode
  querer schema mais estruturado (colunas dedicadas para ref_id,
  source_type, etc) — fica como evolução, não bloqueante.
- **`source_type` não é coluna explícita.** Hoje é inferido do
  `payload.source_file_id` ou via JOIN. Aceitável; o caso real onde
  isso é gargalo (queries por source_type) ainda não apareceu.

## Implementação

Wrapper em `src/rdo_agent/pipeline_state/state_manager.py`:

- `PipelineStateManager(conn)` — recebe conexão SQLite (consistente
  com o resto do codebase).
- `status(obra)` → `StatusReport` (counts, totals, resumable).
- `resumable_state(obra)` → list de tasks running sem finished_at.
- `claim(obra, task_type=None)` → atomic next_pending + mark_running.
- `complete(task_id, result_ref=None)`.
- `fail(task_id, error_msg)`.
- `reset_running(obra)` → running → pending pós-crash.
- `reset_failed(obra, task_type=None)` → failed → pending para retry.

CLI:

- `rdo-agent pipeline-status --obra X` (tabela rich + alerta de
  crash candidates).
- `rdo-agent pipeline-reset --obra X --target {running|failed}
  [--task-type T]`.

Testes: 14 unitários em `tests/test_pipeline_state_manager.py`.

Validação empírica: simulado crash em task #1 do EVERALDO; reset
detectou e devolveu para pending corretamente; estado original
restaurado depois.

## Referências

- `src/rdo_agent/pipeline_state/state_manager.py`
- `src/rdo_agent/orchestrator/__init__.py` (API runtime existente)
- `tests/test_pipeline_state_manager.py`
- Discovery report da Phase 6.0 da Sessão 6
- Plano da Sessão 6 (revisado para opção A)
