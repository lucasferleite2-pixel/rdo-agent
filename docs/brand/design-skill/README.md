# Vestígio — Design System

> Perícia forense de comunicações digitais.
> Transforma registros brutos em laudos cronológicos auditáveis com rastreabilidade forense.

Produto SaaS institucional, operado sob a holding **HCF Investimentos e Participações**. Não é uma startup — a estética transmite **autoridade pericial**, não entusiasmo tech.

---

## Contexto do produto

**Função.** Ingestão de registros brutos (exportações de WhatsApp, e-mails, áudios, documentos) → extração, normalização e datação → geração de laudos cronológicos auditáveis com cadeia de custódia e hash SHA-256 por vestígio.

**Público-alvo.**
- Advogados contenciosos sêniores
- Peritos judiciais
- Gestores de obra (documentação de contencioso em obra pública)
- Operadores de M&A distressed (due diligence documental)

**Casos de uso primários.** Contencioso cível e arbitral, perícia judicial, due diligence de M&A, gestão documental de obras públicas.

**Território visual.** Institucional forense contemporâneo.
Referências: *Stripe Press*, *The Economist*, escritórios *Cleary Gottlieb* e *Latham & Watkins*, *Hermès* (paleta).
Anti-referências: Jusbrasil, Thomson Reuters, Stripe principal, Linear, qualquer SaaS commodity com gradientes e ilustrações chapadas.

**Critério de sucesso.** Um advogado de 55 anos olha a tela e pensa *"isso é uma ferramenta pericial profissional"*, não *"isso é um produto de IA"*.

---

## Fontes disponíveis

| Família | Status | Arquivos |
|---|---|---|
| EB Garamond | **Local** (variável + estáticas 400–800) | `fonts/EBGaramond-VariableFont_wght.ttf` + itálico variável + 10 cortes estáticos |
| JetBrains Mono | **Local** (variável + estáticas 100–800) | `fonts/JetBrainsMono-VariableFont_wght.ttf` + itálico variável + 16 cortes estáticos |
| Inter | **Local** (variável opsz + wght) | `fonts/Inter-VariableFont_opsz,wght.ttf` + itálico variável |

Hospedagem tipográfica 100% local. Sistema funciona offline; laudos imprimem com fidelidade garantida.

---

## Conteúdo — fundamentos

Copy é instrumento probatório. Não é marketing.

**Tom.** Autoridade calma. Terminologia técnica forense. Densidade alta. Zero hype.

**Vocabulário canônico.**
`laudo`, `corpus`, `vestígio`, `cronologia`, `evidência`, `perícia`, `cadeia de custódia`, `rastreabilidade`, `hash`, `carimbo de tempo`, `ingestão`, `normalização`, `anexo`, `fonte`, `recorte`, `contraditório`.

**Pessoa.** Terceira pessoa institucional. "A plataforma", "o sistema", "o perito". Evitar "você" no copy de produto (aceitável em UI direta: *"Selecione o corpus"*). Nunca "nós" em marketing.

**Frases.** Curtas e densas. Ponto final. Sem reticências retóricas, sem exclamação, sem perguntas retóricas. Voz ativa quando há ator definido; passiva aceita em descrição de processo.

**Casing.**
- Títulos: Sentence case com maiúscula inicial. *"Cronologia auditável por vestígio"* — não *"Cronologia Auditável Por Vestígio"*.
- Labels de UI: Sentence case. *"Importar corpus"*, *"Gerar laudo"*.
- Eyebrows / metadata: UPPERCASE tracked (`letter-spacing: 0.14em`). *"MÓDULO 03 · PERÍCIA"*.

**Números.** Sempre tabulares (`font-variant-numeric: tabular-nums`). Datas em ISO quando forense: `2025-03-14T09:42:17-03:00`. Datas em corpo editorial: `14 de março de 2025`. Hashes sempre em `JetBrains Mono` truncados com `…` quando necessário.

**Proibido.**
- Emojis (em qualquer superfície).
- Exclamações. *"Bem-vindo!"* → *"Bem-vindo."*
- Linguagem corporativa genérica: "unleash", "empower", "game-changer", "revolucionar", "transformar seu negócio".
- Hype de IA: "alimentado por IA", "IA avançada", "inteligente". Se há IA, diga-se o que ela faz: *"extração de entidades por NER"*.
- Call-to-actions clichê: *"Comece grátis hoje!"*. Preferir: *"Solicitar avaliação"*, *"Ver laudo exemplo"*.

**Exemplos aceitáveis.**

> Vestígio consolida comunicações digitais em laudos com cadeia de custódia. Cada vestígio carrega carimbo de tempo, hash e procedência auditável.

> Importação de conversas WhatsApp, caixas de e-mail (.eml, .mbox, .pst) e documentos. Extração automática de entidades, datas e anexos. Exportação em PDF assinado digitalmente.

> Corpus analisado: 14.832 mensagens · 3.107 anexos · 62 dias.

**Exemplos rejeitados.**

> ~~Revolucione suas investigações com IA! 🚀~~
> ~~A plataforma mais inteligente para advogados modernos.~~
> ~~Você vai adorar nossa nova timeline!~~

---

## Fundamentos visuais

**Paleta (ver `colors_and_type.css`).**

| Token | Hex | Uso |
|---|---|---|
| `--vst-bordo` | `#6B0F1A` | Primária institucional. Marca, CTA principal, selos, acentos de autoridade. Usada com parcimônia. |
| `--vst-ink` | `#1A1A1A` | Texto, rules fortes, fundos escuros. |
| `--vst-graphite` | `#4A4A4A` | Texto secundário, **links** (sublinhados). |
| `--vst-paper` | `#F5F1EA` | Canvas padrão — off-white quente. |
| `--vst-gold` | `#8B6F47` | Reservado: selos, marcas d'água, brasões. Nunca CTA. |

**Sem azul em lugar algum.** Inclui links: links usam `--vst-graphite` com sublinhado 1px, hover vira `--vst-bordo`.

**Tipografia.**
- **EB Garamond** — títulos obrigatórios (H1–H4, display, lead editorial). Peso 500 padrão; 600 em H3/H4 para densidade.
- **Inter** — corpo, UI, labels. Mínimo 15–16px, line-height 1.6–1.7.
- **JetBrains Mono** — evidência forense: hashes, trechos de log, IDs de vestígio, fragmentos de mensagem citada.

**Fundos.** Papel off-white quente uniforme. **Sem gradientes coloridos.** **Sem glassmorphism.** **Sem imagens full-bleed decorativas.** Imagens quando usadas são documentais (fac-símiles, capturas de evidência) com tratamento monocromático/sepia leve.

**Animação.**
- Restrita e curta (120–280ms), easing `cubic-bezier(.2,.0,.2,1)`.
- Fades e deslocamentos sutis (≤4px) apenas. Sem bounces, sem springs lúdicos, sem parallax.
- Hover de link: transição de cor em 120ms. Hover de botão: mudança de cor de fundo em 120ms — **sem elevação**, **sem shrink**.

**Estados.**
- **Hover botão primário**: bordô escurece para `--vst-bordo-deep`. Cursor pointer. Sem translate, sem shadow pop.
- **Press**: cor ainda mais escura, sem scale.
- **Hover link**: graphite → bordô, sublinhado acompanha.
- **Focus**: outline 2px bordô, offset 2px. Visível sempre (acessibilidade forense).

**Bordas e rules.**
- Hair rule `rgba(26,26,26,0.12)` — separação editorial sutil.
- Rule `rgba(26,26,26,0.22)` — separação de zonas.
- Rule ink `#1A1A1A` — fechamento editorial (topo de tabela, footer de laudo).
- Underline de cabeçalho de tabela: 1.5px sólido preto (editorial).

**Sombras.** Mínimas. Papel não-UI. Modal ganha `0 20px 48px rgba(26,26,26,0.18)`. Cards padrão usam `border` + `0 1px 0 hairline`, não drop shadow.

**Raios.** Quase-zero. `2–3px` máximo para botões/inputs. `0` para cards e superfícies editoriais. Pill `999px` apenas em tags e status dots.

**Transparência / blur.** Nenhum blur. Transparência só em wash de seleção bordô (0.18) e overlays modais (ink 0.55).

**Layout.**
- Grid de 12 colunas em app, 6 em marketing.
- Densidade alta: rejeitar heros vazios, rejeitar cards gigantes com 3 palavras.
- Column rule vertical `1px ink 0.12` separa zonas em layouts multi-coluna (motif editorial).
- Margem generosa só em capa de documento. App é denso.

**Imagem.** Se usada: preto-e-branco ou duotone bordô/paper, grão leve, contraste alto. Tratamento arquivístico.

**Cards.** Borda `1px rule-hair`, fundo `paper-hi`, raio 0–2px, sem sombra. Eyebrow UPPERCASE tracked no topo. Título serif. Meta graphite-3 ao pé.

**Impressão P&B.** Toda tela deve sobreviver a uma impressão preto-e-branco em escritório de advocacia sem perder hierarquia. Testar: se hierarquia depende só de cor, refazer. Hierarquia correta vem de peso, tamanho, família, e rules.

---

## Iconografia

**Conjunto base.** [Lucide](https://lucide.dev) via CDN — stroke 1.5px, estilo linear neutro. Compatível com estética forense: nem corporate-round, nem hand-drawn.

> CDN: `https://unpkg.com/lucide-static@latest/icons/<name>.svg`

**Regras.**
- Stroke 1.5px padrão. Nunca ícones preenchidos (solid) exceto em status dots / badges de selo.
- Cor: `currentColor` — herda do texto. Ícones em corpo: `--vst-fg-2`. Em botão primário: `--vst-paper`.
- Tamanhos: 16px (inline), 18px (botão), 20px (nav), 24px (page header), 32px (empty state).
- Evitar ícones decorativos. Cada ícone deve ter função de navegação, status ou ação.

**Ícones canônicos Vestígio** (subset Lucide usado com frequência):
`file-text`, `folder`, `folder-open`, `clock`, `shield`, `shield-check`, `search`, `filter`, `download`, `upload`, `hash`, `fingerprint`, `scale`, `gavel`, `archive`, `lock`, `key`, `paperclip`, `message-square-text`, `mail`, `file-audio`, `calendar`, `check`, `x`, `alert-triangle`, `info`, `chevron-right`, `more-horizontal`, `external-link`.

**Emoji.** Proibido em qualquer superfície — produto, marketing, docs.

**Unicode como ícone.** Aceitável para separadores e ornamentos tipográficos editoriais: `·`, `§`, `¶`, `†`, `—`. Nunca símbolos decorativos (★ ❯ ✦).

**Selos / marcas d'água.** SVG próprio, cor `--vst-gold` em opacidade baixa (0.12–0.25), sobre papel. Motivo: brasão tipográfico simples com "VESTÍGIO · LAUDO PERICIAL". Ver `assets/seal.svg`.

**Logo.** Wordmark serif EB Garamond 500, tracking neutro, cor ink. Variante invertida em paper. Acento bordô opcional em `í`.

---

## Índice

```
/
├── README.md                    — este arquivo
├── SKILL.md                     — manifest de skill agentic
├── colors_and_type.css          — tokens CSS completos (cores, tipo, spacing, shadow, motion)
├── assets/                      — logos, selos, ícones
│   ├── logo.svg                 — wordmark horizontal
│   ├── logo-mark.svg            — monograma V
│   └── seal.svg                 — selo dourado para laudos
├── preview/                     — cards do Design System tab (registrados no manifest)
├── ui_kits/
│   └── vestigio/                — UI kit do app web Vestígio
│       ├── README.md
│       ├── index.html           — prototipo clicável
│       └── *.jsx                — componentes
└── uploads/                     — (vazio; aguardando zips de fontes)
```

---

## Caveats & pendências

- **Fontes.** Zips `.ttf` citados no briefing não chegaram. Google Fonts CDN é o fallback; reanexar para produção offline.
- **Produto único.** Sem codebase ou Figma anexado, o UI kit é uma interpretação fiel do briefing — não uma recreação pixel-perfect. Alinhar com screenshots reais ou código para calibragem final.
- **Sem marketing site separado.** Briefing não menciona; UI kit foca no produto SaaS.
