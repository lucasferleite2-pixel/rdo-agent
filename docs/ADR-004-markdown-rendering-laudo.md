# ADR-004 — Renderização de markdown no laudo PDF

**Status:** Aceito
**Data da decisão:** 2026-04-24 (Fase 3.8)
**Decisores:** Lucas (proprietário), Claude Code (executor)
**Escopo:** `src/rdo_agent/laudo/` — pipeline de geração do laudo
**Relacionados:** Sessão 3 (v1.0-vestigio-integrated), dívida técnica #38

---

## Contexto

As narrativas forenses produzidas pela Sprint 4/5 (V3_gt, V3.1_anchoring,
V4_adversarial) são geradas em **markdown** — usam `## Título`, `**bold**`,
`*italic*` e parágrafos separados por `\n\n`. Essa escolha foi intencional:
o markdown é fonte canônica, legível por humano e reprocessável.

Na Sessão 3 integramos o `LaudoGenerator` do Vestígio e produzimos o laudo
real EVERALDO (`docs/brand/Laudo-Real-EVERALDO-v1.0.pdf`). O laudo ficou
visualmente consistente com o brandbook Vestígio **exceto** nas seções
narrativas: o template renderiza `secao.conteudo` como texto plain,
fazendo com que `##`, `**`, `*` apareçam **literalmente** no PDF.

Exemplo observável no overview adversarial do laudo v1.0:

```
## Análise temporal
**Os eventos...**
```

Isso viola o posicionamento de marca explicitado no brandbook Vestígio
("ferramenta pericial profissional, não produto de IA") — dívida #38
registrada em `SESSION_LOG_SESSAO_3_LAUDO.md`.

## Decisão

Converter markdown → HTML **no adapter** (`adapter.py`), antes de entregar
o `LaudoData` ao `LaudoGenerator`. Usa a biblioteca `markdown>=3.5`
(pure Python, zero custo de runtime).

### Alternativas consideradas

**Estratégia A (escolhida) — conversão no adapter**

- Função `_markdown_to_html(text)` aplicada em `SecaoNarrativa.conteudo`
- Função `_markdown_inline(text)` aplicada em `resumo_executivo` (sem
  wrapper `<p>` porque já é envolvido por `<p class="lead">` no template)
- Template muda de `{{ paragrafo }}` + split → `{{ secao.conteudo | safe }}`
- Narrativas no DB permanecem em markdown (fonte canônica inalterada)

**Estratégia B — filter Jinja custom no template**

- Mais limpo semanticamente (conteudo continua markdown no adapter)
- Requer registrar filter no `Environment` do Vestígio `LaudoGenerator`
- Superfície de teste maior: precisa mockar template + filter
- Acopla o adapter à infra de template (vazamento de abstração)

### Rationale da escolha

Estratégia A foi escolhida por:

1. **Menor superfície de teste** — o adapter já tem suite dedicada;
   adicionar testes de conversão markdown é incremental.
2. **Isolamento** — a dependência de `markdown` fica contida no adapter,
   sem tocar no `vestigio_laudo.py` (mantido como recebido do repo de
   origem do LaudoGenerator).
3. **Reprodutibilidade** — `_markdown_to_html` é pura: input texto →
   output HTML, fácil de auditar e versionar.

## Consequências

### Positivas

- Laudo respeita posicionamento Vestígio (sem markdown literal)
- Narrativa v4 adversarial fica legível com hierarquia tipográfica correta
- Dep `markdown` é leve (~200kB, pure Python, sem binding nativo)

### Negativas / Riscos

- Rebaixamento de headings (h1/h2 → h3) é feito por pós-processamento de
  string (`.replace()`). Se o markdown produzir `<h2 id="...">`, o replace
  precisa ser tolerante ao prefixo. Resolvido usando `<h2` (sem `>`) como
  agulha de substituição.
- HTML inline no input markdown é permitido por default. Mitigado com
  `html.escape(text, quote=False)` antes de `convert()` — defense-in-depth
  contra XSS, mesmo que narrativas sejam geradas internamente.
- Elementos markdown não mapeados em CSS Vestígio (tabelas longas, code
  blocks) podem sair visualmente inconsistentes. Documentado como
  dívida #39 (tratar em v1.2 se/quando aparecer em produção).

## Validação

Critérios de aceitação da Fase 3.8.4:

- [ ] Nenhum `##`, `**`, `*` literal no PDF regenerado
- [ ] Títulos de sub-seção (h3) em EB Garamond medium
- [ ] Negrito com peso 600-700, itálico com EB Garamond italic
- [ ] Layout não quebrou (paginação OK)
- [ ] Comparação visual antes/depois registrada no session log
