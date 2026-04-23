# Sessao 1 — Polimento + Ground Truth (Sprint 5 Fase C)

**Inicio:** 2026-04-23 (~15:20)
**Termino:** 2026-04-23 (~15:55)
**Duracao:** ~35min
**Meta:** Resolver 6 dividas tecnicas antes da Fase C + implementar
Ground Truth Injection pro narrator verificar corpus vs fatos
conhecidos do operador.
**Teto de custo:** ~US\$ 1.00 (2 narrativas EVERALDO com GT)
**Tag pre-sessao:** safety-checkpoint-pre-sessao1-polimento-gt
**Backup DB:** index.sqlite.bak-pre-sessao1-polimento-gt-20260423-1520

## Resumo executivo

**Todas as 10 fases concluidas.** Tag `v0.7.0-ground-truth-polish`
criada e empurrada.

- 6 dividas tecnicas fechadas (#14, #19, #20, #22, #23, #28)
- Fase C Ground Truth operacional: CLI --context, dossier injeta,
  narrator troca prompt V1 -> V3_GT automaticamente
- 2 narrativas regeneradas com GT real; ambas **passed=YES** (primeira
  vez desde Fase B que a validacao critical passa limpa no piloto)
- Suite: 480 -> 515 verde (+35 novos testes)
- Custo API sessao: **US\$ 0.38** (regen day 04-08 + obra_overview
  com GT)

## Plano executado

| Fase | Descricao | Commit |
|---|---|---|
| 0 | Safety tag + backup DB | — |
| 1 | fix #14 validator regex horario HH:MM + HHhMM + seconds | `5892d9b` |
| 2 | fix #19 --skip-cache invalida cache (force=True) | `3057dcc` |
| 3 | fix #20 cost zero quando API descartada | `c5e7cd0` |
| 4 | fix #22 dedup + #23 janela math 7d -> 48h | `2ec3292` |
| 5 | fix #28 overview prioriza dias densos | `4e710f2` |
| 6 | regen overview pos-polimento (validacao das fixes) | `f1c035d` |
| 7 | schema + loader ground truth YAML | `ed4ad86` |
| 8 | dossier + narrator com GT (prompt V3_GT) | `3fca476` |
| 9 | CLI `rdo-agent narrate --context` | `45d88b9` |
| 10 | SESSION_LOG + tag (este) | (este) |

## Dividas tecnicas fechadas

### #14 — validator regex horario

Sintoma: `_check_horarios_preservados` buscava literal HH:MM na
narrativa. Sonnet 4.6 escreve em estilo PT-BR "11h13" -> narrativa
com horario CORRETO falhava o check critical, gerando warnings
falso-positivo.

Fix: `_horario_pattern(hhmm)` gera pattern
`\\b{HH}[:h]{MM}(?::\\d{2}|min)?\\b` aceitando "11:13", "11h13",
"11:13:00", "11h13min". +3 testes.

Impacto na regeneracao: passed=YES pela primeira vez desde Fase B.

### #19 — --skip-cache nao invalidava cache

Sintoma: flag bypassava o pre-check da CLI mas `save_narrative`
fazia seu proprio `_find_existing_narrative` e retornava
was_cached=True — a narrativa recem-gerada era DESCARTADA silenciosamente.

Fix: `save_narrative(..., force: bool = False)`. CLI passa
`force=skip_cache`. Quando True + cache hit, DELETE row antiga
e INSERT nova (id novo). +2 testes.

### #20 — cost reportava API calls descartadas

Sintoma: `total_cost += narration.cost_usd` acontecia ANTES de
`save_narrative`; se o save retornasse was_cached=True, o custo era
somado mesmo com a narrativa descartada.

Fix: cost adicionado DEPOIS do save; se was_cached=True,
`effective_cost=0`. Stdout mostra `[API call descartada — cache hit]`
para transparencia.

### #22 — MATH_VALUE_MATCH duplicatas

Sintoma: quando mesma classification mencionava R\$3500 duas vezes
(ex: "R\$3.500,00 ... total R\$3500 fechado"), emitia 2 Correlations
identicas.

Fix: dedup de valores dentro da mesma cls antes de classificar
(`set` preservando ordem). +1 teste dedup, +1 teste confirmando que
valores DISTINTOS continuam correlacionando.

### #23 — MATH sem janela temporal

Sintoma: janela de +-7 dias correlacionava eventos com gap de 77h
(>3 dias), adicionando ruido narrativo (um R\$3.500 mencionado muito
antes/depois raramente eh a mesma transacao).

Fix: `WINDOW = timedelta(hours=48)`. +2 testes (dentro/fora 48h).

Efeito: no piloto, correlations cairam de 38 -> 28.

### #28 — dossier overview perdia dia denso

Sintoma: `build_obra_overview_dossier` amostrava primeiros 30 +
ultimos 20 = 50 eventos fixos. No piloto EVERALDO com 239 eventos,
dia 08/04 (48 eventos, negociacao do C2) ficava INTEIRAMENTE FORA
da amostra — narrativa overview nao conseguia detalhar o dia que
deu origem ao C2.

Fix: UNIAO de:
- primeiros 30 (ancora inicial)
- ultimos 20 (ancora final)
- TODOS eventos dos top-5 dias com mais eventos (densidade narrativa)

Deduplicado por event.id, reordenado cronologicamente.

Efeito no piloto: sampled subiu de 50 -> 195 eventos, dia 08/04
integralmente incluso. +1 teste regressao.

## Fase C — Ground Truth Injection

### Modulo novo: `src/rdo_agent/ground_truth/`

```
ground_truth/
  __init__.py       # exports publicos
  schema.py         # dataclasses (GroundTruth, ObraReal, Canal,
                    #              Contrato, PagamentoConfirmado,
                    #              PagamentoPendente, Totais,
                    #              EstadoAtual, ProblemaConhecido)
  loader.py         # load_ground_truth() + GroundTruthValidationError
```

Dependencia adicionada: `pyyaml>=6.0` em `pyproject.toml`.
Import lazy em loader.py — erro claro `pip install pyyaml` se dep
ausente.

### Integracao no pipeline

**`dossier_builder.py`**:
- `build_day_dossier(conn, obra, date, gt=None)`
- `build_obra_overview_dossier(conn, obra, gt=None)`
- Quando `gt` fornecido, injeta chave `ground_truth` no dossier (dict
  serializado via `asdict`, sem o campo `raw`)
- Hash do dossier muda automaticamente com GT -> cache invalidado
  sem precisar --skip-cache

**`prompts.py`**:
- `NARRATOR_SYSTEM_PROMPT_V3_GT` = V1 + bloco sobre GT com 6
  diretrizes obrigatorias:
  1. CONFORME/DIVERGENTE/NAO VERIFICAVEL por asserção
  2. Usar GT pra resolver ambiguidades (ex: 2 contratos vs 1 valor)
  3. Nao inventar fatos ausentes no GT E no corpus
  4. Citar pagamentos por contrato_ref ("sinal C1" vs "R\$3500")
  5. Destacar divergencias financeiras em "Observacoes forenses"
  6. Secao final "Verificacao contra Ground Truth"

**`narrator.py`**:
- `PROMPT_VERSION_GT = "narrator_v3_gt"`
- `_select_prompt_and_version(dossier)`: auto-switch V1/V3 baseado
  em `dossier.get("ground_truth")`
- `narrate()` usa prompt/version dinamicos

**`cli.py`**:
```
rdo-agent narrate --obra X --dia YYYY-MM-DD \
  --context docs/ground_truth/X.yml \
  [--skip-cache] [--scope day|obra|both]
```
- Valida arquivo (exit 2), parse YAML (exit 2 se invalido), import
  yaml (exit 3 se dep ausente)
- Feedback inicial: "+ Ground Truth carregado: X.yml (N contratos,
  M pagamentos confirmados)"

## Validacao — narrativas regeneradas

Backup: `reports/narratives/EVERALDO_SANTAQUITERIA.bak-pre-gt-20260423-1545/`

### Day 2026-04-08 + GT (fase 10 validacao)

| Metrica | Valor |
|---|---|
| Custo | US\$ 0.1429 |
| Passed | **YES** (1 soft warning) |
| Tamanho | 17.166 chars |
| Mencoes a C1/C2 | 25 |
| "ground truth" | 6 |
| Secao "Verificacao contra Ground Truth" | **SIM** |
| Divergencias detectadas | **"Nenhuma"** (conforme realidade) |

Destaques da narrativa:
- Identifica explicitamente C1 (R\$7.000, tesouras+tercas) como
  PRE-EXISTENTE ao dia 08/04 (fechado em 06/04)
- Identifica C2 (R\$11.000, acabamento) como FECHADO no dia 08/04
- Secao "Apenas no GT (sem evidencia digital)" lista 4 fatos que
  o GT afirma mas o corpus do dia nao cobre (sinal C2 em 16/04,
  saldo C1 em 10/04, reembolso 14/04, problema alambrado 15/04)
- Resolveu ambiguidade da transcricao "11 mais os 13" como
  possivel erro de audio para "11 mais os 3,5" (R\$14.500 total)
- Zero divergencias materiais reportadas — consistente com a
  realidade (o GT eh o fiscal aqui; narrar sem contradicao era
  o resultado esperado)

### Obra overview + GT

| Metrica | Valor |
|---|---|
| Custo | US\$ 0.2408 |
| Passed | **YES** (6 soft warnings) |
| Tamanho | 16.871 chars |
| Mencoes a C1/C2 | 24 |
| Ground Truth mencoes | 6 |
| Cronologia em blocos | 6 periodos datados |

Narrativa se organiza em 6 blocos cronologicos (04/04 impasse inicial,
06/04 fechamento C1 + sinal, 07-08/04 execucao+reabertura/C2, 09-10/04
quitacao C1, 11-14/04 execucao+reembolso, 15-16/04 alambrado+sinal C2).
A verificacao vs GT esta embutida em "Observacoes Forenses" com 5
observacoes cada uma cruzando corpus com GT (divergencia de escopo
08/04, MATH_VALUE_DIVERGENCE, sinal menor pedido, ausencia de
contrato escrito, responsabilidade terceiros no alambrado).

## Suite de testes

Baseline: 480 -> Final: **515** (+35 testes novos)

Distribuicao dos testes novos:
- +3 test_narrator_validator (regex horario formats)
- +2 test_narrator_persistence (force=True)
- +4 test_detector_math (dedup + janela 48h)
- +2 test_dossier_builder (overview dia denso)
- +15 test_ground_truth_loader (happy path + validacao)
- +4 test_dossier_builder (GT injection)
- +3 test_narrator (V1 vs V3_GT selection)
- +3 test_cli_narrate (--context error paths + happy)

Zero testes existentes quebraram (algumas assertions adaptadas).

## Custos

- Sessao 1 (este): **US\$ 0.38** (1 day + 1 overview com GT)
- Acumulado Sprint 5: **US\$ 0.77** (Fase A mockado + Fase B + Fase C)

## Proximos passos sugeridos

- GT injection para narrativas ainda nao regeneradas (04-06, 04-10,
  04-14, 04-16) — estimativa +US\$ 0.40
- Ampliar GT schema pra suportar varios canais (uma obra, N
  fornecedores) se outro piloto surgir
- Remover a dependencia explicita `everaldo_ainda_no_canteiro` da
  schema — legado do piloto, nao generico (ja marcado opcional mas
  merece limpeza futura)
- MATH_VALUE_MATCH de R\$3.500 de fr_2 com c_60 (gap -40h) ainda
  pode ser refinado: o GT diz que fr_2 eh saldo C1 (pago 04-10),
  e c_60 de 04-08 discute o C2 — gap de -40h dentro da janela 48h
  causa correlation espuria. Solucao possivel: usar GT.contrato_ref
  como restricao de correlacao (detector so associa cls->fr do
  mesmo contrato quando GT resolve)
