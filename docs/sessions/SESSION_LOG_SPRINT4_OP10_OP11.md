# Sessao Autonoma Sprint 4 Closure — Op10 (RDO financial) + Op11 (hardening)

**Inicio:** 2026-04-22 (~16:30Z apos Op9 final)
**Termino:** 2026-04-22 (~17:15Z)
**Duracao:** ~45 min
**Meta:** Fechar Sprint 4 — integrar financial_records no RDO (Op10) +
resolver 4 dividas tecnicas (Op11)
**Teto de custo:** US$ 1.00 delta; abort em $0.60
**Tag pre-sessao:** safety-checkpoint-pre-op10-11 (32a94bf)

## Resumo executivo

**7/7 fases concluidas. Custo delta: US$ 0.00 (0% do teto).**

Op10 e Op11 são 100% trabalho de codigo + refactor — nenhuma chamada
API necessaria. O retry_failed_ocr rodou em dry-run contra producao
e detectou 0 suspeitas (Op9 reprocess ja havia limpado).

## Plano executado

1. Fase 1 — Op10 integracao financial no RDO (commit `adf1b3a`)
2. Fase 2 — Regerar 4 RDOs com suffix `_v2_op10` (outputs, nao commit)
3. Fase 3 — Divida #9 timeout/retry nativos (commit `cc1da1e`)
4. Fase 4 — Divida #10 archive move-style (commit `0ee39ee`)
5. Fase 5 — Divida #11 video frames pulam OCR (commit `5112c67`)
6. Fase 6 — Divida #12 retry_failed_ocr script (commit `6df2f87`)
7. Fase 7 — SESSION_LOG + tag (este commit)

## Detalhe por fase

### Fase 1 (Op10) — Integracao financial_records no RDO

Novo bloco visual no RDO piloto exibindo comprovantes PIX/TED/boleto
do dia em tabela markdown:

```markdown
## 💰 Pagamentos registrados (comprovantes)

| Hora | Valor | Tipo | De → Para | Descrição |
|---|---:|:---:|---|---|
| 11:13 | R$ 3.500,00 | PIX | CONSTRUTORA...→ Everaldo... | 50% de sinal... |

**Total do dia:** R$ 3.500,00
```

Posicionamento: apos "Resumo do dia", antes das categorias semanticas
(destaque maximo pra forensics). Seção **omitida** se nao ha
comprovantes no dia.

**Novas funcoes em `scripts/generate_rdo_piloto.py`:**
- `_fetch_financial_records_for_date(conn, obra, date)`
- `_format_brl(cents)` — "R$ 3.500,00" estilo brasileiro
- `_truncate(s, maxlen)` — ellipsis pra campos longos
- `_render_financial_section(records)` — lista de linhas markdown
- `render_markdown(*, financial_records=None)` — kwarg opcional
- `generate_rdo` — busca automaticamente e repassa

**Testes (20 novos):** `_format_brl` parametrizado 8 casos (inclui
negativo, zero, 12M reais), fetch 4 cenarios, render 4 cenarios
(empty/header/total/truncate), integração end-to-end 4 cenarios
(com/sem PIX, multi-cronologico, posicionamento).

### Fase 2 — Regerar 4 RDOs afetados

Gerados + renomeados com sufixo `_v2_op10`:
- `reports/rdo_piloto_EVERALDO_SANTAQUITERIA_2026-04-06_v2_op10.md` (4.4KB) + .pdf
- `reports/rdo_piloto_EVERALDO_SANTAQUITERIA_2026-04-10_v2_op10.md` (8KB) + .pdf
- `reports/rdo_piloto_EVERALDO_SANTAQUITERIA_2026-04-14_v2_op10.md` (9.9KB) + .pdf
- `reports/rdo_piloto_EVERALDO_SANTAQUITERIA_2026-04-16_v2_op10.md` (4.7KB) + .pdf

Cada RDO agora exibe a secao de pagamentos com valores PIX:
| Data | Valor | Descrição |
|---|---:|---|
| 06/04 | R$ 3.500,00 | 50% de sinal do serviço de serralheria |
| 10/04 | R$ 3.500,00 | (sem descrição) |
| 14/04 | R$ 30,00 | Gasolina tinta |
| 16/04 | R$ 5.500,00 | Metade do serviço telhado |

**Total no periodo:** R$ 12.530,00 agora visivel em RDO.

### Fase 3 (Op11) — Divida #9 timeout/retry nativos

`src/rdo_agent/ocr_extractor/__init__.py::_get_openai_client`:

```python
OPENAI_CLIENT_TIMEOUT_SEC = 30.0
OPENAI_CLIENT_MAX_RETRIES = 3
return OpenAI(api_key=key, timeout=30.0, max_retries=3)
```

Antes: default 600s sem retry. Apos Op9 tinha monkey-patch em
`reprocess_visual_analyses_ocr_first.py` (30s + max_retries=0) que
resolvia mas abortava tasks em vez de retry. Agora valor sensato
nativo: 30s × 4 tentativas = ~120s max por task, vs 600s antigo.

Monkey-patch `_apply_openai_timeout_patches` REMOVIDO do script
reprocess. Teste novo: `test_get_openai_client_has_timeout_configured`
valida constantes publicas + client real tem `timeout=30.0` e
`max_retries=3`.

### Fase 4 (Op11) — Divida #10 archive move-style

Substitui copia-e-preserva (Op9 criou 54 archive rows separadas) por
move-style via ponteiros forward na propria tabela:

**Schema:**
- `visual_analyses` ganha colunas `superseded_by INTEGER` +
  `superseded_at TEXT` (ALTER TABLE idempotente)
- View `visual_analyses_active AS SELECT * FROM visual_analyses
  WHERE superseded_by IS NULL`

**Handler (`visual_analyzer.visual_analysis_handler`):**
Apos INSERT de row V2 nova, UPDATE marca rows antigas com mesma
imagem-fonte (via `derived_from=image_file_id` join) como
`superseded_by=<new_id>` + `superseded_at=now`.

**Backfill one-shot (`scripts/backfill_superseded_by.py`):**
Marca rows pre-Op11 que foram criadas no reprocess Op9. Rodado em
producao contra EVERALDO:
- total_imagens_com_analyses: 44
- grupos_multi (>=2 analyses): 41
- **rows_marcadas como superseded: 52**

Estado pos-backfill producao:
- 96 visual_analyses total
- **44 active** (superseded_by IS NULL)
- 52 superseded

**8 testes novos:** migration idempotente, view filter, handler
insert marca V1 antiga, backfill N-ary (v1+v2+v3 → v1 v2 superseded),
backfill idempotente, dry-run, solo skip.

### Fase 5 (Op11) — Divida #11 frames de video pulam OCR

Frames de video nao contem texto — chamar OCR neles custa
~$0.005/frame sem proveito. ~35 frames em EVERALDO.

`_is_video_frame(conn, file_id)` helper em `ocr_extractor`:
segue `files.derived_from` e checa `parent.file_type='video'`.

`ocr_first_handler` faz early-return quando `_is_video_frame`:
enfileira `VISUAL_ANALYSIS` direto e retorna
`'routed:visual_analysis (skipped_ocr:video_frame)'`.

**3 testes novos:** frame de video skipa OCR (0 API calls), imagem
original ainda usa OCR normalmente, helper unit test (video, nao-video,
file_id inexistente).

Economia estimada: ~$0.175 em vault com 35 frames, escalavel em
vaults com mais videos.

### Fase 6 (Op11) — Divida #12 retry_failed_ocr

`scripts/retry_failed_ocr.py` identifica rows em
`visual_analyses_active` com markers de falha e re-enfileira
OCR_FIRST. Markers: `_sentinel`, `Unterminated string`,
`malformed_json_response`, `confidence=0.0`.

Rodado em producao: **0 suspeitas detectadas** — Op9 reprocess ja
havia gerado analyses validas pra todas as rows. Script fica
disponivel como ferramenta preventiva.

**6 testes novos:** detect sentinel, ignore valid, dry-run no-op,
live enqueue, skip pending, skip orphan.

## Metricas

| Metrica | Antes Op10+11 | Depois |
|---|---:|---:|
| Testes | 302 | **340** (+38 em 5 novas suites) |
| visual_analyses total | 96 | 96 (inalterado) |
| visual_analyses_active | — | 44 (view criada) |
| visual_analyses superseded | — | 52 (backfilled) |
| RDOs com ledger financial | 0 | **4** (06/10/14/16 abril) |
| Dividas tecnicas ativas | 4 (#9,#10,#11,#12) | **0** |
| Cost cumulativo vault | US\$ 1.0983 | US\$ 1.0983 (inalterado — sem API calls) |

## Commits

| Commit | Descricao |
|---|---|
| `adf1b3a` | feat(sprint4-op10): secao de pagamentos no RDO piloto |
| `cc1da1e` | fix(ocr-extractor): timeout/retry nativos — divida #9 |
| `0ee39ee` | fix(archive): archive move-style superseded_by — divida #10 |
| `5112c67` | fix(ocr-handler): video frames pulam OCR — divida #11 |
| `6df2f87` | fix(ocr-retry): retry_failed_ocr script — divida #12 |
| (este) | docs(sprint4): SESSION_LOG Op10+Op11 closure |

## Custo total sessao

**US$ 0.00 delta** (0% do teto US$ 1.00). Cumulativo vault: US$ 1.0983.

Nao houve chamadas API nesta sessao. Op10 e Op11 foram 100% trabalho
de:
- Edicao de codigo (scripts + modulos)
- Testes unitarios com FakeClient
- Migrations de schema
- Backfill SQL local
- Regeneracao de RDOs offline

## Dividas resolvidas

| # | Descricao | Status |
|---|---|---|
| #9 | Timeout hardening ocr_extractor/financial_ocr | ✅ RESOLVIDA |
| #10 | Archive move-style com superseded_by | ✅ RESOLVIDA |
| #11 | Frames de video pulam OCR | ✅ RESOLVIDA |
| #12 | Retry de JSON-truncados | ✅ RESOLVIDA (script criado; 0 suspeitas em prod) |

## Dividas remanescentes

Nenhuma da Op9. Possiveis para futuras sprints (nao bloqueadoras):

1. **`semantic_classifier` ainda consulta `visual_analyses` direto**
   (nao `visual_analyses_active`). Com superseded_by funcional,
   classificacoes rodariam apenas em rows ativas. Fora de escopo
   Op10/11 (blacklist do classifier preservada). Sprint futura.
2. **`financial_records` nao integrada a classifications como
   source_type='financial_record'**. Atualmente aparece so no RDO
   como secao dedicada. Para unificacao de fontes, poderia virar
   source_type. Decisao pra Sprint 5.
3. **4 tasks VA pending antigas Op8 (nao Op9)** — 59 do Op8 +
   45 novas Op9 foram processadas. Se houver residuos de Op7 ou
   antes, `rdo-agent process --task-type visual_analysis` drena.

## Estado final

- Commits nesta sessao: 6 (1 Op10 + 4 Op11 + 1 SESSION_LOG)
- Tag a criar: `v0.4.4-sprint4-closure`
- Push: OK em todos os commits
- Working tree: limpo exceto `reports/` untracked (RDOs regenerados)
- Suite testes: **340/340 verde**

## Ponteiros

- RDOs regenerados: `reports/rdo_piloto_EVERALDO_SANTAQUITERIA_2026-04-{06,10,14,16}_v2_op10.{md,pdf}`
- Backfill output producao: 52 rows marcadas superseded_by em EVERALDO
- Prompt V2 (Op9): `src/rdo_agent/visual_analyzer/__init__.py::SYSTEM_PROMPT_V2`
- Handler OCR-first: `src/rdo_agent/ocr_extractor/__init__.py::ocr_first_handler`

## Fim

Sprint 4 completa: Op0 - Op11. Total de 11 operacoes desde
2026-04-22, todas com commits pushed e testes verdes.

Tags criadas:
- `v0.4.0-sprint4-ingestao` (Op0-6)
- `v0.4.1-sprint4-op7-summary-total`
- `v0.4.2-sprint4-op8-ocr-first`
- `v0.4.3-sprint4-op9-vision-calibrated`
- **`v0.4.4-sprint4-closure`** (este)
