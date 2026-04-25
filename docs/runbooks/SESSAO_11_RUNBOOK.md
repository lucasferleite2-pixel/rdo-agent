# Runbook — Sessão 11 (Validated at Scale)

**Corpus piloto:** LEO_MIRANDA_PEDREIRO (1.33 GB ZIP)
**Estimado em (Phase 11.0.1):** ~$24.95 ± 50% / ~15.8 h wall clock
**Tag pré-execução:** `safety-checkpoint-pre-execucao-sessao11`
**Conversa 1 fechada com:** vault ingerido + CLI wiring completo + bug Whisper estimate corrigido.

Este runbook é auto-suficiente para o operador rodar as fases caras
(transcribe/classify/vision/narrate) **sem assistente**, monitorando
custo, falhas e regressões em tempo real.

---

## 0. Pré-execução

### 0.1 Sanity checks

```bash
cd ~/projetos/rdo-agent
git status                                  # working tree clean
git log -1 --oneline                        # ultimo commit Phase 11.0.5
.venv/bin/pytest -q 2>&1 | tail -3          # 900 passed
.venv/bin/rdo-agent --version               # 1.6.0+
.venv/bin/rdo-agent status --obra LEO_MIRANDA_PEDREIRO  # vault populated
```

Esperado em `status`:

| task_type        | pending | running | done | failed |
|------------------|---------|---------|------|--------|
| extract_audio    |     340 |       — |    — |      — |
| extract_document |      40 |       — |    — |      — |
| transcribe       |    2189 |       — |    — |      — |
| visual_analysis  |     815 |       — |    — |      — |

### 0.2 Variáveis de ambiente

`.env` já configurado com:
- `OPENAI_API_KEY` (Whisper + classify + vision)
- `ANTHROPIC_API_KEY` (narrator)
- `RDO_VAULTS_ROOT=/home/carcara777/rdo_vaults`

Quotas de custo (defaults da Sessão 6 — ajuste se quiser teto mais
apertado):

```bash
export RDO_DAILY_QUOTA_USD=50           # default; aborta loop se ultrapassar
export RDO_MONTHLY_QUOTA_USD=500        # default
```

### 0.3 Backup pré-execução

```bash
cp ~/rdo_vaults/LEO_MIRANDA_PEDREIRO/index.sqlite \
   ~/rdo_vaults/LEO_MIRANDA_PEDREIRO/index.sqlite.bak-pre-sessao11
```

Se algo der errado nas Fases A-D, restaura via `cp` reverso e re-roda.

### 0.4 Logs

Toda execução escreve em `~/.rdo-agent/logs/LEO_MIRANDA_PEDREIRO/<YYYY-MM-DD>.jsonl`
(StructuredLogger Sessão 6). Grafana / jq queries no fim deste runbook.

---

## Fase A — Pre-processing (extract_audio + extract_document)

**Custo:** $0 (CPU + ffmpeg local).
**Tempo estimado:** ~5–10 min.
**Pré-requisito:** ffmpeg + ffprobe instalados (`which ffmpeg`).

### Comando

```bash
cd ~/projetos/rdo-agent
.venv/bin/rdo-agent process --obra LEO_MIRANDA_PEDREIRO \
                            --task-type extract_audio
```

```bash
.venv/bin/rdo-agent process --obra LEO_MIRANDA_PEDREIRO \
                            --task-type extract_document
```

### Validação

```bash
.venv/bin/rdo-agent status --obra LEO_MIRANDA_PEDREIRO
```

Esperado: `extract_audio` done=340, failed=0; `extract_document` done=40, failed=0.

### Recuperação

Se houver `failed`:

```bash
.venv/bin/rdo-agent pipeline-status --obra LEO_MIRANDA_PEDREIRO
.venv/bin/rdo-agent pipeline-reset --obra LEO_MIRANDA_PEDREIRO --task-type extract_audio
# re-roda process
```

---

## Fase B — Transcribe (Whisper API)

**Custo estimado:** **$9.85** (1642 min × $0.006/min).
**Tempo estimado:** **13.7 h** wall clock single-machine (sem paralelismo
no Whisper). Use `nohup` ou `tmux` para rodar overnight.
**API:** OpenAI Whisper (`whisper-1`).

### Comando

```bash
tmux new -s sessao11-transcribe
cd ~/projetos/rdo-agent
.venv/bin/rdo-agent transcribe-pending --obra LEO_MIRANDA_PEDREIRO \
   2>&1 | tee /tmp/sessao11-transcribe.log
```

`Ctrl-b d` desconecta sem matar. `tmux attach -t sessao11-transcribe`
volta.

### Comportamento esperado

- Idempotente: re-execuções pulam áudios já transcritos.
- CircuitBreaker `openai_whisper`: 5 falhas consecutivas → pausa 60s.
- CostQuota: aborta se acumulado > `RDO_DAILY_QUOTA_USD`.
- Cada áudio emite `stage_start` / `stage_done` / `cost_event` em JSONL.

### Monitoramento (em outro shell)

```bash
# Custo agregado em tempo real
sqlite3 ~/rdo_vaults/LEO_MIRANDA_PEDREIRO/index.sqlite \
  "SELECT COUNT(*), ROUND(SUM(cost_usd),2) FROM api_calls WHERE endpoint='audio.transcriptions'"

# Falhas
.venv/bin/rdo-agent stats --obra LEO_MIRANDA_PEDREIRO --hours 1

# Últimos 20 eventos
.venv/bin/rdo-agent watch --obra LEO_MIRANDA_PEDREIRO --tail 20
```

### Gates

- **STOP** se custo passa de **$15** (50% acima do estimado): investigue
  antes de continuar.
- **STOP** se taxa de falha > 5% em 200 transcribes: provavelmente API
  rate-limited ou audio corrompido em massa. Ver `stats`.

### Validação

```bash
.venv/bin/rdo-agent status --obra LEO_MIRANDA_PEDREIRO
# transcribe done deve aproximar de 2189 (alguns sentinelas
# legítimos de áudios vazios/corrompidos são esperados — ver
# tabela transcriptions com confidence=0).
```

### Recuperação

Crash no meio:

```bash
.venv/bin/rdo-agent pipeline-status --obra LEO_MIRANDA_PEDREIRO
# Detecta tasks em status='running' órfãs e re-pendinga (Sessão 6 #44).
.venv/bin/rdo-agent pipeline-reset --obra LEO_MIRANDA_PEDREIRO \
                                   --task-type transcribe
# Re-roda transcribe-pending (idempotente)
.venv/bin/rdo-agent transcribe-pending --obra LEO_MIRANDA_PEDREIRO
```

---

## Fase C — Classify (gpt-4o-mini)

**Custo estimado:** $0.38.
**Tempo estimado:** 38 min.
**Pré-requisito:** Fase B concluída (transcrições alimentam
classifications via `process --task-type extract_audio` que já criou
`classifications` rows).

### Setup das classifications a partir de transcrições

```bash
# Cria rows em classifications baseadas em transcribe + visual_analysis
# done. Roda via process com handlers default.
.venv/bin/rdo-agent process --obra LEO_MIRANDA_PEDREIRO \
                            --task-type detect_quality
```

### Comando

```bash
tmux new -s sessao11-classify
.venv/bin/rdo-agent classify --obra LEO_MIRANDA_PEDREIRO --throttle 0.2 \
   2>&1 | tee /tmp/sessao11-classify.log
```

### Gates

- **STOP** se custo > $1: 2× over-estimate, investigar.
- **STOP** se >20% das classifications terminam em
  `quality_flag='inconsistente'`: prompt pode estar mal calibrado.

### Validação

```bash
sqlite3 ~/rdo_vaults/LEO_MIRANDA_PEDREIRO/index.sqlite <<EOF
SELECT semantic_status, COUNT(*) FROM classifications
WHERE obra='LEO_MIRANDA_PEDREIRO' GROUP BY semantic_status;
EOF
```

Esperado: maioria em `pending_review` ou `accepted`. Pouco
`pending_classify` restante.

---

## Fase D — Vision (gpt-4o + cascade S9)

**Custo estimado:** $4.08 antes da cascade. Cascade S9 deve reduzir
~30–50% (skipa stickers/blur, pHash dedup, financial routing).
**Tempo estimado:** 20 min.
**API:** gpt-4o vision.

### Comando

```bash
tmux new -s sessao11-vision
.venv/bin/rdo-agent process-visual --obra LEO_MIRANDA_PEDREIRO \
   2>&1 | tee /tmp/sessao11-vision.log
```

### Comportamento esperado

Resumo final imprime contadores das 4 camadas:

| metric             | esperado                                |
|--------------------|-----------------------------------------|
| processed (Vision) | 400–600 (sobreviventes da cascade)      |
| filtered_heuristic | 50–150 (stickers, blur, micro)          |
| deduped (pHash)    | 100–250 (dups visuais)                  |
| routed_ocr         | 50–200 (comprovantes/screenshots)       |

### A/B comparativo (opcional, custa ~$4 a mais)

```bash
# Backup, depois roda sem cascade pra comparar
cp ~/rdo_vaults/.../index.sqlite ~/index_pre_vision.bak
.venv/bin/rdo-agent process-visual --obra LEO_MIRANDA_PEDREIRO --no-cascade
# Compare counts e custo entre os dois logs.
```

### Gates

- **STOP** se custo > $8: cascade não está filtrando, revisar.
- **STOP** se `failed > 50`: imagens corrompidas ou API down.

---

## Fase E — Correlate

**Custo:** $0 (rule-based).
**Tempo:** <1 min mesmo em corpus grande.

```bash
.venv/bin/rdo-agent correlate --obra LEO_MIRANDA_PEDREIRO --workers 4
```

`--workers 4` ativa paralelismo S10 (#50). Em ~7000 mensagens o overhead
de spawn pode anular ganho — sequencial (`sem --workers`) também é OK.

### Validação

```bash
sqlite3 ~/rdo_vaults/LEO_MIRANDA_PEDREIRO/index.sqlite \
  "SELECT correlation_type, COUNT(*) FROM correlations
   WHERE obra='LEO_MIRANDA_PEDREIRO' GROUP BY correlation_type"
```

---

## Fase F — Narrate (Sonnet 4.6)

**Custo estimado:** $10.64 (cascade hierárquica day→week→month→overview).
**Tempo estimado:** 1.1 h.
**API:** Anthropic Claude Sonnet 4.6.

### Cascade completa (S10 #51)

```bash
tmux new -s sessao11-narrate
.venv/bin/rdo-agent narrate --obra LEO_MIRANDA_PEDREIRO --scope obra \
   2>&1 | tee /tmp/sessao11-narrate.log
```

`--scope obra` triggers cascade hierárquica completa: gera todos os
day-narratives, depois week-narratives compondo days, depois
month-narratives, depois `obra_overview` final.

Cache binário S10 (#52): re-execuções pulam narrativas cujo
`dossier_hash` não mudou. Útil em caso de crash mid-run.

### Gates

- **STOP** se custo > $20: cascade está regenerando demais. Verifique
  `forensic_narratives` table — algo invalida cache em massa?
- **STOP** se `validation['passed']=False` em >30% das narrativas:
  prompt issue.

### Validação

```bash
sqlite3 ~/rdo_vaults/LEO_MIRANDA_PEDREIRO/index.sqlite \
  "SELECT scope, COUNT(*), ROUND(SUM(cost_usd),2)
   FROM forensic_narratives
   WHERE obra='LEO_MIRANDA_PEDREIRO' GROUP BY scope"
```

Esperado: day ~152, week ~22, month ~5, obra_overview 1.

---

## Fase G — Export laudo

```bash
.venv/bin/rdo-agent export-laudo --obra LEO_MIRANDA_PEDREIRO \
                                 --output /tmp/laudo_LEO.pdf
```

Validação manual: abrir o PDF, conferir ~150 dias narrados, sumário
executivo, evidências citadas, custos consolidados.

---

## Pós-execução — métricas finais

### Coleta automatizada

```bash
sqlite3 ~/rdo_vaults/LEO_MIRANDA_PEDREIRO/index.sqlite <<EOF
SELECT
  endpoint,
  COUNT(*) AS calls,
  ROUND(SUM(cost_usd), 2) AS total_usd,
  ROUND(AVG(cost_usd), 4) AS avg_usd_per_call
FROM api_calls
WHERE obra='LEO_MIRANDA_PEDREIRO'
GROUP BY endpoint
ORDER BY total_usd DESC;
EOF
```

### Comparativo estimate vs real

| Stage     | Estimate | Real | Δ % |
|-----------|----------|------|-----|
| Transcribe| $9.85    |      |     |
| Classify  | $0.38    |      |     |
| Vision    | $4.08    |      |     |
| Narrator  | $10.64   |      |     |
| **Total** | **$24.95** |    |     |

Preencha após cada fase para Conversa 2 (consolidação de gargalos
+ avaliação de triggers das dívidas #59-#63).

### Logs forenses

```bash
# Custo total por dia
ls ~/.rdo-agent/logs/LEO_MIRANDA_PEDREIRO/

# Falhas por stage
.venv/bin/rdo-agent stats --obra LEO_MIRANDA_PEDREIRO --hours 24

# Eventos de circuit breaker
jq 'select(.event_type=="stage_failed" and .error_type=="circuit_open")' \
  ~/.rdo-agent/logs/LEO_MIRANDA_PEDREIRO/*.jsonl
```

---

## Recovery cheat-sheet

| Sintoma                             | Comando                                         |
|-------------------------------------|-------------------------------------------------|
| Tasks órfãs em 'running'            | `rdo-agent pipeline-reset --obra X --running`   |
| Quota excedida                      | `RDO_DAILY_QUOTA_USD=100 ...` ou aguarda 24h    |
| Circuit breaker aberto              | aguarda 60s; revisa `stats` p/ root cause       |
| API key inválida                    | edita `.env`, re-roda                            |
| DB locked                           | `lsof index.sqlite`; mata processo zumbi        |
| Custo aproximando do gate           | reduz `--max N`, ou pausa via Ctrl+C            |

---

## Conversa 2 — Próximos passos

Após operador concluir as Fases A-G:

1. **Phase 11.9** — análise consolidada (gargalos, custo real vs
   estimate, latência, taxa de falha por stage).
2. **Phase 11.10** — avaliação de triggers das dívidas #59-#63
   (sentence-transformers? CLIP? batch API? cache fuzzy?). Decidir
   reabrir ou manter Pareto.
3. **Phase 11.11** — `SESSION_LOG_SESSAO_11_VALIDATED_AT_SCALE.md` +
   ADR-013 + tag `v1.7-validated-at-scale`.

---

**Tag intermediária pré-execução:** `safety-checkpoint-pre-execucao-sessao11`
**Validação pré-execução vault (Phase 11.1):** 7333 msgs, 3413 files,
1m22s wall, 70 MB RAM peak — dentro do esperado.
