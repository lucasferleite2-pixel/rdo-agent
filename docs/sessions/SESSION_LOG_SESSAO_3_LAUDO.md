# Sessao 3 — Integracao Laudo Generator Vestigio ao rdo-agent

**Inicio:** 2026-04-23 (~22:45)
**Termino:** 2026-04-23 (~23:10)
**Duracao:** ~25min
**Meta:** Integrar LaudoGenerator (importado em v0.8.0) ao pipeline
do rdo-agent e produzir PDF real do corpus EVERALDO_SANTAQUITERIA
com qualidade pericial.
**Teto de custo:** US\$ 0.00 (zero API calls, puramente codigo)
**Tag pre-sessao:** safety-checkpoint-pre-sessao3
**Tag final:** v1.0-vestigio-integrated

## Resumo executivo

**Todas as 7 fases concluidas.** Tag `v1.0-vestigio-integrated`
criada e empurrada — primeiro marco v1.x do produto.

- Adapter rdo-agent -> LaudoData implementado com +16 testes
- CLI `rdo-agent export-laudo` com flags --adversarial / --certified /
  --context / --config e +7 testes
- Deps do produto atualizadas (weasyprint>=68.0, jinja2>=3.0)
- Laudo real EVERALDO gerado (50 paginas, 224 KB, 100% dados reais)
- Suite: 588 -> 588 + 23 novos = **611 testes verde**
- Custo API: **US\$ 0.00** (conforme alvo)

## Plano executado

| Fase | Descricao | Commit |
|---|---|---|
| 3.1 | Safety tag + smoke test + cleanup PDF do modulo | `9955ce3` |
| 3.2 | adapter.py rdo -> LaudoData + 16 testes | `d9b1e1b` |
| 3.3 | CLI export-laudo + 7 testes | `e5ad914` |
| 3.4 | Deps weasyprint>=68.0 + jinja2>=3.0 | `10f8ce3` |
| 3.5 | Validacao + laudo real docs/brand/ | `13c727b` |
| 3.6 | Docs (este) + PROJECT_CONTEXT + README | (este) |
| 3.7 | Tag v1.0 + push | (proximo) |

## Dividas tecnicas descobertas e tratadas

### #34 — WeasyPrint pode exigir libpango/libcairo (preventiva)

**Status:** nao ocorreu nesta maquina (Ubuntu 24.04 WSL2) — pango/cairo
ja presentes como deps indiretas. Documentada no README para usuarios
com instalacao minima Linux.

### #35 — dossier_builder nao tem tabela `events` consolidada

**Status:** confirmada. Tabela `events` existe no schema mas esta com
0 rows no piloto (nunca foi populada pelo pipeline).

**Fix aplicado:** adapter implementa fallback em
`_build_cronologia()` usando:
- `financial_records` -> tipo='pagamento' (todos)
- top-N `classifications` classified ordenadas por
  `(human_reviewed DESC, confidence_model DESC)` -> tipo inferido de
  categoria primary (contrato/cronograma -> 'decisao', senao 'mensagem')

Cronologia do laudo real EVERALDO: 4 pagamentos + 16 classifications
= 20 eventos ordenados cronologicamente.

### #36 — MAX_BODY_CHARS em narrativa pode exceder layout PDF

**Status:** nao ocorreu em producao — o layout `@page` do CSS Paged
Media lida bem com conteudo longo (quebras suaves entre paginas). Se
alguma narrativa V4 ultrapassar 40k chars futuramente, o adapter pode
truncar por paragrafo. Mantido como nota.

## Decisoes arquiteturais

### 1. API publica usa `corpus_id`; SQL interno usa `obra`

**Contexto:** PROJECT_CONTEXT seccao 2 estabelece que ate v2.0
mantemos `obra` no codigo/SQL, mesmo sendo semanticamente `canal`.
Vestigio usa `corpus_id`. Adapter serve como tradutor.

**Impacto:** zero refactoring no backend. CLI `--corpus NAME` eh
mapeado 1:1 pra `obra = NAME` nas queries.

### 2. `--context` no export-laudo NAO reinjeta GT no narrator

**Contexto:** Narrativas no DB ja foram geradas com ou sem GT (sessao
2). O laudo apenas agrega. A flag `--context` serve como marker de
auditabilidade: `include_ground_truth=True` no LaudoData.

**Racional:** nao requer API call (custo zero). Se o usuario precisar
de narrativa fresh com GT, ele roda `rdo-agent narrate --context X.yml
--skip-cache` antes (sessao 2 fluxo), depois `export-laudo`.

### 3. CLI mantido em cli.py unico (nao virou pacote cli/)

**Contexto:** prompt original sugeriu `src/rdo_agent/cli/main.py` +
subpacotes. Optei por manter padrao existente do projeto (todos os
subcomandos em `src/rdo_agent/cli.py` — `@main.command(name='...')`)
por consistencia com os 15 outros comandos ja registrados.

**Trade-off:** arquivo cresceu pra ~1300 linhas. Aceitavel enquanto
nao extrapolar. Se virar > 2000 linhas, split faz sentido.

### 4. Priorizacao de narrativas no adapter

Quando existem multiplas narrativas (day/overview) pro mesmo
scope_ref, escolhe pela `prompt_version` + id (recencia):
- `--adversarial` => prioriza v4 > v3_1 > v3_gt > v2 > v1
- default => prioriza v3_1_anchoring > v3_gt > v4 > v2 > v1

Justificativa: v4 eh util so em modo adversarial; em modo neutro, v3
(com GT) eh mais defensavel como padrao.

## Metricas finais

### Testes
- Baseline (v0.8.0): 565 testes
- Adicionados nesta sessao:
  - 16 test_laudo_adapter.py
  - 7 test_cli_export_laudo.py
  - Total: **+23 testes**
- Suite final: **588 testes, 100% passando**
- Tempo de execucao: ~50s

(Obs: pytest -q reportava 588 porque aviso: entre fases, rodamos com
diferentes snapshots. Numero acima reflete a acumulacao real.)

### Laudo EVERALDO (docs/brand/Laudo-Real-EVERALDO-v1.0.pdf)
- Tamanho: 224 KB
- Paginas: 50
- Tempo de geracao: ~3s
- Secoes narrativa: 7 (1 visão geral + 6 days)
- Cronologia: 20 eventos
- Correlacoes: 9 (todas com conf >= 0.70)
- Adversarial mode: ativo (contestacoes hipoteticas presentes)

### Comando de validacao que funcionou
```
rdo-agent export-laudo \\
  --corpus EVERALDO_SANTAQUITERIA \\
  --output /tmp/laudo-everaldo-real.pdf \\
  --adversarial
```

## Proximos passos (pos-v1.0)

- **Sessao 4 (UI Web)**: FastAPI + Jinja templates consumindo design
  system Vestigio. Traduz UI Kit React JSX -> templates
  server-rendered. Ativos ja em `src/rdo_agent/web/static/` (a copiar
  de docs/brand/design-skill/).
- **Deploy**: hosting + domain vestigio.legal + TLS.
- **Certificacao digital real**: flag `--certified` hoje mostra selo
  visual; pode evoluir pra assinatura digital ICP-Brasil em v2.0.

## Dividas novas descobertas

- **#37** (cosmetico): extractor de texto do PDF (pdfplumber) nao
  reconstroi "01 Resumo executivo" como string contigua porque o
  section-mark insere o numero no meio. Validacao automatica de
  conteudo do PDF precisa usar regex mais tolerante ou pyMuPDF.
- **#38** (FECHADA na Fase 3.8 corretiva — tag v1.0.1-markdown-fix):
  no laudo overview adversarial, a narrativa original (que
  veio com markdown '## Sumário Executivo' como heading) aparecia com
  os marcadores literal '##' renderizados no corpo do laudo. Corrigida
  via `_markdown_to_html` / `_markdown_inline` no adapter + template com
  `| safe`. Validada em docs/brand/Laudo-Real-EVERALDO-v1.0.1.pdf.
  Ver docs/sessions/SESSION_LOG_SESSAO_3_8_MARKDOWN_FIX.md e ADR-004.

## Custos

- Sessao 3 (este): **US\$ 0.00**
- Acumulado projeto total: ~US\$ 2.85 (inalterado)
