# ADR-006 — Status da tabela `events`

**Data:** 25/04/2026
**Status:** PENDENTE (decisão a tomar em sprint futura)
**Sprint:** Higiene Documental (`v1.0.2-docs-sync`) — formalização da
dívida; **não** implementação.
**Referência:** `docs/audits/AUDIT_2026-04-25_state_of_roadmap.md`
(inconsistência #15)

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

## Decisão atual

**Opção C — manter como está.** Razão: a Sprint de Higiene Documental
(esta) é puramente de alinhamento de docs e não tem mandato para
mexer em pipeline. A tabela `events` provavelmente vira relevante
quando o **Consolidador multi-canal (Sessão 5)** precisar materializar
cronologia cross-canal — naquele momento, decidir entre A e B fica
informado pelas necessidades reais do consolidador.

A próxima sprint que precisar tocar nessa decisão deve ler este ADR
primeiro.

## Referências

- `src/rdo_agent/laudo/adapter.py` — `_build_cronologia` (usa fallback)
- `src/rdo_agent/forensic_agent/dossier_builder.py` — ignora `events`
- `docs/audits/AUDIT_2026-04-25_state_of_roadmap.md` — origem da observação
- `docs/SPRINT3_PLAN.md` (histórico) — onde a tabela foi originalmente proposta
- ADR-002, ADR-003 — schema das classifications (camada anterior à events)
