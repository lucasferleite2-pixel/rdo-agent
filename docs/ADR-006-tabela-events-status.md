# ADR-006 — Status da tabela `events`

**Data inicial:** 25/04/2026 (manhã, formalização da dívida)
**Data de resolução:** 25/04/2026 (noite, Sessão 7)
**Status:** **RESOLVIDO — opção B (REMOVE) executada**
**Sprints relacionadas:**
- `v1.0.2-docs-sync` — formalização original da dívida
- `v1.2-resilient-pipeline` — sessão anterior, manteve status quo
- `v1.3-safe-ingestion` — **resolveu**, droppando a tabela
**Referências:** `docs/audits/AUDIT_2026-04-25_state_of_roadmap.md`,
plano da Sessão 7 (Phase 7.1)

## Contexto

O schema do blackboard SQLite (`~/rdo_vaults/<obra>/index.sqlite`)
define a tabela `events`, prevista para consolidar eventos forenses
extraídos do corpus — mensagens-chave, pagamentos, decisões — em uma
"camada de evento" agnóstica de origem (mensagem, transcrição, visão,
documento).

**Estado real (verificado em 25/04/2026 contra
`EVERALDO_SANTAQUITERIA/index.sqlite`): 0 rows.** A tabela existe
estruturalmente mas nunca foi alimentada pelo pipeline.

A originalidade do design previa um `event_extractor` que rodaria
após classificação e antes do narrador, mas esse módulo nunca foi
implementado. O pipeline atual passa direto de
`classifications + visual_analyses + financial_records` para o
`forensic_agent` (dossier + narrator + correlator) sem materializar
a camada de eventos.

## Impacto presente

- **Adapter do laudo** (`src/rdo_agent/laudo/adapter.py`,
  função `_build_cronologia`) usa **fallback** quando precisa montar
  cronologia para o laudo PDF:
  - Todos os `financial_records` viram eventos `tipo='pagamento'`
  - Top-N `classifications` ordenadas por
    `(human_reviewed DESC, confidence_model DESC)` viram eventos com
    `tipo` inferido da categoria primária (contrato/cronograma →
    `decisao`, demais → `mensagem`)
- O laudo real EVERALDO usa essa cronologia derivada — funciona
  visualmente, mas **não há "fonte da verdade" cronológica única**
  no sistema.
- O `dossier_builder` também ignora `events` e monta dossier do zero
  a partir de SQL bruto.

## Opções (a decidir em sprint futura)

### Opção A — Popular `events` no pipeline

Implementar `src/rdo_agent/event_extractor/` que:

- Consome `classifications` (com source polimórfico) +
  `financial_records`
- Aplica regras de promoção a evento (heurísticas + thresholds)
- Materializa rows em `events` com referências cruzadas
- Vira fonte da verdade para `dossier_builder` e `adapter` do laudo

Custo: 1 sessão dedicada (estimativa 4-6h).
Benefício: arquitetura mais limpa, queries mais simples, layer
reutilizável para futuro consolidador multi-canal.

### Opção B — Remover `events` do schema

Aceitar o fallback como solução permanente. Schema fica mais simples
(uma tabela a menos), mas a lógica de "o que é evento" fica
distribuída entre o adapter e o dossier_builder.

Custo: migração SQL pequena + ajustes em testes que tocam o schema.
Benefício: menos código morto, schema reflete o que efetivamente roda.

### Opção C — Manter como está

Tabela existe mas não é populada. Documentado neste ADR. Nada muda
até que apareça uma demanda concreta (ex: precisar de cronologia
unificada para o consolidador multi-canal — Sessão 5+).

Custo: zero.
Benefício: não decide nada que o caso de uso futuro pode redirecionar.

## Decisão final — Opção B (REMOVE)

**Tomada em 25/04 (noite), Sessão 7 (`v1.3-safe-ingestion`).**

### Investigação que confirmou a decisão

Phase 7.0 da Sessão 7 cruzou o estado real do código contra a tabela:

- **`tasks` tem 675 rows; `events` tem 0**, em todos os vaults
  inspecionados (apenas EVERALDO em produção, mas o padrão se repete
  em qualquer init_db novo — tabela criada vazia, nunca alimentada).
- **`grep` exaustivo por `INSERT INTO events`, `FROM events`, `events(`
  em `src/rdo_agent/`** retornou zero referências de produção.
- **`grep` em `tests/`** confirmou zero referências também (todos os
  matches em testes para a string "events" eram a coluna
  `forensic_narratives.events_count` ou variáveis Python locais
  como `events: list[EventoCronologia] = []`).
- O nome da função `_fetch_classified_events` em
  `forensic_agent/dossier_builder.py` é enganoso — ela lê de
  `classifications`, **não** da tabela `events`.
- O `adapter.py` do laudo (caminho que era descrito como "fallback")
  **é a implementação real** desde v1.0; nunca houve um "caminho
  primário" via tabela `events`.

### Por que B em vez de A ou C

- **Opção A (popular `events`)**: criaria event_extractor para
  alimentar uma tabela cujo schema (9 colunas, criado na Sprint 1)
  nunca foi validado contra um caso real. Custo de implementação alto
  para reproduzir o que o adapter já faz bem.
- **Opção C (manter)**: deixar tabela dormente no schema continua
  poluindo todo `init_db` futuro, gera confusão sobre qual é "a fonte
  da verdade", e mantém ADR-006 perpetuamente aberto. Não há demanda
  concreta no horizonte (consolidador multi-canal — Sessão 12 — vai
  precisar de design dedicado, não desta tabela legada).
- **Opção B (remover)**: schema cleanup pequeno, zero impacto em
  callers (porque não há callers), conserva a pista no ADR para o
  caso de demanda futura (que terá o luxo de desenhar do zero).

### Implementação

Migration nova em `orchestrator/__init__.py`:

```python
def _migrate_sessao7_drop_events_table(conn):
    conn.execute("DROP INDEX IF EXISTS idx_events_obra_date")
    conn.execute("DROP TABLE IF EXISTS events")
```

Idempotente (DROP IF EXISTS). Aplicada via `init_db()` na próxima
abertura de qualquer vault.

`schema.sql` perde o bloco `CREATE TABLE events (...)`. Comentário
"REMOVIDA na Sessão 7 (ADR-006)" deixa pista para arqueologia
futura.

`adapter.py:_build_cronologia` perde a frase "Fallback da dívida #35
(events table esta vazia)" — agora é só "implementação canônica".

### Critério de reabertura

Se uma sessão futura precisar de uma camada agnóstica de origem
(consolidando eventos de N canais para narrativa cross-canal), a
recomendação é **desenhar do zero** com schema validado contra o caso
real do consolidador, **não** ressuscitar `events` legada. Este ADR
fica como histórico — qualquer ADR-008+ futura sobre evento
consolidado deve referenciá-lo.

## Referências

- `src/rdo_agent/orchestrator/__init__.py:_migrate_sessao7_drop_events_table`
- `src/rdo_agent/orchestrator/schema.sql` (bloco events removido)
- `src/rdo_agent/laudo/adapter.py:_build_cronologia` (comentário atualizado)
- `docs/audits/AUDIT_2026-04-25_state_of_roadmap.md` — origem
- `docs/sessions/SESSION_LOG_SESSAO_7_INGESTION.md` — log da resolução
- ADR-002, ADR-003 — schema das classifications (camada que cobriu o
  papel que `events` deveria ter coberto)
