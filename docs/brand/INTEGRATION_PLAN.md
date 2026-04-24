# Vestígio — Plano de Integração com rdo-agent

**Data:** 23/04/2026
**Status:** Design system completo + Laudo Generator entregues

## Decisões travadas (do brandbook)

- Nome do produto: **Vestígio**
- Domínio: vestigio.legal
- Razão social: Vestígio Tecnologia Ltda (em abertura)
- Controladora: HCF Investimentos e Participações (100%)
- Wordmark principal: V06 (acento í bordô)
- Monograma: M03 (V + selo bordô)

## Arquitetura de integração
rdo-agent (nome técnico, repo, CLI)
│
└─ produto comercial: Vestígio
├─ src/rdo_agent/laudo/     ← Laudo Generator (este plano)
├─ src/rdo_agent/web/       ← UI Web (Sessão 4)
└─ docs/brand/              ← Ativos de marca

## Sessões de integração

### Sessão 3 (próxima) — Integração do Laudo Generator
Copia 06-laudo-generator → src/rdo_agent/laudo/
Implementa adapter.py (state rdo-agent → LaudoData)
CLI: rdo-agent export-laudo --corpus X --output Y.pdf
Validação: regerar laudo EVERALDO com fidelidade ao exemplo

### Sessão 4 — UI Web
FastAPI + Jinja consumindo design system
Telas: CaseList, CaseDetail, LaudoView, GTEditor

## Ativos de marca (docs/brand/)

- Vestigio-Brandbook-v1_0.pdf (34 páginas)
- Vestigio_Design_System.html (tokens HTML/CSS)
- showcase.html (comparação visual)
- Laudo-Exemplo-Santa-Quiteria.pdf (referência de qualidade)
- lockups/ (L02, L03)
- social/ (OG image, LinkedIn, avatar)
- docs/ (Letterhead, Deck PPTX)

## Referências

- Brandbook: docs/brand/Vestigio-Brandbook-v1_0.pdf
- Design tokens: docs/brand/Vestigio_Design_System.html
- Laudo Generator: src/rdo_agent/laudo/README.md
