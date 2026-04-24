# Vestígio — Plano de Integração com rdo-agent

**Data:** 23/04/2026  
**Status:** ✅ Identidade visual completa + Laudo Generator importado  
**Próximo:** Sessão 3 → implementar adapter + CLI + regenerar laudo EVERALDO

## Decisões travadas

| Elemento | Valor |
|---|---|
| Nome do produto | **Vestígio** |
| Domínio | vestigio.legal |
| Razão social | Vestígio Tecnologia Ltda (em abertura) |
| Controladora | HCF Investimentos e Participações (100%) |
| Wordmark principal | V06 (acento í bordô) |
| Monograma principal | M03 (V + selo bordô) |
| Tagline institucional | "Perícia forense de comunicações digitais" |
| Tagline evocativa | "Do vestígio ao laudo" |

## Paleta (sem azul)

| Token | Hex | Uso |
|---|---|---|
| `--vst-bordo` | #6B0F1A | Primária institucional, CTAs, selos |
| `--vst-ink` | #1A1A1A | Texto principal |
| `--vst-graphite` | #4A4A4A | Texto secundário, links (com underline) |
| `--vst-paper` | #F5F1EA | Canvas, fundo padrão |
| `--vst-gold` | #8B6F47 | Selos, marcas d'água APENAS |

## Tipografia

- **EB Garamond** — títulos obrigatórios (H1-H4, display, lead)
- **Inter** — corpo, UI, labels
- **JetBrains Mono** — evidência forense (hashes, IDs, code)

## Arquitetura do repo
rdo-agent/  (nome técnico: repo, CLI, package Python)
│
└── produto comercial: Vestígio
│
├── docs/brand/                     ← REFERÊNCIA INSTITUCIONAL
│   ├── Vestigio-Brandbook-v1.0.pdf
│   ├── Laudo-Exemplo-Santa-Quiteria.pdf
│   ├── design-system.html
│   ├── showcase.html
│   ├── design-skill/               ← skill agentic + UI kit React
│   ├── wordmarks/ (V01-V06 SVG)
│   ├── monograms/ (M01, M03 SVG)
│   ├── lockups/ (L01-L03 SVG)
│   ├── palette/ social/ favicons/
│   └── docs/ (Letterhead DOCX, Deck PPTX)
│
├── src/rdo_agent/laudo/            ← ✅ LAUDO GENERATOR IMPORTADO
│   ├── init.py
│   ├── vestigio_laudo.py           (LaudoGenerator + dataclasses)
│   ├── gen_laudo_example.py        (exemplo funcional)
│   ├── README.md                   (guia integração)
│   ├── templates/laudo.html
│   ├── static/laudo.css
│   └── fonts/ (4 VF compactas)
│
└── src/rdo_agent/web/static/       ← USO PRODUTIVO (Sessão 4)
├── css/design-tokens.css
├── fonts/ (6 VF completas)
├── logos/ (SVGs)
└── favicons/ (16/32/48/180/192/512 + .ico)

## Status dos ativos

### ✅ TODOS no repo agora
- Brandbook PDF 34 páginas
- Design skill completo (SKILL.md + tokens + UI kit JSX)
- Logos PNG + SVG vetoriais (V01-V06, M01/M03, L01-L03)
- 3 famílias de fonte self-hosted
- Social assets (avatar, LinkedIn banner, OG image)
- Ativos operacionais (Letterhead DOCX, Deck PPTX)
- Favicons completos
- **Laudo Generator Python + templates + static + fontes**
- Laudo exemplo de referência

## Próximos passos

### Sessão 3 (pronta pra disparar)
1. Criar `src/rdo_agent/laudo/adapter.py`:
   - Função `rdo_to_vestigio_data(corpus_id, state) → LaudoData`
   - Mapeia estado interno (narrativas, correlações, cronologia) → dataclasses do Vestígio
2. Adicionar dependências ao `pyproject.toml`:
weasyprint>=68.0
jinja2>=3.0
3. Criar CLI: `rdo-agent export-laudo --corpus X --output Y.pdf [--certified]`
4. Regenerar laudo EVERALDO com fidelidade ao exemplo entregue
5. Testes de integração
6. Tag: `v1.0-vestigio-integrated`

Estimativa: 2-3h autônoma, $0 (zero API calls).

### Sessão 4 (UI Web)
FastAPI + Jinja templates consumindo design system.
Traduz UI Kit React JSX → templates server-rendered.
Tokens, fontes e logos já em `src/rdo_agent/web/static/`.

## Princípios não-negociáveis (do SKILL.md)

1. Sem azul em lugar algum
2. Serifa obrigatória em títulos (EB Garamond)
3. Sem emojis em qualquer superfície
4. Sem gradientes, glassmorphism, ilustrações chapadas
5. Densidade alta (rejeitar heros vazios)
6. Print-safe em preto e branco
7. Tabelas editoriais, não dashboard
8. Bordô com parcimônia (autoridade, não decoração)
9. Tom: frases curtas, vocabulário técnico forense, zero hype
10. Mínimo 15-16px no corpo, line-height 1.6-1.7
