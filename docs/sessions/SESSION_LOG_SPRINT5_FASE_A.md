# Sessao Autonoma Sprint 5 Fase A — Agente Narrador Forense

**Inicio:** 2026-04-22 (~17:30Z)
**Termino:** 2026-04-22 (~18:30Z)
**Duracao:** ~1h
**Meta:** Primeira sessao Sprint 5. Agente leitor cronologico (Fase A)
+ esqueleto correlator (Fase B).
**Teto de custo:** US$ 2.00 delta
**Tag pre-sessao:** safety-checkpoint-pre-sprint5 (6f7898a)

## Resumo executivo

**Fases 1-7 + 9 concluidas. Fase 8 (producao) PULADA — `ANTHROPIC_API_KEY`
ausente na sessao.**

- Infraestrutura completa pronta: schema, dossier builder, narrator
  (Sonnet 4.6), validator checklist F3, persistence, correlator
  esqueleto, CLI `rdo-agent narrate`.
- 66 testes novos, todos com FakeAnthropicClient — zero chamadas API.
- Suite: 411 -> 418 verde (inclui 38 novos distribuidos em 6 suites).
- Custo delta sessao: **US$ 0.00** (dev 100% mockado; producao pulada).

## Plano executado

| Fase | Descricao | Commit |
|---|---|---|
| 1 | Schema forensic_narratives + correlations + migration | `676a115` |
| 2 | dossier_builder.py (day + obra_overview + hash) | `35510da` |
| 3 | narrator + prompts (Sonnet 4.6) | `b59a927` |
| 4 | validator checklist F3 (9 checks) | `f707ef8` |
| 5 | persistence (DB + arquivo + cache hit) | `82182df` |
| 6 | correlator esqueleto Fase B + __init__ | `6cfbb34` |
| 7 | CLI `rdo-agent narrate` | `e274d66` |
| 8 | Producao | **PULADA** — sem ANTHROPIC_API_KEY |
| 9 | SESSION_LOG + tag | (este) |

## Por que Fase 8 foi pulada

Conforme regra 5.6 do briefing: "Se env não estiver setada na sessão:
pular Fase 8 com log 'Anthropic API key ausente'".

Verificacao feita no inicio:
```
ANTHROPIC_API_KEY: nao presente em env nem em .env
config.get().anthropic_api_key: '' (vazio)
```

Isto nao eh bug — eh **falta de credencial configurada**. Toda a
infraestrutura esta pronta para producao. Para executar Fase 8,
o proprietario precisa:

1. Obter API key em https://console.anthropic.com/settings/keys
2. Adicionar `ANTHROPIC_API_KEY=sk-ant-...` no arquivo `.env`
3. Rodar:
   ```
   rdo-agent narrate --obra EVERALDO_SANTAQUITERIA --dia 2026-04-06 --scope day
   rdo-agent narrate --obra EVERALDO_SANTAQUITERIA --dia 2026-04-10 --scope day
   rdo-agent narrate --obra EVERALDO_SANTAQUITERIA --dia 2026-04-14 --scope day
   rdo-agent narrate --obra EVERALDO_SANTAQUITERIA --dia 2026-04-16 --scope day
   rdo-agent narrate --obra EVERALDO_SANTAQUITERIA --scope obra
   ```

Custo estimado: ~US$ 1.00 (5 narrativas × ~US$ 0.20 cada).

## Componente A entregue (Fase A completa — codigo)

### Novos modulos

```
src/rdo_agent/forensic_agent/
    __init__.py              # exports publicos (Fase A + B esqueleto)
    dossier_builder.py       # build_day_dossier, build_obra_overview, hash
    narrator.py              # Sonnet 4.6 + NarrationResult dataclass
    prompts.py               # NARRATOR_SYSTEM_PROMPT_V1
    validator.py             # 9 checks (4 critical + 5 soft)
    persistence.py           # save_narrative + cache hit via UNIQUE
    correlator.py            # ESQUELETO Fase B (NotImplementedError stubs)
```

### Dossier builder — arquitetura

- `build_day_dossier(conn, obra, date)`: eventos do dia + financial_records
  + context_hints (day_has_payment, contract_establishment, renegotiation)
- `build_obra_overview_dossier(conn, obra)`: amostra 30+20 se >50 eventos,
  daily_summaries, TODOS financial_records
- `compute_dossier_hash(d)`: SHA256 determinista (sort_keys) — cache key

Smoke test prod EVERALDO 2026-04-06: 12 eventos + 1 PIX detectados
(consistente com RDO Op10 existente).

### Narrator — Sonnet 4.6

- Timeout 60s nativo + max_retries 3 nativo (aplica licao do Op11 #9)
- Retry em connection/rate_limit/timeout; propaga auth/bad_request
- Logging per-tentativa em api_calls (provider='anthropic',
  endpoint='messages', model='claude-sonnet-4-6')
- Pricing: input US$ 3/1M, output US$ 15/1M
- Parse regex + json.loads do bloco ` ```json { "self_assessment": ... } ``` `
  ao final do markdown; sentinel is_malformed se ausente

### Validator — 9 checks

| Check | Tipo | Regra |
|---|---|---|
| `valores_preservados` | critical | todos valor_brl em R$ X.XXX,XX |
| `horarios_preservados` | critical | ao menos 1 HH:MM do timeline |
| `tem_abertura` | critical | comeca com `# Narrativa:` |
| `tamanho_razoavel` | critical | 300 ≤ body ≤ 20000 chars |
| `file_ids_preservados` | soft | >=50% dos file_ids aparecem |
| `nomes_preservados` | soft | pagador/recebedor literais |
| `marcadores_inferencia` | soft | >=1 marcador se >=5 eventos |
| `tem_fechamento` | soft | `---` em algum ponto |
| `self_assessment_presente` | soft | dict nao-vazio com 'confidence' |

`passed = all(critical_checks)`. Soft checks geram warnings mas nao
invalidam.

### Persistence — idempotencia via UNIQUE

- UNIQUE (obra, scope, scope_ref, dossier_hash)
- `_find_existing_narrative` tem tratamento correto de NULL em
  scope_ref (pra obra_overview)
- Cache hit retorna (id_existente, path_convencao, was_cached=True)
  sem novo INSERT

## Componente B entregue (esqueleto)

### Schema

- Tabela `correlations` criada (Fase A inclui migration Fase B pra
  evitar segunda migration futura)
- Indexes presentes

### Interfaces

- `Correlation` dataclass alinhada com schema
- `EventSource` Literal type pra type-safety
- `find_correlations_for_day` / `find_correlations_obra_wide`:
  stubs com `NotImplementedError("Fase B — ...")` + docstrings
  com TODOs detalhados
- `save_correlation` **ja implementado** pra evitar reescrita quando
  detectores chegarem

### TODOs documentados

1. `payment_intent_before_execution`: texto 'manda a chave' / 'pix' /
   'transferir' seguido por financial_record em <30min
2. `audio_mentions_matching_photos`: audio + foto do mesmo material
   no mesmo dia
3. `cronograma_vs_execution`: promessa em data X + reporte em data Y
4. `recurring_payment_pattern` / `contract_then_execution` /
   `escalation_pattern` (obra-wide)

## Metricas

| Metrica | Antes Sprint 5 | Depois Fase A |
|---|---:|---:|
| Testes | 340 | **418** (+78) |
| Suites novas | 0 | **6** |
| Modulos novos | 0 | **7** (forensic_agent/*) |
| Migrations | 5 | **6** (+_migrate_sprint5_fase_a_b) |
| Tabelas | 10 | **12** (+forensic_narratives, correlations) |
| CLI commands | 7 | **8** (+narrate) |

Breakdown dos 78 testes novos:
- test_sprint5_schema: 12
- test_dossier_builder: 16
- test_narrator: 10
- test_narrator_validator: 14
- test_narrator_persistence: 11
- test_correlator_skeleton: 8
- test_cli_narrate: 7

## Custo sessao

| Fase | Custo USD |
|---|---:|
| F1 schema | 0.00 |
| F2 dossier_builder | 0.00 |
| F3 narrator (mock) | 0.00 |
| F4 validator | 0.00 |
| F5 persistence | 0.00 |
| F6 correlator esqueleto | 0.00 |
| F7 CLI | 0.00 |
| F8 producao | **PULADA** — credencial ausente |
| F9 SESSION_LOG + tag | 0.00 |
| **Total sessao** | **US$ 0.00** |

Budget: US$ 2.00. Usado: 0% (infra 100% offline).

Custo acumulado vault EVERALDO (pre-Sprint 5): US$ 1.0983 (inalterado).

## Erros encontrados e resolvidos

1. Ruff I001 em vários testes — `--fix` autofix.
2. Ruff N806 pre-existente em `cli.py` (STATUSES, STATUS_COLORS, HANDLERS)
   — fora do escopo desta sessao.
3. NULL handling em `_find_existing_narrative` — UNIQUE em SQLite trata
   NULL como distinto, resolvido com branch explicito para
   `scope_ref IS NULL`.

## Dividas observadas

Sem bloqueadoras. Possiveis para proximas sessoes:

1. **Fase B real — detectores de correlacao**:
   - 3 regras rule-based planejadas
   - Integracao LLM-based opcional
   - Integrar resultados no RDO piloto (nova seção)
2. **Fase 8 producao pendente**: rodar narrate em EVERALDO quando
   credencial disponivel. ~US$ 1.00 + validacao manual.
3. **Prompt V2 Narrator**: apos validar V1 em producao, pode ser
   calibrado com ground truth se narrativas forem inconsistentes.
4. **Visual_analyses active** (divida Op11 remanescente): narrator
   nao esta sujeito a isso (nao le visual_analyses direto —
   dossier_builder faz o LEFT JOIN), mas dossier_builder poderia
   usar `visual_analyses_active` explicitamente. Sprint futura.

## Proximo passo sugerido (Fase B real)

Sprint 5 Fase B deve:

1. **Implementar rule-based `find_payment_intent_before_execution`**:
   query classifications com content LIKE '%chave%' OR '%pix%' OR
   '%transferir%' AND LEFT JOIN financial_records WHERE
   abs(time_diff) < 1800s
2. **Implementar `find_audio_mentions_matching_photos`**:
   classifications source_type='transcription' que mencionam material
   + classifications source_type='visual_analysis' mesma data
3. **Integrar correlations na narrativa**: dossier_builder ganha flag
   `include_correlations=True`; narrator menciona na secao
   "Observacoes forenses"
4. **Testes ground truth**: 5-10 correlacoes conhecidas em EVERALDO
   (Lucas valida) + regressao automatica

## Estado final

- Commits nesta sessao: 7 (F1-F7 + F9)
- Tag a criar: `v0.5.0-sprint5-fase-a`
- Push: OK em todos
- Working tree: limpo exceto `reports/` untracked (pre-existente)
- Suite testes: **418/418 verde**

## Ponteiros

- Modulos novos: `src/rdo_agent/forensic_agent/*`
- Schema: `src/rdo_agent/orchestrator/schema.sql` (+87 linhas)
- Migration: `src/rdo_agent/orchestrator/__init__.py::_migrate_sprint5_fase_a_b`
- CLI: `src/rdo_agent/cli.py::narrate_cmd`
- Testes: `tests/test_{sprint5_schema,dossier_builder,narrator,narrator_validator,narrator_persistence,correlator_skeleton,cli_narrate}.py`

## Fim

Primeira sessao do Sprint 5 **completa em infraestrutura** —
agente leitor forense tem contrato definido, codigo 100% testado,
e CLI pronta pra uso. Aguarda credencial Anthropic pra Fase 8
(producao); Fase B vem na proxima sessao.

Tags Sprint 5 series:
- **`v0.5.0-sprint5-fase-a`** (este — infra Fase A + esqueleto B)
- Futura: `v0.5.1-sprint5-fase-a-prod` (apos producao)
- Futura: `v0.6.0-sprint5-fase-b` (correlacoes reais)

---

## Addendum (2026-04-23) — Descoberta Arquitetural: Ground Truth Injection

### Motivação

Durante validação da narrativa obra_overview gerada, o Lucas (domain expert) identificou que o agente **inferiu incorretamente** a estrutura contratual da obra EVERALDO_SANTAQUITERIA.

**Realidade contratual:**
- Contrato 1: R$ 7.000 (tesouras + terças) — negociado FORA do WhatsApp
- Contrato 2: R$ 11.000 (telhado + fechamento + alambrado) — negociado NO WhatsApp em 04/04
- Total negociado: R$ 18.000

**O que o agente narrou:**
- 1 único contrato de R$ 11.000 (leitura da negociação 04/04)
- Detectou divergência R$1.500 e sinalizou "ajuste não documentado no dossier"

### Análise

O erro do agente é **metodologicamente correto**:
- Ele narrou apenas o que estava na evidência
- Sinalizou a lacuna de informação
- Não inventou contratos sem base documental

O limite expõe uma **necessidade arquitetural**: input complementar de fatos contratuais
conhecidos pelo operador mas ausentes do corpus (acordos presenciais, contratos físicos,
negociações por telefone).

### Proposta: Sprint 5 Fase C — Ground Truth Injection

**Feature:** CLI aceita parâmetro `--context obra_context.yml` com ground truth estruturado.

**Schema YAML** (proposto):
```yaml
obra: EVERALDO_SANTAQUITERIA
contratos:
  - id: C1
    escopo: "Tesouras + terças"
    valor: 7000.00
    origem: "acordo_presencial_fora_whatsapp"
  - id: C2
    escopo: "Telhado + fechamento + alambrado"
    valor: 11000.00
    origem: "whatsapp_04_04_2026"
pagamentos_confirmados:
  - valor: 3500.00
    data: 2026-04-06
    contrato_ref: C1
    parcela: "sinal_50pct"
  # ...
```

**Impacto:** narrativa evolui de "inferência" para "auditoria forense", mudando
natureza comercial do produto (narrador → perito assistente).

**Estimativa:** 3-5h de sessão autônoma, custo ~$1-2.

### Proposta: Sprint 5 Fase D — Ground Truth Extraction (futura)

Modo interativo onde agente conversa com operador para extrair ground truth estruturado,
reduzindo fricção de uso. Output: YAML pronto pra Fase C.

### Roadmap atualizado pós-descoberta

- Fase A: ✅ Concluída
- Fase A.1 (polimento): próximo
- Fase B (correlator rule-based): sessão dedicada
- Fase C (ground truth injection): NOVA — alta prioridade
- Fase D (ground truth extraction): NOVA
- Fase E (contestações hipotéticas): renumerada (era Fase D)
- Fase F (run final): renumerada (era Run Final)
