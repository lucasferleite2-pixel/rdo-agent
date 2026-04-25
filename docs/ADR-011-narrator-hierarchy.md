# ADR-011 — Narrator hierárquico (cascade day → week → month → overview)

**Data:** 25/04/2026 (noite)
**Status:** ACEITO
**Sprint:** Sessão 10 — Escala analítica (`v1.6-scale-analytics`)
**Dívida:** #51 (narrator hierárquico).

## Contexto

Para overview de obra de 730 dias, o narrator V4 atual recebe **todas
as 730 day-narratives concatenadas** como input. Custo:

- Token budget Sonnet 4.5 = 200k. Concatenação de 730 days × ~3k
  tokens/day = ~2.2M tokens. **Não cabe.**
- Mesmo caso curto (corpus EVERALDO, 13 days): cabe, mas
  desperdiça contexto que poderia condensar evidências.

`MAX_TOKENS_BY_SCOPE` (Sessão 5 / #32) endereçou o **output**, mas
o problema do **input** persistia.

## Decisão

Cascade obrigatória em corpus longo:

```
day narratives (existente)
    ↓ agregação por week ISO
week narratives (NOVO)
    ↓ agregação por mês calendário
month narratives (NOVO)
    ↓ agregação por trimestre (opcional)
quarter narratives (NOVO, skipped em corpus < 90 dias)
    ↓ síntese final
obra_overview
```

Cada nível **resume** o nível inferior, mas **preserva file_ids**
de evidência (rastreabilidade forense não pode quebrar).

### Schema

CHECK constraint legacy (`scope IN ('day', 'obra_overview')`) era
bloqueio hard. Migration `_migrate_sessao10_relax_narratives_scope_check`
recria a tabela sem CHECK em scope.

**Single source of truth** para scopes válidos passa para Python:
`narrator.VALID_SCOPES = {day, week, month, quarter, obra_overview,
adversarial}`. Schema fica flexível para futuros scopes
(`adversarial_v2`, `weekly_summary`, etc) sem nova migration.

### Composição do input

`compose_input_from_children(children, parent_scope, bucket_label)`
monta markdown estruturado:

```markdown
# Período: 2026-W15 (week)
# Narrativas filhas (7 de day):

## 2026-04-06
[narrativa day completa]

## 2026-04-07
[narrativa day completa]

...

# Evidências citadas (file_ids):
f_aaa12, m_bbb22, f_ccc34, ...
```

`extract_file_ids(text)` regex (`m_/f_/c_/fr_` + 4-12 chars) extrai
todos os file_ids do texto e os preserva — caller (narrator) deve
incluí-los na narrativa pai.

### Fallback obra_overview

Em corpus pequeno onde quarter foi pulado, `obra_overview`
**fallback automático** para o maior scope com narratives:
quarter → month → week → day. Evita falha em corpus piloto.

### Cache implícito

Se `skip_existing=True` (default), cada bucket que já tem narrativa
persistida é pulado. Re-rodar `narrate_hierarchy` em corpus
parcialmente narrado **não regenera**. Útil em pipeline parcial
(crash, retry, calibração).

## Consequências

### Positivas

- Token budget cabe em cada nível (week ≈ 6k tokens input,
  overview ≈ 32k tokens — ambos abaixo de 200k).
- Cascade introduz pontos naturais de cache: re-narrar overview
  com prompt diferente reusa weeks + months já narrados (= economia
  significativa).
- file_ids preservados: forense intacta na cascade.
- Schema flexível: futuros scopes sem nova migration.
- Compatibilidade total com narrativas existentes (nenhum CHECK
  removido = falha em rows antigas).

### Compromissos

- **Custo de iteração**: re-narrar uma day desencadeia week →
  month → overview se prompt de qualquer um mudou. Mitigação:
  cache binário (#52 / ADR-012).
- **Quarter skipped em corpus < 90 dias**: é pragmático mas
  significa que `obra_overview` pode pular sobre month direto
  sem o "mid-level" de quarter. Aceito.
- **Backwards compat com obra_overview existente**: corpus que já
  tem obra_overview narrative não dispara cascade automaticamente
  (a menos que `skip_existing=False`). Operador decide.

## Próximos passos

- Sessão 11 (validação real) deve gerar 1 cascade end-to-end
  contra EVERALDO ou corpus equivalente para validar tokens em
  cada nível e garantir que file_ids realmente propagam até o
  overview.
- Wiring com narrator V4 prompt template é responsabilidade do
  caller (este módulo entrega `compose_input_from_children`,
  caller passa pra `narrate(dossier, conn)` existente).

## Referências

- `src/rdo_agent/forensic_agent/hierarchy.py`
- `src/rdo_agent/orchestrator/__init__.py:_migrate_sessao10_relax_narratives_scope_check`
- `tests/test_narrator_hierarchy.py` (18 testes)
- `tests/test_sprint5_schema.py` (test atualizado para refletir
  remoção do CHECK)
- ADR-008 (cascade Pareto + dívidas adiadas)
- ADR-012 (cache binário complementar)
