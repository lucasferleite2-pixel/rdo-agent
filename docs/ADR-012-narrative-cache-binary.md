# ADR-012 — Cache binário de narrativas (fuzzy adiado em #62)

**Data:** 25/04/2026 (noite)
**Status:** ACEITO
**Sprint:** Sessão 10 — Escala analítica (`v1.6-scale-analytics`)
**Dívida:** #52 (cache narrativas com invalidação inteligente).

## Contexto

Antes desta sprint, re-narrar mesmo escopo dispara nova chamada
Sonnet API mesmo se:

- Prompt mudou pouco (typo fix, formatting).
- Conteúdo de evidências não mudou.
- Caller só queria conferir resultado existente.

`UNIQUE(obra, scope, scope_ref, dossier_hash)` no schema da v0.x já
fazia cache implícito **por evidência** (mudança em dossier
hashifica → row nova). Mas **prompt_version** era string nominal —
typo em PROMPT_V1 vs PROMPT_V_1 gerava `prompt_version` diferente
e duas rows duplicadas.

## Decisão

Implementar cache binário em **5 dimensões** que o caller decide:

```
cache hit ⇔
    obra            == row.obra
AND scope           == row.scope
AND scope_ref       == row.scope_ref          (handle NULL)
AND prompt_template_hash == hash(prompt)      (NOVO em S10)
AND dossier_hash    == row.dossier_hash       (existente)
```

### Migration

`_migrate_sessao10_narrative_cache_columns` adiciona:

- `ALTER TABLE forensic_narratives ADD COLUMN prompt_template_hash TEXT`
- `CREATE INDEX idx_narratives_cache (obra, scope, scope_ref,
  prompt_template_hash, dossier_hash)`

Idempotente via PRAGMA table_info inspection. Narrativas legadas
**permanecem** (rows preservadas) mas com `prompt_template_hash =
NULL` — para elas, cache **sempre miss** até ser re-narradas (ou
hidratadas manualmente via `cache.annotate_hash(id, template)`).

### Hash binário

```python
def hash_prompt_template(template: str) -> str:
    return sha256(template.encode("utf-8")).hexdigest()[:16]
```

Mudança trivial (espaço extra, quebra de linha, typo) → hash
diferente → cache miss → re-narrativa paga.

### NÃO implementado nesta sprint: invalidação fuzzy

Plano original previa similarity check:

```python
if existing.prompt_template_hash != prompt_hash:
    sim = compute_textual_similarity(existing.template, new.template)
    if sim > 0.95:
        return existing  # mudança trivial, cache válido
```

Decisão (mesma filosofia da Sessão 8 — Jaccard agora,
sentence-transformers depois): **adiar como dívida #62** com
triggers concretos:

1. Operador reportar "fiz typo no prompt e tive que re-pagar
   narrativa overview".
2. 3+ ocorrências documentadas de cache miss por mudança trivial.
3. Custo agregado de re-narrativas por typo > $5 num período.

Quando trigger ativar:

- Adicionar coluna `prompt_template` (texto completo, não só hash)
  para cálculo de similarity.
- Implementar `_prompt_similarity` (Jaccard inicial, conforme
  ADR-008; embeddings se #59 ativar primeiro).
- ADR-013 documentará a tecnologia escolhida e o threshold.

## API

```python
cache = NarrativeCacheManager(conn)

# Lookup antes de chamar Sonnet
hit = cache.get(
    obra="EVERALDO_SANTAQUITERIA",
    scope="day",
    scope_ref="2026-04-08",
    prompt_template=PROMPT_V4,
    dossier_hash=compute_dossier_hash(dossier),
)
if hit is not None:
    return hit.narrative_text  # economia de $$ + tempo

# Após save_narrative, hidrata o hash
nid = save_narrative(...)
cache.annotate_hash(nid, PROMPT_V4)

# Telemetria
stats = cache.stats(obra="EVERALDO_SANTAQUITERIA")
# stats.total_narratives, stats.with_hash, stats.legacy, stats.by_scope

# Invalidação manual (operador decide)
cache.invalidate(obra="X", scope="day", before="2026-04-01")
```

## Consequências

### Positivas

- 1 chamada Sonnet poupada em corpus grande pode pagar dezenas
  de centavos a vários dólares (overview narrative ~$0.30).
- Hidratação opcional (`annotate_hash`) permite migrar narrativas
  legadas para o regime de cache sem perder histórico.
- `invalidate` cirúrgico: pode invalidar só 1 day-narrative ou
  todo um scope, sem deletar texto (rastreabilidade preservada).
- Single source of truth: hash do template é determinístico e
  parametrizável pelo caller — sem mágica.

### Compromissos

- **Binário = miss em mudança trivial**: typo no prompt invalida.
  Para corpus piloto (EVERALDO 17 narratives) custo de re-narrar
  é trivial; para corpus produtivo grande pode acumular. Mitigação
  via #62 quando trigger ativar.
- **Coluna `prompt_template` ausente**: hoje só armazenamos hash
  (16 chars). Para fuzzy futuro precisaremos da string completa
  (mais ~2-5KB por narrative). Migração será aditiva.

## Critério de reabertura — dívida #62

Triggers documentados acima. Estimativa de implementação: meio
dia (similarity textual + threshold + ADR-013).

## Referências

- `src/rdo_agent/forensic_agent/narrative_cache.py`
- `src/rdo_agent/orchestrator/__init__.py:_migrate_sessao10_narrative_cache_columns`
- `tests/test_narrative_cache.py` (19 testes)
- ADR-008 (mesma filosofia binário-agora-fuzzy-depois para classify)
- ADR-009 (mesma filosofia heurística-agora-CLIP-depois para visual)
