# ADR-010 — Correlator paralelo (inter-detector) + janela by-detector

**Data:** 25/04/2026 (noite)
**Status:** ACEITO
**Sprint:** Sessão 10 — Escala analítica (`v1.6-scale-analytics`)
**Dívida:** #50 (correlator com janela temporal + workers paralelos).

## Contexto

Premissa P1 do plano original: "correlator é O(N²) em loop único".
Discovery (Phase 10.0) **refutou**: `correlator.py:detect_correlations`
é orquestrador thin (~10 linhas) que invoca 4 detectores
**independentes**:

- TEMPORAL_PAYMENT_CONTEXT (window default 30 minutos)
- SEMANTIC_PAYMENT_SCOPE (window default 3 dias, com time_decay)
- MATH_VALUE_MATCH/MATCH/DIVERGENCE (window default 48 horas)
- CONTRACT_RENEGOTIATION (window default 30 dias)

Cada detector tem sua própria complexidade interna; cada um tem
sua janela calibrada para o tipo de evento que detecta. A
estrutura do orquestrador, sequencial, não é o gargalo principal.

## Decisão

Aplicar **dois ataques** ao custo de correlate em corpus grande:

### 1. Configurabilidade de janela por detector

Cada `detect_*` aceita kwarg `window: timedelta | None = None`.
``None`` mantém comportamento legado (WINDOW da módulo). Override
permite calibrar por contexto:

- TEMPORAL: ampliar a 1-2h se mensagens entre PIX e contexto
  estão longe (operador conversa antes/depois mas o PIX é
  registrado isoladamente).
- SEMANTIC: reduzir a 1d em corpus muito longo (3d default
  vira ruído quando há discussões repetidas semana a semana).
- MATH: reduzir a 24h se valores são citados próximo ao PIX.
- RENEGOTIATION: ampliar a 60d para casos de contratos longos.

### 2. Paralelismo inter-detector via ProcessPoolExecutor

`parallel_detect_correlations(db_path, obra, *, workers,
windows)` em `forensic_agent/parallel.py`:

- 4 workers (default `min(4, cpu_count)`) — 4 é teto natural
  pois há 4 detectores.
- Cada worker abre **própria conexão SQLite** (conn não é
  pickle-safe).
- Persistência **fora dos workers**, no main process após
  `as_completed` (evita conflito de escrita SQLite).
- Erros parciais (1 detector falha, outros completam) são
  reportados em `CorrelationStats.errors_by_detector` mas **não
  levantam** — caller decide.

Em corpus de produção esperado (5GB, 100k+ mensagens), ganho
estimado: 2-4× wall-clock dependendo de quantidades por detector.
Em corpus piloto pequeno (EVERALDO, 226 mensagens), overhead de
spawn pode superar o ganho (validação Phase 10.4: 0.90×, mas
resultado idêntico — regressão zero).

## Por que não paralelismo intra-detector

A alternativa óbvia seria subdividir o trabalho de **um** detector
entre N workers (ex: dividir os 100k pares de SEMANTIC em chunks
de 1000, distribuir entre 8 workers, agregar). Custo de adoção:

- Cada detector exige refatoração específica (loop interno
  diferente em cada um).
- Risk de duplicar correlações se chunks têm overlap.
- Coordenação de progresso fica não-trivial.
- Para corpus < 10k mensagens, ganho marginal vs complexidade
  alta.

Decisão: **adiar para dívida #61** ativada por triggers concretos:

- Sessão 11 (validação real) reportar gargalo em **um** detector
  específico que paralelismo inter (4×) não resolve.
- Profiling identificar detector como bottleneck individual em
  corpus grande.
- Corpus > 100k mensagens onde 1 detector domina tempo total.

Quando ativar: refatorar **apenas** o detector identificado, não
todos. ADR-010+ documentará a tecnologia (chunking + agregação,
ou Ray, ou outra abordagem).

## Consequências

### Positivas

- Janela configurável habilita calibração de produção sem mudar
  código (env vars / `DetectorWindows`).
- Workers independentes: 1 detector com bug não derruba os
  outros (`errors_by_detector` reporta).
- Backwards compat total: `window=None` preserva comportamento
  legado dos 4 detectores.
- Persistência centralizada elimina race condition em SQLite.

### Compromissos

- **Overhead de spawn**: ~30ms por worker em WSL2. Para corpus
  pequeno (<1000 msgs) o paralelismo perde para o sequencial.
  Aceito; o ganho aparece em corpus médio/grande.
- **Workers consumem RAM independente**: 4 workers × 50MB de
  Python boot = 200MB. Em máquina de 4GB pode apertar.
  Configurabilidade via `workers` arg permite ajuste.
- **multiprocessing exige main guard** (`if __name__ ==
  "__main__"`): em scripts standalone, OK; em pytest, OK; em
  notebook, requer atenção (forking pode falhar).

## Critério de reabertura — dívida #61

- Sessão 11 reporta detector individual como gargalo
- Corpus > 100k mensagens com 1 detector dominando tempo
- Operador identifica latência inaceitável em correlate

ADR-013+ futuro decide tecnologia específica para detector
identificado.

## Referências

- `src/rdo_agent/forensic_agent/parallel.py`
- `src/rdo_agent/forensic_agent/detectors/{temporal,semantic,
   math,contract_renegotiation}.py` (param `window` adicionado)
- `tests/test_parallel_correlator.py` (14 testes)
- `docs/sessions/SESSION_LOG_SESSAO_10_SCALE.md`
- ADR-008 (mesma filosofia: ataque mais barato + adiar pesado
  com triggers)
