# Sessão 9 — Eficiência custo (vision + frames + OCR)

**Início:** 2026-04-25 (noite)
**Término:** 2026-04-25
**Duração:** ~3h
**Meta:** Fechar 3 dívidas (#47 vision cascade, #48 frames de vídeo,
#49 OCR router). Reduzir custo OpenAI Vision em 60-80% via filtro
pré-API, completando GRUPO 3 (eficiência custo).
**Teto de custo:** US$ 0.30–1.00 (validação empírica)
**Tag pre-sessão:** `safety-checkpoint-pre-sessao9`
**Tag final:** `v1.5-efficient-vision`

## Resumo executivo

**3 dívidas fechadas + 1 nova registrada (#60) + ADR-009 + 7
premissas auditadas com 1 REFUTED parcial e 1 PARTIAL importante.**

| Item | Mecanismo entregue | Commit |
|---|---|---|
| #47 | Vision cascade 4 camadas (heuristic + pHash + routing + Vision API) sem CLIP | `438be85` |
| #48 | Promoção de `scripts/extract_video_frames.py` → `src/rdo_agent/video/` + integração StructuredLogger | `0dc7693` |
| #49 | `OCRRouter` com fail-open Tesseract + tabela `ocr_cache` | `97c7223` |
| ADR-009 | rationale do cascade + critério de reabertura para CLIP | (este) |
| #60 | upgrade para CLIP/zero-shot quando triggers ativarem | (registrada PROJECT_CONTEXT) |

- Suite: 791 → **848 testes** (+57 novos: 24 + 15 + 18)
- Custo API: **US$ 0.00** (validação 100% em fixtures + read-only EVERALDO)
- EVERALDO **intacto** (96 visual_analyses preservadas).

## Phase 9.0 — Discovery (premissas)

| # | Premissa | Veredito |
|---|---|---|
| P1 | visual_analyzer chama Vision sem filtro | CONFIRMED |
| P2 | sem blur/pHash/binário pré-API | CONFIRMED |
| **P3** | vídeos NÃO são processados | **REFUTED parcial** — `scripts/extract_video_frames.py` JÁ existe e rodou em EVERALDO Sessão 4 Op3b (35 frames). Faltava integração formal com state machine |
| P4 | OCR fragmentado em 3 módulos sem roteador | CONFIRMED |
| **P5** | Tesseract instalado | **PARTIAL** — instalado mas só `eng+osd` (sem `por`). Solução: fallback para `eng` com warning único; instalação de `tesseract-ocr-por` documentada no README |
| P6 | `visual_analyses_archive` ativo | CONFIRMED |
| P7 | sem coluna "skip reason" | CONFIRMED ausente |

A descoberta P3 mudou o escopo da Fase 9.2 — em vez de "implementar
do zero", **promovemos** o script existente.

## Plano executado

| Fase | Descrição | Commit |
|---|---|---|
| 9.0 | Safety tag + discovery + report ao operador (pausa) | — |
| 9.1 | #47 cascade 4 camadas + 24 testes | `438be85` |
| 9.2 | #48 video module promotion + 15 testes | `0dc7693` |
| 9.3 | #49 OCR router + 18 testes | `97c7223` |
| 9.4 | Validação empírica não-destrutiva (4 cenários) | — |
| 9.5 | SESSION_LOG + ADR-009 + dívida #60 + docs | (este) |
| 9.6 | Release v1.5-efficient-vision | (próximo) |

## Decisões arquiteturais e desvios

### Drop de CLIP / transformers / torch (Phase 9.1)

Plano original previa Camada 3.5 com CLIP zero-shot
(`clip-vit-base-patch32`). Aplicamos o **mesmo padrão da Sessão 8**
(ADR-008): adiar dep pesada com triggers concretos.

**Dívida #60 registrada** com triggers:

1. Corpus > 1000 imagens E pHash hit rate < 20%.
2. Revisão manual: > 10% memes vazando para Vision API.
3. Operador identifica desperdício de Vision em irrelevantes.

ADR-009 documenta a decisão completa. Custo evitado nesta sessão:
~2GB de torch + 600MB de modelo CLIP que não pagavam o overhead
para o corpus piloto atual.

### Promoção do script (Phase 9.2)

Discovery refutou P3: frame extraction JÁ EXISTIA como
`scripts/extract_video_frames.py` (Sprint 4 Op3b — 35 frames em
EVERALDO). Em vez de reescrever, **promovi** para
`src/rdo_agent/video/` mantendo:

- Mesma API base (`extract_frames_for_video(conn, obra, video_id)`).
- Mesma lógica de timestamps adaptativos (5/25/50/75/95% com clamp
  ±0.5s).
- Mesma idempotência (sha256-based file_id, INSERT OR IGNORE).
- Mesmo wiring de tasks (VISUAL_ANALYSIS por frame novo).

Adicionei wrapper `process_videos_pending(conn, obra)` que detecta
vídeos sem derivações via LEFT JOIN com `media_derivations`,
integrando com StructuredLogger. Script em `scripts/` reescrito
como CLI shim que importa do módulo.

Audio split de vídeo já existia em
`rdo_agent.extractor.extract_audio_from_video` com
`TaskType.EXTRACT_AUDIO` — fora do escopo desta dívida.

### OCR router NÃO substitui extractors (Phase 9.3)

`OCRRouter` é **coordenador**, não substituição. Os 3 módulos
existentes (`ocr_extractor`, `financial_ocr`, `document_extractor`)
mantêm-se intactos:

- Router decide **qual** chamar via heurísticas + hints externos.
- Wiring entre router e extractors fica para sessão futura
  (mesma decisão de não entregar `process_visual_pending` ainda).
- Cache em `ocr_cache` table evita reprocessamento mesmo quando
  caller decide ignorar router.

### Tesseract com fallback consciente (Phase 9.3)

Discovery em P5 mostrou que sistema tinha apenas idiomas `eng+osd`
(sem `por`). Solução implementada:

- `_resolve_lang()` cacheia lookup; tenta `por` → `eng` → `None`.
- Warning emitido apenas 1× por categoria (não spam).
- `detect_text_presence` retorna `None` (fail-open) quando idioma
  ausente — caller assume "tem texto".

**Surpresa em Phase 9.4**: validação empírica detectou que
Tesseract `por` foi instalado entre Phase 9.0 e Phase 9.4 (provável
ação manual do operador em paralelo). Sistema em produção agora
usa `por` direto, sem fallback. Validação confirma que ambos os
caminhos funcionam.

### Validação NÃO destrutiva (Phase 9.4)

Plano original previa `process-visual --regenerate` em EVERALDO.
Substituído por 4 cenários isolados:

1. **Cascata heurística + pHash em fixtures sintéticas**: 10
   imagens (5 normais, 2 micro, 1 corrupted, 2 dupes em resoluções
   diferentes) → 6 passaram, 4 skipped corretamente; pHash
   detectou dup_1 (800x600) ↔ dup_2 (400x300) da mesma imagem.
2. **Roteamento read-only sobre EVERALDO**: 45 imagens reais; 10
   amostradas → 5 routed for document, 5 for vision. **Zero
   modificações no DB**.
3. **OCR router em fixtures**: routing por extensão (PDF →
   document), por hint (financial), default (generic). Idiomas
   detectados corretamente em produção.
4. **Vídeo synthetic via ffmpeg**: testsrc 8s gerado; probe → 8s
   exato; timestamps adaptativos `[0.5, 2.0, 4.0, 6.0, 7.5]`;
   extract_frame produz JPEGs válidos.

Custo total Phase 9.4: **US$ 0.00**. EVERALDO intacto.

## Métricas finais

### Testes adicionados

| Arquivo | Testes |
|---|---|
| `tests/test_vision_cascade.py` | 24 |
| `tests/test_video_module.py` | 15 |
| `tests/test_ocr_router.py` | 18 |
| **Total** | **57** |

Suite: 791 → 848 testes verde, ~69s execução completa.

### Custos

- Sessão 9 (este): **US$ 0.00**
- Acumulado projeto total: ~US$ 3.16 (inalterado)

### Dívida nova registrada

**#60** — Upgrade pre-classify visual para CLIP/zero-shot quando:

- Corpus > 1000 imagens E pHash hit rate < 20%, OU
- Revisão manual: > 10% memes vazando para Vision API, OU
- Operador identifica desperdício de Vision em irrelevantes.

Estimativa: 1 sprint pequena. Decisão concreta vem em ADR-010.
Documentada em PROJECT_CONTEXT section 9.12.

## Próximos passos (pós-v1.5)

GRUPO 3 (eficiência custo) **completo** — todas as dívidas do
roadmap reformulado para esta fase resolvidas (#45, #46, #47, #48,
#49). Próxima sessão abre GRUPO 4 (escala analítica):

- **Sessão 10 → v1.6-scale-analytics**: #50 (correlator janela +
  workers), #51 (narrator hierárquico), #52 (cache narrativas).

## Custos da sessão

| Op | Descrição | Custo (USD) |
|---|---|---:|
| 9.0 | Discovery | 0.0000 |
| 9.1 | #47 vision cascade (puro código + fixtures) | 0.0000 |
| 9.2 | #48 promoção video script (puro código + ffmpeg local) | 0.0000 |
| 9.3 | #49 OCR router (puro código + Tesseract local) | 0.0000 |
| 9.4 | Validação empírica (read-only EVERALDO + fixtures) | 0.0000 |
| 9.5 | Docs + ADR-009 + #60 | 0.0000 |
| 9.6 | Release | 0.0000 |
| **Total sessão** | | **US$ 0.00** |

Teto autorizado: US$ 1.00. Usado: 0%.
