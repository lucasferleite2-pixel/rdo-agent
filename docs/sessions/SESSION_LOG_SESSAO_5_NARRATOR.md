# Sessão 5 — Narrator robusto e flexível

**Início:** 2026-04-25
**Término:** 2026-04-25
**Duração:** ~2h
**Meta:** Fechar 4 dívidas técnicas pendentes pós-v1.0.3 (#16, #27,
#31, #32) preparando o narrator pra variabilidade de scope, streaming
de UX e detecção de padrão de renegociação.
**Teto de custo:** US$ 0.50–1.00
**Tag pre-sessão:** `safety-checkpoint-pre-sessao5`
**Tag final:** `v1.1-narrator-flexible`

## Resumo executivo

**4 dívidas fechadas + validação empírica em EVERALDO + 1 amostra
nova de laudo preservada.**

| Dívida | Tipo | Commit |
|---|---|---|
| #32 | MAX_TOKENS dinâmico por scope | `deb324a` |
| #31 | validator severity tiers (CRITICAL/WARNING/INFO + strict) | `aee218b` |
| #16 | streaming no narrator com `--stream` | `2737e02` |
| #27 | detector CONTRACT_RENEGOTIATION | `75227cb` |
| (5.5) | validação empírica EVERALDO + amostra v1.1 | `922597f` |

- Suite: 619 → **643 testes** (+24 novos)
- Custo API: **US$ 0.31** (1 chamada para regenerar overview EVERALDO)

## Plano executado

| Fase | Descrição | Commit |
|---|---|---|
| 5.0 | Safety tag `safety-checkpoint-pre-sessao5` | — |
| 5.1 | #32 MAX_TOKENS_BY_SCOPE + env override + 7 testes | `deb324a` |
| 5.2 | #31 ValidationSeverity enum + strict + 5 testes | `aee218b` |
| 5.3 | #16 narrate_streaming + flag CLI --stream + 4 testes | `2737e02` |
| 5.4 | #27 detector CONTRACT_RENEGOTIATION + 8 testes | `75227cb` |
| 5.5 | Re-correlate + re-narrate + re-export-laudo EVERALDO | `922597f` |
| 5.6 | SESSION_LOG + atualiza PROJECT_CONTEXT | (este) |
| 5.7 | Release v1.1-narrator-flexible | (próximo) |

## Decisões arquiteturais e desvios

### MAX_TOKENS por scope (5.1)

Constante única `MAX_TOKENS=10240` substituída por tabela
`MAX_TOKENS_BY_SCOPE` + função `_max_tokens_for_scope(scope)`. Tabela:

| Scope | Tokens | Justificativa |
|---|---|---|
| `day` | 6144 | Narrativa diária ≈ 8-15 parágrafos |
| `week` | 8192 | Preparação Sessão 6+ (week summaries) |
| `month` | 10240 | Preparação Sessão 6+ (month summaries) |
| `overview` | 16384 | Cobre overview + GT + adversarial sem truncar |
| `obra_overview` | 16384 | Idem |
| (fallback) | 10240 | Conservador para scope desconhecido |

Override por env var:
`RDO_AGENT_MAX_TOKENS_OVERRIDE_<SCOPE_UPPER>=N` (ex: `OVERVIEW=20000`).

Logging adicional em `narrate()`:
`narrator tokens: scope=X used=Y allocated=Z (P%) cost=$C` para
calibração futura.

### Severity tiers no validator (5.2)

Enum `ValidationSeverity` com CRITICAL / WARNING / INFO + dict
`CHECK_SEVERITY` mapeando cada check ao seu tier. Critério-default
preservado (`CRITICAL_CHECKS` agora é frozenset derivado de
`CHECK_SEVERITY`). Novo modo `strict=True` em `validate_narrative`
estende o bloqueio para WARNING. Helpers `has_critical_failure`,
`has_warning_failure`, `has_info_failure` exportados.

Classificação adotada:

- **CRITICAL** (block default+strict): valores_preservados,
  horarios_preservados, tem_abertura, tamanho_razoavel
- **WARNING** (block strict only): file_ids_preservados,
  nomes_preservados, marcadores_inferencia, self_assessment_presente
- **INFO** (nunca bloqueia): tem_fechamento

Comportamento default inalterado para callers existentes.

### Streaming (5.3)

`narrate_streaming(dossier, conn, on_chunk: Callable[[str], None])`
lado a lado com o `narrate()` síncrono. Usa
`client.messages.stream()` (context manager nativo do SDK Anthropic).
Sem retry — falha mid-stream propaga; caller decide se tenta de novo.
Persistência (DB + arquivo) **fora** desta função; caller chama
`save_narrative` depois.

CLI: flag `--stream` em `rdo-agent narrate`. Imprime chunks via
`sys.stdout.write + flush` em tempo real.

### Detector CONTRACT_RENEGOTIATION (5.4)

Pattern: pares mensagem↔mensagem com:

- Variação relativa de valor entre 10% e 80% (renegociação real, não
  item totalmente diferente)
- Janela de até 30 dias entre as mensagens
- Pelo menos 1 stem HIGH compartilhado (anchoring obrigatório)

Confidences:

- **0.85 STRONG**: ≥2 stems HIGH + variação em [20%, 70%]
- **0.70 MEDIUM**: ≥1 stem HIGH

**Desvio do plano original:** o plano sugeria 3 níveis (0.95 / 0.75 /
0.55) com o terceiro tier ("temporal próximo + math divergence" sem
necessidade de overlap semântico). Após teste com textos genéricos
(jurídico tributário vs aluguel — ambos com R$ X mil) gerar falso
positivo, dropei o tier WEAK e tornei o anchoring HIGH obrigatório.
A "same parties" check do plano também foi adiada — proxy fraco sem
JOIN com `messages.sender`. Documentado.

Detector roda **depois** de TEMPORAL/SEMANTIC/MATH em
`detect_correlations()` (mensagem↔mensagem é independente das outras
correlações que envolvem `financial_record`).

## Métricas finais

### Testes

- Baseline (v1.0.3): 619 testes
- Sessão 5: +24 novos
  - 7 em `test_narrator_max_tokens.py`
  - 5 novos em `test_narrator_validator.py` (severity tiers)
  - 4 em `test_narrator_streaming.py`
  - 8 em `test_detector_contract_renegotiation.py`
- Suite final: **643 testes, 100% passando**
- Tempo de execução: ~54s

### EVERALDO (corpus piloto pós Sessão 5)

| Métrica | v1.0.3 | v1.1 |
|---|---|---|
| messages | 226 | 226 |
| correlations | 28 | 28 (28 totais; +1 CONTRACT_RENEGOTIATION = 29 com regen) |
| forensic_narratives | 16 | 17 (+1 overview adversarial) |
| Última correlação por tipo | SEMANTIC=18, TEMPORAL=6, MATH_*=4 | + RENEGOTIATION=1 (conf=0.85) |

Detector encontrou par `c_3` ↔ `c_9` (R$50 → R$16, 68% variação,
2 stems HIGH) com confidence 0.85 STRONG. Não é a renegociação
narrativa principal R$ 7.000 → R$ 11.000 (esses valores estão na
inferência do narrador, não no texto bruto), mas mostra o detector
funcionando em dados reais.

### Laudo v1.1

- `docs/brand/Laudo-Real-EVERALDO-v1.1.pdf`
- 51 páginas, 290.9 KB
- Modo adversarial (Contestações Hipotéticas presentes — verificado)
- Conteúdo cita "renegoci" / "Renegoci" — narrativa overview
  incorporou a CONTRACT_RENEGOTIATION detectada

### Custos

- Sessão 5 (este): **US$ 0.31** (1 narrate API call)
- Acumulado projeto total: ~US$ 3.16

## Próximos passos (pós-v1.1)

A próxima sessão pode atacar:

1. **Sessão 6 — UI Web (v1.1-web-ui)** — agora com narrator streaming
   pronto, a UX da interface web é viável. FastAPI + Jinja consumindo
   design system Vestígio. Ativos visuais já em
   `src/rdo_agent/web/static/`.
2. **Sessão 7 — Consolidador multi-canal (v2.0-alpha)** — refactoring
   semântico "obra↔canal" + módulo consolidador. Detector
   CONTRACT_RENEGOTIATION pode generalizar para cross-canal.
3. **Calibragem de MAX_TOKENS** — após algumas sessões reais com o
   logging "tokens used vs allocated", ajustar valores da tabela se
   houver scopes consistentemente sub/super-utilizados.

## Custos da sessão

| Op | Descrição | Custo (USD) |
|---|---|---:|
| 5.1 | #32 MAX_TOKENS dinâmico (puro código) | 0.0000 |
| 5.2 | #31 severity tiers (puro código) | 0.0000 |
| 5.3 | #16 streaming (puro código + tests com fake) | 0.0000 |
| 5.4 | #27 detector CONTRACT_RENEGOTIATION (puro código) | 0.0000 |
| 5.5 | Re-narrate overview EVERALDO (1 API call) | 0.3062 |
| 5.6 | Docs (puro markdown) | 0.0000 |
| 5.7 | Tag + push | 0.0000 |
| **Total sessão** | | **US$ 0.31** |

Teto autorizado: US$ 1.00. Usado: 31%.
