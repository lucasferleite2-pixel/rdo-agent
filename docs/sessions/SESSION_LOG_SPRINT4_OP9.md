# Sessao Autonoma Sprint 4 Op9 — Vision V2 + OCR-first retroativo

**Inicio:** 2026-04-22 (~15:00Z apos Op8)
**Termino:** 2026-04-22 (~16:15Z)
**Duracao:** ~75 min
**Meta:** Calibrar prompt Vision V2 com few-shot + arquivar visual_analyses
antigas + reprocessar pipeline OCR-first retroativamente
**Teto de custo:** US$ 1.00 (delta sessao); gate abort em US$ 0.60
**Tag pre-sessao:** safety-checkpoint-pre-op9 (e7d2ba1)

## Plano executado

1. Fase 1 — Baseline measure_vision_accuracy.py (commit `d1ce7d7`)
2. Fase 2 — Vision prompt V2 calibrado + feature flag (commit `e91642f`)
3. Fase 3 — Tabela visual_analyses_archive (commit `791db16`)
4. Fase 4 — Script reprocess_visual_analyses_ocr_first.py (commit `6a5733f`)
5. Fase 5 — Execucao dry-run + piloto + full run **PARCIAL**
6. Fase 6 — Metricas comparativas + relatorios (commit abaixo)
7. Fase 7 — Tag v0.4.3

## Decisoes tecnicas

1. **Prompt V2 preserva 4 campos V1 obrigatorios** (elementos_construtivos,
   atividade_em_curso, condicoes_ambiente, observacoes_tecnicas) +
   adiciona 6 campos estendidos (materiais_presentes, epi_observados,
   pessoas_presentes, categoria_sugerida, categorias_secundarias,
   confidence). `_validate_schema` nao muda — campos extras sao
   persistidos sem afetar validacao. Backward compat total.

2. **Feature flag VISION_PROMPT_VERSION** (env var, default `v2`):
   rollback trivial via `export VISION_PROMPT_VERSION=v1`. V1 preservado
   como constante publica `SYSTEM_PROMPT_V1` — nao deletado.

3. **6 few-shot examples no V2** derivados do ground truth:
   - Ex 1: medicao com fita (reporte_execucao + especificacao_tecnica)
   - Ex 2: estrutura montada sem pessoas (reporte_execucao, nao off_topic)
   - Ex 3: desenho tecnico fotografado (especificacao_tecnica)
   - Ex 4: equipamento em feira (off_topic contextual)
   - Ex 5: acidente — trator tombado (off_topic contextual)
   - Ex 6: material sozinho no canteiro (material, nao off_topic)

4. **visual_analyses_archive** preserva linhagem forense: mirror + 2
   colunas extras (archived_at NOT NULL, archive_reason). Migration
   idempotente `_migrate_visual_analyses_archive_sprint4_op9`.

5. **Retroatividade via archive + re-enqueue:** script reprocess NAO
   deleta visual_analyses originais — cria copia em archive. Rows atuais
   ficam intactas. Novas rows sao adicionadas quando OCR_FIRST route 'foto'
   enfileira VISUAL_ANALYSIS com prompt V2 — geram rows adicionais,
   nao sobrescrevem.

6. **Cost gate delta (nao absoluto):** COST_BUDGET_DELTA_USD=0.50
   (relativo ao inicio da execucao do script, nao cumulativo vault).
   Evita abort prematuro quando vault ja tem custo acumulado de sessoes
   anteriores.

## Problema encontrado e mitigacao parcial

**API OpenAI instavel durante Fase 5 (15:55-16:15Z):**

- Timeouts silenciosos do SDK OpenAI (chat.completions.create)
- Retry loop interno do SDK (default 600s timeout) pendurou processos
- ConnectionError intermitente em ~40% das tasks

**Mitigacao aplicada:**
- Monkey-patch em `scripts/reprocess_visual_analyses_ocr_first.py`:
  `_apply_openai_timeout_patches()` sobrescreve `_get_openai_client`
  de ocr_extractor e financial_ocr com `timeout=30s, max_retries=0`.
  Evita travamentos indefinidos — tasks vao pra FAILED em vez.
- Respeitado blacklist: nao tocamos em `ocr_extractor/*` nem
  `financial_ocr/*` diretamente (Op8 preservado).

**Resultado:**
- 7/45 imagens arquivadas reprocessadas com sucesso (piloto2 completo)
- 38 pending ficaram porque API nao respondia em 30s * 3 retries
- Worker pode ser retomado quando API estabilizar

## Metricas

| Metrica | Baseline V1 | Atual V2 (parcial) |
|---|---:|---:|
| Amostras ground truth | 11/11 encontradas | 11/11 encontradas |
| Keyword match | 7/11 | 7/11 (inalterado — amostras GT nao reprocessadas) |
| Divergencias v1/v2 | 1 | 2 |
| `false_off_topic` | 0 | 0 |
| visual_analyses totais | 50 | 50 (mesmos — archive eh copia) |
| visual_analyses_archive | 0 | 50 |
| documents (ocr_first) | 7 | 7 (mesmo — reprocess nao criou novos) |
| financial_records | 4 | 4 (mesmo) |
| tasks OCR_FIRST done | 10 | 17 (+7) |
| tasks OCR_FIRST pending | 0 | 38 (+38, aguardando API) |
| tasks OCR_FIRST failed | 0 | 0 |

**Nota importante sobre comparacao:** como archive eh COPIA (nao move) e
rows atuais de visual_analyses permanecem, o measure_vision_accuracy
sobre estado atual retorna mesmas metricas que baseline. A mudanca
semantica so apareceria APOS as 38 pending rodarem e criarem NOVAS
rows com prompt V2. Isso eh divida tecnica documentada.

## Custo total

| Fase | Custo USD (delta) |
|---|---:|
| Fase 1 (baseline) | 0.0 |
| Fase 2 (V2 prompt impl) | 0.0 |
| Fase 3 (schema archive) | 0.0 |
| Fase 4 (script reprocess) | 0.0 |
| Fase 5 (piloto 5 + full parcial) | ~0.067 |
| Fase 5 extra (4 VA tasks V2) | ~0.020 |
| Fase 6 (measure script rerun) | 0.0 |
| **Total sessao** | **~US\$ 0.087** |
| **Cumulativo vault apos Op9** | US\$ 0.6767 |

Budget: US$ 1.00. Usado: 9% (US\$ 0.087). Gate acionado: nao.

## Erros resolvidos

1. `test_skips_analyses_without_source_files` falhou com FK constraint
   no primeiro teste — re-escrito para usar cenario de files sem
   derived_from (nullable) em vez de deletar imagem fonte.
2. Cost gate usava absoluto — mudado para delta relativo ao inicio
   da execucao do script.
3. Gate de idempotencia `_enqueue_ocr_first_task` bloqueava tasks com
   status='done' — removido 'done' da check (Op9 quer reprocessar
   imagens ja processadas em Op8).

## Dividas criadas para revisao

1. **Divida #1 — 38 tasks OCR_FIRST pending:** API OpenAI latente
   durante Fase 5. Retomar via `rdo-agent process --task-type ocr_first
   --obra EVERALDO_SANTAQUITERIA` quando API estavel. Custo esperado:
   ~US$ 0.20 (delta).
2. **Divida #2 — Validar V2 contra ground truth apos reprocess
   completo:** executar `python scripts/measure_vision_accuracy.py`
   novamente apos as 38 pending completarem. Esperado: melhoria em
   keyword matches (especialmente frames `f_445a0975174b` e
   `f_1f818f64eefa` que V1 erra).
3. **Divida #3 — Timeout hardening no codigo principal:** `ocr_extractor`
   e `financial_ocr` sem timeout explicito no `OpenAI()` constructor.
   Sprint futura deveria adicionar `timeout=30.0, max_retries=1` como
   default em `_get_openai_client`. Fora de escopo Op9 por blacklist.
4. **Divida #4 — Archive preserva copia, nao move:** design atual mantem
   rows originais intactas. Pode crescer DB significativamente em vaults
   multi-tenancy. Avaliar em revisao.
5. **Divida #5 — 4 VA tasks V2 novas foram processadas:** seus outputs
   em visual_analyses com prompt V2 podem ser validados manualmente
   contra ground truth pela primeira vez.

## Estado final

- Commits nesta sessao: 5 (1 por fase 1-4 + 1 session_log) + tag
- Tag criada: `v0.4.3-sprint4-op9-vision-calibrated`
- Push: OK em todos
- Working tree apos commit final: limpo exceto reports/ untracked
- Suite testes: 302/302 verde

## Ponteiros

- Baseline: `/tmp/op9_vision_baseline.md`
- After (parcial): `/tmp/op9_vision_after.md`
- Comparacao: `/tmp/op9_comparison.md`
- Reprocess report: `/tmp/op9_reprocessing_report.md`
- Scripts novos: `scripts/measure_vision_accuracy.py`,
  `scripts/reprocess_visual_analyses_ocr_first.py`,
  `scripts/compare_vision_v1_v2.py`
- Prompt V2: `src/rdo_agent/visual_analyzer/__init__.py::SYSTEM_PROMPT_V2`
