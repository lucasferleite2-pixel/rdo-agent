# Vestígio — Plano de Integração com rdo-agent

**Data:** 23/04/2026  
**Status:** Design system + identidade visual entregues. Laudo Generator Python PENDENTE.

## Decisões travadas (do brandbook)

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
| Paleta | Bordô #6B0F1A + Preto #1A1A1A + Paper #F5F1EA + Grafite #4A4A4A + Dourado #8B6F47 |
| Tipografia | EB Garamond (títulos) + Inter (corpo) + JetBrains Mono (código) |

## Arquitetura de integração
rdo-agent/  (nome técnico: repo, CLI, package Python)
│
└── produto comercial: Vestígio
├── src/rdo_agent/laudo/     ← Laudo Generator (aguardando upload)
├── src/rdo_agent/web/       ← UI Web (Sessão 4, futura)
└── docs/brand/              ← Ativos de marca (este diretório)

## O que está no repo agora

### docs/brand/
- `Vestigio-Brandbook-v1.0.pdf` — manual de marca 34 páginas
- `design-system.html` — tokens CSS e tipografia
- `showcase.html` — comparação visual dos ativos
- `Laudo-Exemplo-Santa-Quiteria.pdf` — referência de qualidade alvo

### docs/brand/docs/
- `Vestigio-Letterhead.docx` — papel timbrado A4 institucional
- `Vestigio-Deck-Template.pptx` — template 8 slides 16:9

### docs/brand/wordmarks/
- V06 (wordmark principal com í bordô)

### docs/brand/monograms/
- M01 (institucional formal, baseline rule)
- M03 (principal, evidence mark)

### docs/brand/lockups/
- L01 (institucional com tagline descritiva)
- L02 (evocativo com "Do vestígio ao laudo")
- L03 (horizontal com monograma)

### docs/brand/palette/
- palette-swatch.png — especificação cromática visual

### docs/brand/social/
- avatar-square-400x400.png
- linkedin-banner-1584x396.png
- og-image-1200x630.png

## Ativos PENDENTES (precisam vir do Claude Designer)

### Críticos para Sessão 3
- `06-laudo-generator/` — pacote Python completo do gerador de laudo
  - `vestigio_laudo.py` (módulo principal com LaudoGenerator + dataclasses)
  - `adapter.py` (rdo_to_vestigio_data)
  - `templates/laudo.html` (template Jinja2)
  - `static/laudo.css` (CSS Vestígio)
  - `fonts/` (EB Garamond, Inter, JetBrains Mono — embarcadas)
  - `gen_laudo_example.py` (exemplo executável)

### Úteis para Sessão 4 (UI Web)
- SVGs vetoriais de wordmarks, monogramas e lockups (atualmente só temos PNG)
- Favicon set completo (16/32/48/180/192/512 + .ico)
- Variações adicionais de wordmarks (V01, V02, V04, V05) para fallbacks

## Roadmap de integração

### Sessão 3 — Integração Laudo Generator (aguardando uploads)
1. Copiar `06-laudo-generator/` completo para `src/rdo_agent/laudo/`
2. Criar `src/rdo_agent/laudo/adapter.py` conectando estado rdo-agent → LaudoData
3. Adicionar dependências: `weasyprint>=68.0`, `jinja2>=3.0`
4. CLI: `rdo-agent export-laudo --corpus X --output Y.pdf [--certified]`
5. Regenerar laudo EVERALDO com fidelidade ao exemplo entregue
6. Tag: `v1.0-vestigio-integrated`

### Sessão 4 — UI Web (posterior)
1. FastAPI + Jinja templates consumindo design system
2. Reutilizar paleta, tipografia e componentes do brandbook
3. Telas: CaseList, CaseDetail, LaudoView, GTEditor

## Referências visuais

- Brandbook completo: `docs/brand/Vestigio-Brandbook-v1.0.pdf`
- Design tokens CSS: `docs/brand/design-system.html`
- Showcase visual: `docs/brand/showcase.html`
- Qualidade alvo do laudo: `docs/brand/Laudo-Exemplo-Santa-Quiteria.pdf`
