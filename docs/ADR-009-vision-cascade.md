# ADR-009 — Vision cascade em 4 camadas (sem CLIP)

**Data:** 25/04/2026 (noite)
**Status:** ACEITO
**Sprint:** Sessão 9 — Eficiência custo visual (`v1.5-efficient-vision`)
**Dívidas:** #47 (cascata vision), #49 (OCR routing). Inspirado por
ADR-008 (classify 3-tier).

## Contexto

OpenAI gpt-4o (Vision) custa ~$0.01-0.03/imagem; gpt-4o-mini ~$0.003.
Em corpus grande de WhatsApp pt-BR, 60-90% das imagens são meme,
sticker, screenshot ou foto pessoal irrelevante. Sem filtro pré-API,
processar 5000 imagens custa $15-150 só em Vision — desperdiçando
80% no que é descartável.

A camada equivalente para texto (classify) foi resolvida em ADR-008
com pipeline 3-tier (cache, Jaccard, Batch). Esta sessão aplica o
mesmo padrão para visual.

## Decisão

Pipeline em **4 camadas**, do mais barato ao mais caro:

### Camada 1 — `HeuristicImageFilter` (CPU, $0)

Filtros pure Python sobre PIL Image:

- Tamanho do arquivo (skip < 3KB; aggressive: < 5KB).
- Dimensões mínimas (skip < 64x64 conservador, < 100x100 aggressive).
- Detecção de corrupção (PIL.Image.open + img.load() falha).
- Blur via Laplacian variance (warning conservador; skip aggressive
  se < 30).
- Tamanho excessivo: warning conservador; aggressive skipa se > 15MB.

**Modo conservador (default)**: skipa só o óbvio. Apropriado para
corpus piloto (EVERALDO, 96 imagens), onde custo total não justifica
risco de skipar evidência forense legítima por blur/tamanho.

**Modo aggressive (opt-in)** via env var
``RDO_AGENT_VISUAL_FILTER_AGGRESSIVE=true``: thresholds estritos.
Apropriado para corpus produtivo grande.

### Camada 2 — `PerceptualHashDedup` (CPU, $0)

Detecção de imagens visualmente iguais (incluindo redimensionamentos)
via `imagehash.phash` (8x8 DCT-based). Hamming distance ≤ 6 = match.
Tabela `image_phashes(file_id PK, obra, phash, visual_analysis_id)`.
Isolamento por obra (corpus_id).

**Dep nova**: `imagehash>=4.3` (traz PyWavelets + scipy + numpy,
~100MB total). Aceitável; muito mais leve que torch (~2GB) que
ficou em dívida #59/#60.

### Camada 3 — `RoutingClassifier` (CPU, $0, sem ML)

Heurística para identificar imagens que devem rotear para OCR
especializado em vez de Vision API:

- **Comprovante (financial)**: aspect ratio 0.35-0.75 (vertical
  fino) + dimensões médias 200-2400px. Tipicamente PIX print.
- **Screenshot/doc (document)**: densidade de texto via Tesseract
  bbox count ≥ 30.
- **Default (vision)**: continua para Camada 4.

**Tesseract opcional**: quando ausente ou idioma ausente, faz
fail-open (retorna `None` em `_text_dense`; routing usa só
heurísticas de aspect ratio).

### Camada 4 — Vision API (única que custa $$)

Apenas em imagens que sobreviveram às 3 camadas. Wrapper integrado
com:

- `get_openai_vision_circuit()` (singleton novo separado de openai
  chat e whisper — perfis de rate/falha independentes)
- `StructuredLogger.cost_event` para tracking
- `CostQuota.check_or_raise` quando integrado ao orchestrator

A função orquestradora `process_visual_pending` que une as 4 camadas
**não é entregue nesta sessão**. Primitivas isoladas testadas;
wiring fica para sessão futura quando caso real exigir.

## Por que não CLIP / sentence-transformers

Plano original previa Camada 3.5 com CLIP zero-shot
(`clip-vit-base-patch32`) para classificação multi-label de imagens.
Isso traria:

- ~600MB de download do modelo
- ~2GB de torch (PyTorch CPU mínimo)
- ~5s de boot time
- ~50ms/imagem em CPU

A Sessão 8 (ADR-008) já enfrentou pergunta análoga para texto
(sentence-transformers) e decidiu: **adiar para dívida #59 com
triggers**. Aplicamos o mesmo padrão aqui.

**Dívida #60 registrada**: zero-shot CLIP/transformers em corpus
heterogêneo grande. Triggers para ativar:

1. **Corpus > 1000 imagens E pHash hit rate < 20%** — indica que
   dedup léxico de pixel (pHash) não está pegando variações
   semânticas relevantes.
2. **Revisão manual identifica > 10% de "memes vazando"** para
   Vision API — heurísticas estão deixando passar.
3. **Operador humano reclama** de classificação Vision desperdiçada
   em irrelevantes.

Estimativa: 1 sprint pequena (~3-4h). Quando trigger ativar, ADR-010
substitui ou complementa esta decisão.

## OCR Router (#49)

Apesar de ser dívida separada, o `OCRRouter` complementa a Camada 3
do cascade visual:

- `RoutingClassifier.classify(path)` → `RoutingDecision.target ∈
  {financial, document, vision}` (decisão sobre **rotear**).
- `OCRRouter.route(path, hint=...)` → `OCRTarget ∈ {financial,
  document, generic, skip}` (decisão sobre **qual extractor**).

O caller orquestra:

```python
# Pseudo-código do wiring futuro (não nesta sessão):
decision = routing_classifier.classify(image_path)
if decision.target == "vision":
    call_vision_api(image_path)  # Camada 4
else:
    target = ocr_router.route(
        image_path,
        hint=decision.target,  # "financial" ou "document"
    )
    if target == OCRTarget.FINANCIAL:
        from rdo_agent.financial_ocr import extract
    elif target == OCRTarget.DOCUMENT:
        from rdo_agent.document_extractor import extract
    elif target == OCRTarget.SKIP:
        ...
    else:
        from rdo_agent.ocr_extractor import extract
```

`ocr_cache` table evita reprocessamento de mesmo file_id.

## Validação empírica

Phase 9.4 validou primitivas em fixtures **sem alterar EVERALDO**:

- **Heurística + pHash**: 10 fixtures sintéticas → 6 passam, 4
  skipped corretamente (1 corrupted, 1 redimensionada que ficou
  pequena, 2 micro emoji). pHash detectou redimensionamento
  (800x600 vs 400x300 da mesma imagem) com Hamming match.
- **Roteamento sobre EVERALDO read-only**: 10 imagens amostradas;
  5 → document, 5 → vision. Zero modificações no DB. EVERALDO
  intacto.
- **OCR router**: Tesseract com idiomas `por` + `eng` instalados
  durante a sessão (validação confirmou disponibilidade real).
- **Vídeo synthetic**: ffmpeg lavfi gera 8s de testsrc; probe
  retorna duração; extract_frame produz JPEGs válidos com magic
  bytes corretos.

Custo total Phase 9.4: **US$ 0.00** (zero chamadas API).

## Consequências

### Positivas

- Cada camada é independente; pode ser usada isolada ou combinada.
- pHash dedup beneficia também o reprocessamento (re-rodar Vision
  em imagem já analisada anteriormente reaproveita resultado).
- Routing reduz custo Vision quando comprovantes/documentos podem
  ser tratados por OCR mais barato.
- Modo conservador default protege evidência forense em corpus
  pequeno.

### Compromissos

- **Heurísticas léxicas vs semânticas**: variações artísticas
  ("mesma imagem com filtro Instagram") podem escapar do pHash.
  Aceito; ver dívida #60.
- **Tesseract opcional**: routing aspect-ratio-based funciona sem
  Tesseract, mas detecção de "screenshot/doc denso" depende dele.
- **Modo conservador deixa passar mais para Vision API** que
  poderia ser skipado. Trade-off de risco (perder evidência) vs
  custo (Vision ~$0.003-0.01/img). Para corpus grande, ativar
  aggressive via env var.

## Critério de reabertura — dívida #60

Mesmo padrão de #59 (sentence-transformers para classify), agora
para vision:

1. Corpus > 1000 imagens com pHash hit rate < 20%
2. Operador identifica > 10% memes vazando para Vision API
3. Revisão manual aponta classificação Vision desperdiçada

ADR-010 futuro decide tecnologia (CLIP, BLIP, modelo treinado
custom) baseado em qual trigger ativar e qual sintoma observado.

## Referências

- `src/rdo_agent/visual_analyzer/cascade.py`
- `src/rdo_agent/ocr_router/router.py`
- `src/rdo_agent/video/__init__.py`
- `tests/test_vision_cascade.py` (24 testes)
- `tests/test_ocr_router.py` (18 testes)
- `tests/test_video_module.py` (15 testes)
- `docs/sessions/SESSION_LOG_SESSAO_9_EFFICIENT_VISUAL.md`
- ADR-008 — pipeline 3-tier para classify (mesmo padrão de adoção
  consciente de heurística antes de ML pesado)
