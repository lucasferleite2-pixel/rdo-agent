# Sessao Sprint 5 Fase B — Correlator Rule-Based

**Inicio:** 2026-04-23 (~09:50)
**Termino:** 2026-04-23 (~10:30)
**Duracao:** ~40min
**Meta:** Implementar correlator Fase B com 3 detectores rule-based
(temporal, semantico, matematico), integrar no dossier + RDO, regenerar
narrativas.
**Teto de custo:** ~US$ 0.30 (5 narrativas com --skip-cache)
**Tag pre-sessao:** safety-checkpoint-pre-sprint5-fase-b
**Backup DB:** index.sqlite.bak-pre-sprint5-fase-b-20260423-0939

## Resumo executivo

**Todas as 9 fases concluidas.** Tag `v0.6.0-correlator` criada e
empurrada. Correlator rule-based populou 38 correlacoes no piloto
(6 TEMPORAL, 18 SEMANTIC, 6 MATH_VALUE_MATCH, 8 MATH_VALUE_DIVERGENCE),
das quais 10 sao validadas (confidence >= 0.70).

Narrativas regeneradas (5) citam as correlacoes validadas explicitamente
e destacam MATH_VALUE_DIVERGENCE na secao de observacoes forenses.

- Suite: 418 -> 480 verde (+62 novos testes)
- Custo API total: **US$ 0.38** (5 narrativas regeneradas,
  narrator_v2_correlations)
- Zero chamadas a API no correlator/detectores (rule-based puro)

## Plano executado

| Fase | Descricao | Commit |
|---|---|---|
| 1 | `types.py` + CorrelationType enum | `789eaad` |
| 2 | Detector TEMPORAL_PAYMENT_CONTEXT + 14 testes | `b617793` |
| 3 | Detector SEMANTIC_PAYMENT_SCOPE + 18 testes | `76a111e` |
| 4 | Detector MATH_* + 20 testes | `4227c98` |
| 5 | Orquestrador correlator + 10 testes integracao | `81d05a1` |
| 6 | CLI `rdo-agent correlate` | `0ff91c2` |
| 7 | Correlations no dossier + narrator_v2 prompt | `f8f6d06` |
| 8 | Secao correlations no RDO piloto | `9416988` |
| 9 | SESSION_LOG + tag (este) | (este) |

## Decisao arquitetural: schema Fase A mantido

O prompt original descrevia um schema pra tabela `correlations` com
campos `type/strength/subject_type/subject_id/related_ids_json/explanation`
(modelo 1:N cluster). **Ao verificar o DB, encontrei schema Fase A
diferente**: `correlation_type/confidence/primary_event_ref/related_event_ref/
rationale/detected_by` (modelo pairwise 1:1).

Conversa com usuario confirmou: schema Fase A eh superior (aresta de
grafo, queryable, consistente com resto do sistema). Decisao **(A)** da
proposta: manter Fase A e adaptar detectores.

Mapeamento:
- `type` -> `correlation_type` (TEXT enum: TEMPORAL_PAYMENT_CONTEXT,
  SEMANTIC_PAYMENT_SCOPE, MATH_VALUE_MATCH, MATH_INSTALLMENT_MATCH,
  MATH_VALUE_DIVERGENCE)
- `strength 0-100` -> `confidence 0.0-1.0` (dividido por 100)
- `explanation` -> `rationale`
- `subject + related_ids_json` -> `primary_event_ref/source` +
  `related_event_ref/source` (uma linha por par)

## Detectores — design

Cada detector recebe `(conn, obra)` e retorna `list[Correlation]`.
Zero dependencias externas alem de stdlib. Fluxo: fetch financial_records
+ classifications -> matching in-memory -> return.

Helper compartilhado `detectors/_common.py`:
- `fetch_event_texts(conn, obra)`: retorna list[EventText] com texto
  agregado (reasoning + corpo principal + categorias) e timestamp
  naive (TZ descartada, assumindo America/Sao_Paulo)
- `fetch_financial_timestamps(conn, obra)`: FinancialEvent com
  timestamp combinado (data_transacao + hora_transacao)

### TEMPORAL_PAYMENT_CONTEXT (`temporal_v1`)

- Janela: +-30min em torno do financial_record
- Keywords: pix, transferencia, manda, chave, valor, reais, sinal,
  comprovante, transferência
- `confidence = min(unique_matches / 3, 1.0)` (satura em 3 keywords)
- Smoke test piloto: **6 correlacoes**

### SEMANTIC_PAYMENT_SCOPE (`semantic_v1`)

- Janela: +-3 dias
- Normalizacao: `unicodedata.NFKD` + lower + regex `[a-z0-9]+` +
  stopwords PT (~60 entries) + stemming trivial (sufixos "mento",
  "cao", "dade", "ar/er/ir", "ando/endo/indo", "s/ns" etc)
- Overlap >= 2 termos distintos no conjunto tokenizado
- `confidence = min(overlap / 5, 1.0)`
- Smoke test piloto: **18 correlacoes**

### MATH_VALUE_MATCH / INSTALLMENT / DIVERGENCE (`math_v1`)

- Janela: +-7 dias
- Regex: `R\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d+(?:,\d{2})?)`
  (prefixo R$ obrigatorio pra reduzir FP)
- Parse BR: `.` milhar, `,` decimal, converte pra centavos
- Classificacao:
  - `MATH_VALUE_MATCH` (conf 1.0): `|V_menc - V_pago| < R$1`
  - `MATH_INSTALLMENT_MATCH` (conf 0.8): `V_menc == V_pago/2` ou `*2`
  - `MATH_VALUE_DIVERGENCE` (conf 0.6): `V_menc` em `[0.5, 1.5]x V_pago`
    sem match exato (flag pra revisao humana)
- Smoke test piloto: **14 correlacoes** (6 MATCH + 8 DIVERGENCE)

## Orquestrador (`correlator.py`)

API publica:
- `detect_correlations(conn, obra, *, persist=True)`: roda os 3
  detectores concatenando e opcionalmente persiste
- `get_correlations(conn, obra, *, filter_type, min_confidence)`:
  query na tabela (NAO roda detectores)
- `delete_correlations_for_obra(conn, obra)`: suporte pro `--rebuild`
- `find_correlations_for_day/obra_wide`: wrappers retrocompativeis que
  NAO persistem

Import lazy dos detectores pra evitar ciclo
(`_common.py` depende de `correlator.Correlation`).

## Integracao dossier + narrator

`build_day_dossier`: novo campo `correlations` — lista das correlacoes
onde primary_event eh fr do dia OU related_event eh cls presente no dia.
Cada item inclui flag `validated: true` se `confidence >= 0.70`.

`build_obra_overview_dossier`: novo `correlations_summary` com total,
breakdown por tipo, `validated_count`, `top_validated` (top 10 por
confidence).

`prompts.NARRATOR_SYSTEM_PROMPT_V1`: nova secao "Correlacoes" com:
- Explicacao dos 5 tipos e o que cada um signfica
- Diretriz: **citar correlacoes validadas explicitamente** com
  linguagem fatual
- `MATH_VALUE_DIVERGENCE` validadas -> destacar em "Observacoes forenses"
- Inferencias em correlacoes nao-validadas: linguagem cautelosa

`PROMPT_VERSION` bumped: `narrator_v1` -> `narrator_v2_correlations`
(invalida cache de narrativas antigas).

## CLI `rdo-agent correlate`

```
rdo-agent correlate --obra X [--rebuild] [--sample N]
```

Smoke test vault piloto:
```
Correlacoes detectadas (38 total, 0.12s)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ tipo                     ┃ count ┃ conf media ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ SEMANTIC_PAYMENT_SCOPE   │    18 │       0.50 │
│ MATH_VALUE_DIVERGENCE    │     8 │       0.60 │
│ TEMPORAL_PAYMENT_CONTEXT │     6 │       0.50 │
│ MATH_VALUE_MATCH         │     6 │       1.00 │
└──────────────────────────┴───────┴────────────┘
```

## Secao correlations no RDO

Em `scripts/generate_rdo_piloto.py`: novo `_render_correlations_section`
inserido apos o ledger financeiro no markdown. Agrupa por tipo, com
badge `✅` (validada) ou `·` (nao). Omitida se vazia (retrocompativel).

Smoke test RDO 2026-04-06: secao mostra 24 correlacoes (1 TEMPORAL
validada, 3 MATH_VALUE_MATCH validadas, 3 SEMANTIC validadas, 17
nao-validadas incluindo 3 MATH_VALUE_DIVERGENCE).

## Validacao — narrativas regeneradas

5 narrativas regeneradas com `--skip-cache` (prompt_version mudou, cache
teria acionado mesmo sem flag):

| Scope | Ref | Cost | Passed | Correlacao citadas |
|---|---|---:|---|---:|
| day | 2026-04-06 | US$ 0.0617 | NO (1 warn) | 8 mencoes |
| day | 2026-04-10 | US$ 0.0788 | NO (1 warn) | 4 mencoes |
| day | 2026-04-14 | US$ 0.0624 | YES | 2 mencoes |
| day | 2026-04-16 | US$ 0.0396 | NO (1 warn) | 5 mencoes |
| obra_overview | — | US$ 0.1373 | NO (1 warn) | 9 mencoes |
| **Total** | | **US$ 0.3798** | 1 passed, 4 warnings | — |

Warnings sao soft checks do validator F3 (nao bloqueantes). Backup
das narrativas antigas preservado em
`reports/narratives/EVERALDO_SANTAQUITERIA.bak-pre-fase-b-20260423-1015/`.

Exemplo de citacao (day 2026-04-06):

> A correlacao temporal entre a solicitacao da chave e o pagamento eh
> **validada com confianca maxima (1,0)**: a mensagem "Manda a chave"
> (c_131) antecedeu o registro financeiro em exatos 215 segundos —
> menos de quatro minutos —, configurando sequencia causal direta entre
> pedido de chave PIX e efetivacao da transferencia (correlacao
> TEMPORAL_PAYMENT_CONTEXT, detector `temporal_v1`).

Exemplo de MATH_VALUE_DIVERGENCE destacada (mesma narrativa):

> **Sobre a divergencia de valor (MATH_VALUE_DIVERGENCE):** O sistema
> detectou, em eventos anteriores ao dia (c_31 e c_69), mencao a um
> valor de **R$ 3.000,00** em contexto relacionado ao mesmo pagamento,
> contra os **R$ 3.500,00 efetivamente pagos**. [...] Essa divergencia
> de R$ 500,00 pode indicar renegociacao de valor entre a tratativa
> inicial e o fechamento [...]. Recomenda-se verificar os eventos
> c_31 e c_69.

## Arquivos novos

```
src/rdo_agent/forensic_agent/
    types.py                       # CorrelationType + constants
    detectors/__init__.py
    detectors/_common.py           # fetch helpers + EventText/FinancialEvent
    detectors/temporal.py
    detectors/semantic.py
    detectors/math.py

tests/
    test_detector_temporal.py      # 14 testes
    test_detector_semantic.py      # 18 testes
    test_detector_math.py          # 20 testes
    test_correlator_integration.py # 10 testes

docs/sessions/
    SESSION_LOG_SPRINT5_FASE_B.md  # este arquivo
```

## Arquivos modificados

```
src/rdo_agent/forensic_agent/
    __init__.py                    # exports novos
    correlator.py                  # stubs -> orquestrador real
    dossier_builder.py             # correlations + correlations_summary
    narrator.py                    # PROMPT_VERSION bump
    prompts.py                     # secao Correlacoes

src/rdo_agent/
    cli.py                         # comando correlate

scripts/
    generate_rdo_piloto.py         # secao Correlacoes no RDO

tests/
    test_correlator_skeleton.py    # 2 testes NotImplementedError -> comportamento real
```

## Proximos passos (fora do escopo desta sessao)

- **Calibrar thresholds**: janela temporal fixa +-30min; semantic +-3d;
  math +-7d. Em obra maior com mais dados podem precisar ajuste.
- **Detector LLM** pra correlacoes complexas (audio menciona material
  + foto no mesmo dia). Nao prioritario enquanto rule-based entrega.
- **Deduplicacao**: correlations repetidas quando mesma cls menciona
  o mesmo valor 2x (observado em MATH na vault piloto). Nao afeta
  narrativa, mas polui tabela.
- **Stemming mais robusto**: atual perde "instal" vs "instala" em
  "instalar/instalacao". Pode usar `nltk.stem.RSLPStemmer` se valer
  a dependencia.
- **RDO: thumbnail/sample por correlacao tipo** — em obras maiores,
  a secao pode ficar muito longa. Considerar colapsar nao-validadas.

## Custos

- Sessao: **US$ 0.38**
- Acumulado Sprint 5 (Fase A + Fase B): ~US$ 0.38 (Fase A foi mockado)

---

## Addendum — Validação em Caso Real (2026-04-23 tarde)

### Descoberta forense

Após conclusão da Fase B, o operador Lucas forneceu ground truth sobre a obra
EVERALDO_SANTAQUITERIA: existem 2 contratos separados (R$7.000 + R$11.000 = R$18.000
total negociado), não 1 contrato único de R$11.000 como narrativas anteriores inferiram.

### Validação pelo correlator

O detector MATH_VALUE_DIVERGENCE da Fase B detectou automaticamente a inconsistência:
- fr_1 (PIX R$3.500 em 06/04) correlacionado com c_31 mencionando "R$3.000" (confidence 0.6)
- Correlacion não-validada mas sinalizada como ponto de atenção

Investigação manual dos eventos correlacionados (c_31, c_60, c_69) revelou que
a evidência dos 2 contratos ESTÁ no corpus, em áudios do dia 08/04:
- c_31 (08/04 08h46): Everaldo decompõe R$15k em componentes (R$9k + R$3k + R$3k)
- c_60 (08/04 20h36): "vamos fechar nos 11" + empilhamento de pagamentos dos 2 contratos
- c_69 (09/04 16h15): menciona "os 11 lá" + pedido de adiantamento R$3k pra operário

### Narrativa 08/04 gerada pós-descoberta

Com correlações populadas e eventos densos disponíveis, a narrativa day_2026-04-08.md:
- Reconstruiu a renegociação R$15k → R$11k minuto a minuto
- Identificou corretamente a estrutura de 2 contratos (R$7k engradamento + R$11k cobertura)
- Detectou mensagem apagada às 09h13 (possível retratação)
- Detectou repasse R$2.000 a terceiro (c_62) como ponto de atenção
- Usou MATH_VALUE_MATCH validada para vincular pagamentos à negociação
- Cita MATH_VALUE_DIVERGENCE com confidence 0.6 com cautela forense

Qualidade self-assessment: 0.82 confidence.

### Implicação para Fase C (Ground Truth)

A expectativa inicial era que GT seria necessário para suprir ausência de evidência.
Descoberta: evidência ESTAVA no corpus — faltava apenas correlator para direcionar
atenção + dossier amostrar eventos certos.

GT continua valiosa mas com propósito reposicionado:
- ANTES: "fornecer dados ausentes"
- AGORA: "orientar interpretação estruturada"

Fase C permanece no roadmap como feature de polimento forense, não como dependência crítica.

### Dívidas técnicas novas

- #22: MATH_VALUE_MATCH duplica linhas idênticas no DB
- #23: MATH_VALUE_MATCH sem janela temporal (gap 77h correlacionado)
- #26: Detector MATH não diferencia valor unitário vs agregado
- #27: Detector futuro CONTRACT_RENEGOTIATION para padrões tipo "fecha em X"
- #28: dossier_builder.build_obra_overview_dossier deveria priorizar amostragem
       de eventos em dias de alta densidade narrativa

### Tag produzida

v0.6.1-case-validated — Sprint 5 Fase B validada em caso real.
