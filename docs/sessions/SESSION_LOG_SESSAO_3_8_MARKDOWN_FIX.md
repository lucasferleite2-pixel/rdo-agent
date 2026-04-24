# Sessao 3.8 — Fix markdown literal no laudo PDF (dívida #38)

**Inicio:** 2026-04-24
**Termino:** 2026-04-24
**Duracao:** ~1h
**Meta:** Resolver dívida #38 — markdown literal (`##`, `**`, `*`)
aparecendo no corpo do laudo Vestígio, violando o posicionamento
de marca ("ferramenta pericial profissional, não produto de IA").
**Teto de custo:** US$ 0.00 (zero API calls)
**Tag pre-sessao:** safety-checkpoint-pre-sessao3-8
**Tag final:** v1.0.1-markdown-fix
**ADR:** docs/ADR-004-markdown-rendering-laudo.md

## Resumo executivo

Dívida #38 encerrada. Laudo EVERALDO regenerado sem marcadores markdown
literais. Hierarquia editorial Vestígio agora funciona conforme brandbook
(h3 EB Garamond, h4 Inter uppercase eyebrow, blockquotes preservados).

- 10 testes novos em `test_laudo_adapter.py` (baseline 23 → 33)
- Suite total: 588 → 598 passando
- Custo API: US$ 0.00

## Estratégia escolhida (ADR-004)

**Estratégia A** — conversão markdown → HTML **no adapter**, antes de
entregar `LaudoData` ao `LaudoGenerator`. Alternativa B (filter Jinja
custom) rejeitada por acoplar adapter a infra de template.

Duas funções adicionadas em `adapter.py`:

- `_markdown_to_html(text)` — converte markdown em HTML com rebaixamento
  de headings (h1/h2 → h3, h3 → h4, etc). Usado em `secoes_narrativa[].conteudo`.
- `_markdown_inline(text)` — idem, mas remove wrapper `<p>` único. Usado
  em `resumo_executivo` porque o template já envolve em `<p class="lead">`.

**Defense-in-depth contra XSS:** input é pré-escapado com
`html.escape(text, quote=False)` antes de `markdown.convert()`. Narrativas
são geradas internamente (não há input de usuário), mas o escape custa
nada e fecha a porta contra raw HTML no input.

## Plano executado

| Fase | Descrição | Commit |
|---|---|---|
| 3.8.1 | Safety tag + ADR-004 | `4151062` |
| 3.8.2/3 | markdown>=3.5 + funções + template + 10 testes | `14fbc11` |
| 3.8.4 | Laudo real regenerado sem markdown literal | `d2e4388` |
| 3.8.5 | Docs (este + PROJECT_CONTEXT + session log sessao 3) | (este) |
| 3.8.6 | Tag v1.0.1-markdown-fix | (próximo) |

## Mudanças no código

### `src/rdo_agent/laudo/adapter.py`

- Imports: `html`, `markdown`
- Módulo: `_MD` (instância reutilizável), `_HEADING_DOWNSHIFT` (tabela de
  rebaixamento)
- Funções: `_markdown_to_html`, `_markdown_inline`
- `_extract_narratives` agora aplica conversão em `conteudo` das seções
  e no `resumo_exec`

### `src/rdo_agent/laudo/templates/laudo.html`

- `<p class="lead">{{ laudo.resumo_executivo }}</p>` → `{{ ... | safe }}`
- `{% for paragrafo in secao.conteudo.split('\n\n') %}<p>{{ paragrafo }}</p>{% endfor %}`
  → `<div class="secao-body">{{ secao.conteudo | safe }}</div>`

### `pyproject.toml`

- Nova dep: `markdown>=3.5`

## Testes adicionados

10 testes novos em `tests/test_laudo_adapter.py`:

1. `test_markdown_h2_becomes_h3`
2. `test_markdown_bold_becomes_strong`
3. `test_markdown_italic_becomes_em`
4. `test_markdown_paragraph_separation`
5. `test_markdown_empty_string_returns_empty`
6. `test_markdown_xss_protection` (injeção `<script>`)
7. `test_markdown_inline_strips_single_p_wrapper`
8. `test_markdown_inline_preserves_multi_block`
9. `test_markdown_preserves_lists_and_blockquotes`
10. `test_adapter_narratives_have_html_not_markdown` (integração)

## Antes / depois visual

Validação via `pdfplumber.extract_text()` sobre o laudo real EVERALDO:

| Marcador literal | v1.0 (BUG) | v1.0.1 (FIX) |
|---|---:|---:|
| `## ` | 72 | **0** |
| `**` | 510 | **0** |
| `*palavra` isolado | 5 | **0** |

Hierarquia editorial renderizada:

- **h2 (section-mark)**: "Resumo executivo", "Visão geral do canal", "Cronologia"
- **h3 (EB Garamond 17pt)**: "Sumário Executivo", "Cronologia em Bloco",
  "Ledger Financeiro Consolidado", "Padrões Observados"
- **h4 (Inter uppercase 8.5pt, eyebrow)**: "BLOCO 1 — PROSPECÇÃO...",
  "BLOCO 2 — FECHAMENTO DO C1...", etc.
- **Blockquotes** (`> Observação forense:`): preservados

Paginação: 50 → 52 páginas (+4%), aumento esperado devido aos margin-top
dos headings estilizados.

**Arquivos:**
- Antes (bug): `docs/brand/Laudo-Real-EVERALDO-v1.0.pdf` (223.9 KB)
- Depois (fix): `docs/brand/Laudo-Real-EVERALDO-v1.0.1.pdf` (285.4 KB)

## Dívidas derivadas (documentadas, não implementadas)

- **#39**: Elementos markdown não mapeados em CSS Vestígio (tabelas longas,
  code blocks) podem sair visualmente inconsistentes. Tratar em v1.2 se/
  quando aparecer em produção.
- **#40**: Narrativa V4 pode usar emoji ocasional (proibido pelo brandbook).
  Não detectado em produção; considerar warning/strip em v1.2.

## Custos

- Sessão 3.8 (este): **US$ 0.00**
- Acumulado projeto total: inalterado
