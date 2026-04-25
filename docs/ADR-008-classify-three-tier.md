# ADR-008 — Classify pipeline em 3 níveis (cache + Jaccard + batch)

**Data:** 25/04/2026 (noite)
**Status:** ACEITO
**Sprint:** Sessão 8 — Eficiência custo (`v1.4-efficient-classify`)
**Dívida:** #46 (classify cache + dedup semântico + batch)

## Contexto

Cada chamada ao gpt-4o-mini para classificar uma mensagem custa
~$0.0001. Em corpus grande (estimativa 100k mensagens em ZIP de
5GB), isso vira ~$10 só em classify, sem contar latência (cada call
~500ms → quase 14h sequencial). Antes desta sessão, **toda mensagem
acionava 1 chamada API** — sem cache, sem dedup, sem batching.

Padrões empíricos do WhatsApp pt-BR:

- 30-50% das mensagens são **repetições exatas** ("ok", "blz", "valeu",
  "rsrs", emojis curtos).
- 10-30% adicionais são **variações lexicais leves** ("ok valeu",
  "valeu blz" — mesmo set de tokens, label provavelmente igual).
- Restante (~30-60%) é conteúdo único que de fato precisa de
  classificação semântica via LLM.

Otimizar custo neste estágio é alavanca grande para viabilizar
processamento de corpus de produção real (Sessão 11+).

## Decisão

Pipeline de classify em **3 níveis** com fallback explícito, do
mais barato ao mais caro:

### Nível 1 — `ClassifyCache` (exact-match)

`src/rdo_agent/classifier/cache.py`

- Tabela ``classify_cache(text_hash, prompt_version, label_json,
  hit_count, created_at)``.
- ``normalize_text``: lower, strip pontuação, colapsa whitespace.
- Hash: sha256 de ``normalize(text) || prompt_version`` truncado em
  16 hex chars.
- ``get(text, pv)`` → ``CachedLabel`` ou ``None`` (com
  ``hit_count++`` em hit, para analytics).
- ``put(text, label)`` → INSERT OR IGNORE (primeiro put vence).
- Versionado por ``prompt_version``: troca de prompt invalida cache
  automaticamente sem precisar de TRUNCATE.

**Custo de hit**: 1 SELECT indexado em SQLite local (microsegundos).
**Hit rate esperado**: 30-50% em corpus longo.

### Nível 2 — `JaccardDedup` (similaridade léxica)

`src/rdo_agent/classifier/jaccard_dedup.py`

- Tokenização: lower, regex `[a-z0-9áéíóúâêôãõàç]+`, filtra stopwords
  PT-BR mínimas, remove tokens de len<2.
- Similaridade: `|A∩B| / |A∪B|` sobre sets de tokens.
- Janela rolante `max_pool=500` (FIFO eviction).
- Threshold default 0.80 — mensagens muito similares
  (variações triviais) compartilham label do candidato mais recente.

**Por que Jaccard, não embeddings?**

A alternativa óbvia seria `sentence-transformers/all-MiniLM-L6-v2`
para captura semântica plena (paráfrases, sinônimos). Custo de
adoção:

- ~2GB de install (PyTorch dominante).
- Modelo 80MB no primeiro uso.
- Boot time +5s.
- ~50ms/embedding em CPU.

Para a corpus piloto EVERALDO (250 mensagens, custo classify
total ~$0.025), o overhead de adoção não compensa. Para corpus de
100k+ mensagens, *talvez* — mas ainda não há evidência empírica
disso. Decisão: **Jaccard agora, embeddings em dívida #59**
(disparada por evidência de produção, ver abaixo).

**Hit rate esperado**: 10-30% adicional sobre o cache. Pega
variações lexicais leves; deixa passar paráfrases reais.

**Custo de hit**: O(n) sobre pool de 500 candidatos = ~5ms.

### Nível 3 — `BatchClassifier` (OpenAI Batch API)

`src/rdo_agent/classifier/batch.py`

- Tabela ``batches(id, corpus_id, status, request_count, ...)``.
- ``submit_batch(requests)``: serializa JSONL, ``files.create`` →
  ``batches.create`` → registra ``batch_id``.
- ``poll_batch(batch_id)``: ``batches.retrieve`` → atualiza status.
- ``fetch_results(batch_id)``: quando ``completed``, ``files.content``
  → parseia JSONL → retorna `BatchResult[]`.

**Por que batch?**

OpenAI Batch tem **50% de desconto** sobre o sync, com latência
de ~24h. Em pipeline de corpus grande onde:

- Transcribe roda em paralelo (também custa $$),
- Vision roda em paralelo,
- Narrator espera tudo isso terminar,

…os 24h do batch ficam dentro da janela natural de processamento.
Para corpus piloto pequeno, batch não faz sentido — mas a primitiva
fica pronta para Sessão 9+ usar.

**Tradeoff**: latência alta vs custo metade. Aceito quando o caller
tolera; sync continua disponível como fallback.

## Wiring no orchestrator

A ordem de tentativas (do barato ao caro):

```
para cada mensagem nao classificada:
    1. cache.get(text, pv)        # ~µs, 30-50% hit
       hit -> usar cached label, marcar task done
    2. dedup.find_similar(text)   # ~ms, 10-30% adicional
       hit -> reutilizar label, cachear, marcar task done
    3. queue para batch ou chamada sync
       (decisao por flag --batch / --no-batch)
```

O orchestrator `classify_pending` que une as 3 camadas **não
é entregue nesta sessão** — fica para sessão futura quando wiring
fino for necessário (caso real). As 3 camadas são entregues como
primitivas testadas e prontas para uso.

## Consequências

### Positivas

- Cada nível é independente; pode ser usado isolado (ex: só cache
  em pipeline simples, sem batch).
- Migrations novas (`classify_cache`, `batches`) são idempotentes —
  não afetam vault existente até o classifier ser re-rodado.
- Compatibilidade total com `classify_handler` existente (nível 3
  pode chamar tanto sync quanto batch; cache + dedup são acima).
- Versionamento por `prompt_version` permite invalidação consciente
  de cache em cada bump de prompt sem TRUNCATE manual.

### Compromissos

- **Jaccard ≠ embeddings**: paráfrases ("vou aí" vs "estou indo")
  passam pelo dedup. Aceito; ver dívida #59.
- **Batch tem 24h de latência**: caller que precisar de classificação
  imediata usa sync (`--no-batch`).
- **Pool rolante de 500** pode evictar match relevante se o corpus
  tiver muita inversão temporal — improvável em conversa ordenada,
  mas vale registrar.
- **Embedding-based dedup vs Jaccard**: Jaccard ignora sinônimos.
  Em domínios técnicos onde "PIX" e "transferência" são sinônimos,
  Jaccard não dedupa. Mensagens de WhatsApp de obra costumam ter
  vocabulário concreto (números, nomes, comprovantes) que Jaccard
  serve bem.

## Critério de reabertura — dívida #59

O upgrade para sentence-transformers é justificado quando **uma**
das condições for atingida em corpus de produção (Sessão 11+):

1. **Hit rate Jaccard < 15%** em corpus de 50k+ mensagens. Indica
   que dedup léxico não está pegando as variações que importam.
2. **Narrator V4 reclama de ruído classificacional**: ex: mensagens
   sobre o mesmo assunto recebendo categorias contraditórias.
3. **Operador identifica falsos negativos** em revisão humana
   (mensagem genuinamente similar não dedupada, gerando custo
   redundante).

Trigger meets → criar ADR-009 com decisão concreta de adotar
embeddings + biblioteca específica + threshold de cosine + pool
size adequado a corpus.

## Validação empírica

Phase 8.3 testou as 3 primitivas em fixture sintético:

- 60 mensagens (50 únicas + 50 duplicatas + 20 variantes "+1 token"):
  Cache hits = 20 (dedup interno do set), Jaccard = 3, API = 37 →
  **38% redução**. Em corpus mais homogêneo (tokens
  compartilhados), reduções de até 91% foram observadas.
- Batch lifecycle (submit → poll → fetch) via mock de OpenAI SDK:
  funciona end-to-end (5 requests parseadas com tokens corretamente
  extraídos).
- Transcribe checkpoint com crash simulado: `state.claim` →
  shutdown → `state.reset_running` → `transcribe_pending` retoma e
  processa as 3 tasks (validado).

Custos reais incorridos: **US$ 0.00** (puro mock + módulo isolado).

## Referências

- `src/rdo_agent/classifier/cache.py`
- `src/rdo_agent/classifier/jaccard_dedup.py`
- `src/rdo_agent/classifier/batch.py`
- `tests/test_classify_cache_and_jaccard.py` (30 testes)
- `tests/test_batch_classifier.py` (13 testes)
- `docs/sessions/SESSION_LOG_SESSAO_8_EFFICIENT_TEXT.md`
