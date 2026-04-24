# Vestígio Laudo Generator

Gerador de laudos forenses em PDF com identidade visual Vestígio. Desenhado pra integração direta com **rdo-agent** e qualquer aplicação Python que produza laudos periciais.

---

## Instalação

```bash
pip install weasyprint>=68.0 jinja2>=3.0
```

Nada mais. As fontes (EB Garamond, Inter, JetBrains Mono) vêm embutidas no pacote — zero dependência de sistema.

---

## Uso mínimo

```python
from vestigio_laudo import LaudoGenerator, LaudoData

data = LaudoData(
    caso_id="VST-2026-0001",
    titulo="Análise Forense · Caso Exemplo",
    periodo_inicio="15/11/2025",
    periodo_fim="28/03/2026",
    operador="Lucas Fernandes Leite",
)

LaudoGenerator().generate(data, "laudo.pdf")
```

Pronto. Laudo renderizado, com capa, rodapés institucionais e metadata forense básica.

---

## Integração com rdo-agent

O gerador foi projetado pra ser o **ponto terminal do pipeline do rdo-agent** — a camada de saída que converte narrativas, correlações e cronologias em documento apresentável.

### Passo 1. Copiar o pacote pro repositório do rdo-agent

```bash
cp -r laudo-generator/ rdo-agent/src/rdo_agent/laudo/
```

### Passo 2. Adaptar o adapter no rdo-agent

Crie `rdo_agent/laudo/adapter.py`:

```python
"""Adapter: estado interno do rdo-agent → LaudoData do Vestígio."""
from .vestigio_laudo import LaudoData, SecaoNarrativa, EventoCronologia, Correlacao


def rdo_to_vestigio_data(corpus_id: str, corpus_state: dict) -> LaudoData:
    """Converte o estado de um corpus rdo-agent em LaudoData."""

    # Estado interno do rdo-agent mapeia diretamente para LaudoData
    return LaudoData(
        caso_id=corpus_state["metadata"]["case_id"],
        titulo=corpus_state["metadata"]["title"],
        periodo_inicio=corpus_state["corpus"]["date_start"],
        periodo_fim=corpus_state["corpus"]["date_end"],
        operador=corpus_state["metadata"]["operator"],
        corpus_hash=corpus_state["corpus"]["sha256"][:12],
        total_mensagens=corpus_state["stats"]["messages"],
        total_documentos=corpus_state["stats"]["documents"],
        total_audios=corpus_state["stats"]["audios"],
        total_correlacoes=len(corpus_state["correlations"]),

        cliente=corpus_state["metadata"].get("client", ""),
        processo=corpus_state["metadata"].get("process_number", ""),
        objeto=corpus_state["metadata"].get("scope", ""),
        resumo_executivo=corpus_state["narrative"]["executive_summary"],

        secoes_narrativa=[
            SecaoNarrativa(titulo=s["title"], conteudo=s["content"])
            for s in corpus_state["narrative"]["sections"]
        ],
        cronologia=[
            EventoCronologia(
                data=e["date"],
                hora=e.get("time"),
                autor=e.get("author", ""),
                conteudo=e["content"],
                tipo=e.get("type", "mensagem"),
                tags=e.get("tags", []),
            )
            for e in corpus_state["timeline"]
        ],
        correlacoes=[
            Correlacao(
                tipo=c["type"].upper(),
                descricao=c["description"],
                excerto_a=c["excerpt_a"],
                excerto_b=c["excerpt_b"],
                confianca=c["confidence"],
            )
            for c in corpus_state["correlations"]
        ],
    )
```

### Passo 3. CLI command no rdo-agent

```python
# rdo_agent/cli/export_laudo.py
import click
from pathlib import Path
from rdo_agent.laudo.vestigio_laudo import LaudoGenerator
from rdo_agent.laudo.adapter import rdo_to_vestigio_data
from rdo_agent.state import load_corpus_state

@click.command()
@click.option("--corpus", required=True, help="Corpus ID")
@click.option("--output", required=True, type=click.Path())
@click.option("--certified/--no-certified", default=False,
              help="Incluir selo de certificação na última página")
def export_laudo(corpus: str, output: str, certified: bool):
    """Exporta laudo forense em PDF com identidade Vestígio."""
    state = load_corpus_state(corpus)
    data = rdo_to_vestigio_data(corpus, state)
    data.incluir_marca_dagua_certificacao = certified

    gen = LaudoGenerator()
    result = gen.generate(data, Path(output))
    click.echo(f"✓ Laudo gerado: {result}")
```

Uso:

```bash
rdo-agent export-laudo --corpus EVERALDO_SANTAQUITERIA \
                       --output ./laudos/everaldo-2026.pdf \
                       --certified
```

---

## Estrutura de dados — `LaudoData`

Todos os campos são tipados com `dataclass`. Campos obrigatórios no topo, opcionais com defaults apropriados.

### Obrigatórios

| Campo | Tipo | Descrição |
|---|---|---|
| `caso_id` | `str` | Identificador único do caso (ex: `"VST-2026-0001"`) |
| `titulo` | `str` | Título do laudo |
| `periodo_inicio` | `str` | Data de início do período analisado (formato livre, ex: `"15/11/2025"`) |
| `periodo_fim` | `str` | Data de fim do período |
| `operador` | `str` | Nome do operador responsável pela análise |

### Metadata forense (opcionais, todos com default)

| Campo | Tipo | Default | Observação |
|---|---|---|---|
| `corpus_hash` | `str` | auto-gerado | Se vazio, gera sha256 baseado em `caso_id + período` |
| `total_mensagens` | `int` | `0` | Total de mensagens analisadas |
| `total_documentos` | `int` | `0` | Total de documentos anexos |
| `total_audios` | `int` | `0` | Total de áudios analisados |
| `total_correlacoes` | `int` | `0` | Total de correlações detectadas |

### Conteúdo narrativo

| Campo | Tipo | Descrição |
|---|---|---|
| `resumo_executivo` | `str` | 1 parágrafo de resumo |
| `secoes_narrativa` | `list[SecaoNarrativa]` | Seções da narrativa forense — cada seção vira uma página |
| `cronologia` | `list[EventoCronologia]` | Eventos cronológicos (até N eventos; layout responsivo) |
| `correlacoes` | `list[Correlacao]` | Correlações detectadas (TEMPORAL / SEMANTIC / MATH) |

### Controle de renderização

| Campo | Tipo | Default | Uso |
|---|---|---|---|
| `incluir_marca_dagua_certificacao` | `bool` | `False` | Liga selo dourado na última página |
| `incluir_cronologia_completa` | `bool` | `True` | Desliga a seção de cronologia se `False` |
| `versao_laudo` | `str` | `"1.0"` | Versão do laudo, aparece na capa |
| `data_geracao` | `str` | hoje | Data de geração automática |

---

## Dataclasses auxiliares

### `SecaoNarrativa`

```python
SecaoNarrativa(
    titulo="Contexto da perícia",
    conteudo="Parágrafo 1...\n\nParágrafo 2...",  # \n\n separa parágrafos
)
```

### `EventoCronologia`

```python
EventoCronologia(
    data="15/11/2025",
    hora="09:14",  # opcional
    autor="Eng. Fiscal (SEE-MG)",
    conteudo="Emissão de ordem de serviço...",
    tipo="documento",  # "mensagem" | "pagamento" | "decisao" | "documento"
    tags=["OS", "item-150107"],
)
```

Cada `tipo` recebe uma cor de borda distinta na timeline:
- `documento` → grafite
- `decisao` → bordô
- `pagamento` → dourado
- `mensagem` → hairline cinza

### `Correlacao`

```python
Correlacao(
    tipo="TEMPORAL",  # "TEMPORAL" | "SEMANTIC" | "MATH"
    descricao="Descrição em 1-3 frases da relação detectada...",
    excerto_a="Primeiro trecho do corpus (será exibido entre aspas)",
    excerto_b="Segundo trecho do corpus",
    confianca=0.94,  # 0.0 a 1.0 — aparece como %
)
```

---

## Customização avançada

### Usar paths customizados

```python
gen = LaudoGenerator(
    templates_dir="/meu/caminho/templates",
    static_dir="/meu/caminho/static",
    fonts_dir="/meu/caminho/fonts",
)
```

### Renderizar HTML sem gerar PDF (pra debug em browser)

```python
gen = LaudoGenerator()
html_content = gen.render_html(data)
with open("/tmp/preview.html", "w") as f:
    f.write(html_content)
# Abra /tmp/preview.html no browser
```

---

## Estrutura do pacote

```
06-laudo-generator/
├── README.md                              ← Este documento
├── vestigio_laudo.py                      ← Módulo principal (LaudoGenerator + dataclasses)
├── gen_laudo_example.py                   ← Exemplo executável realista
├── Laudo-Exemplo-Santa-Quiteria.pdf       ← Output do exemplo (10 páginas)
├── templates/
│   └── laudo.html                         ← Template Jinja2 do laudo
├── static/
│   └── laudo.css                          ← CSS da identidade Vestígio
└── fonts/
    ├── EBGaramond-VF.ttf
    ├── EBGaramond-Italic-VF.ttf
    ├── Inter-VF.ttf
    └── JetBrainsMono-VF.ttf
```

---

## Notas técnicas

- **WeasyPrint** é usado em vez de ReportLab puro porque oferece controle total de tipografia via CSS. Os glyphs do EB Garamond saem com kerning, ligaduras e rendering profissional.
- **Fontes embarcadas em base64** no CSS — o PDF resultante não depende das fontes estarem instaladas no sistema do leitor.
- **Paginação automática** com `@page` do CSS Paged Media. Rodapé institucional aplicado em todas as páginas exceto capa.
- **Text-to-path não é necessário aqui** — diferente dos logos que precisam ser portáveis pra Figma/Illustrator, o PDF renderiza as fontes embarcadas diretamente. Isso torna o arquivo menor e o texto selecionável/copiável.
- **Sem dependências pesadas** — WeasyPrint + Jinja2 + Python 3.10+. Instalação limpa em qualquer ambiente.

---

## Performance

Um laudo típico com ~10 seções narrativas, ~20 eventos de cronologia e ~5 correlações gera em **1-2 segundos** em máquina média. Para lotes grandes, o `LaudoGenerator` pode ser reutilizado (o custo de inicialização é minúsculo).

```python
gen = LaudoGenerator()  # custo único
for corpus_id in corpus_list:
    data = rdo_to_vestigio_data(corpus_id, load_state(corpus_id))
    gen.generate(data, f"laudos/{corpus_id}.pdf")
```

---

**Vestígio Tecnologia Ltda** (em abertura) · Subsidiária da HCF Investimentos e Participações · 2026
