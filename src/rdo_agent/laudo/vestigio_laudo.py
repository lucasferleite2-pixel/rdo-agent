"""
vestigio_laudo.py — Gerador de Laudo Forense Vestígio
=======================================================

Módulo Python que produz laudos forenses em PDF com identidade visual Vestígio.
Desenhado pra integração direta com rdo-agent.

USO BÁSICO:

    from vestigio_laudo import LaudoGenerator, LaudoData

    data = LaudoData(
        caso_id="VST-2026-0001",
        titulo="Análise Forense · EE Santa Quitéria",
        periodo_inicio="15/11/2025",
        periodo_fim="28/03/2026",
        operador="Lucas Fernandes Leite",
        corpus_hash="a7f2b4c9d1e8",
        total_mensagens=3847,
        total_correlacoes=127,
        # ... (ver LaudoData docstring para campos completos)
    )

    gen = LaudoGenerator()
    gen.generate(data, "laudo_saida.pdf")

DEPENDÊNCIAS:
- weasyprint>=68.0
- jinja2>=3.0

FONTES REQUERIDAS (incluídas no pacote):
- EB Garamond (variable)
- Inter (variable)
- JetBrains Mono (variable)
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import base64
import hashlib

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration


# =============================================================================
# DATA CLASSES — Estruturas que rdo-agent preenche e passa pro gerador
# =============================================================================

@dataclass
class Correlacao:
    """Correlação detectada entre eventos do corpus."""
    tipo: str  # "TEMPORAL" | "SEMANTIC" | "MATH"
    descricao: str
    excerto_a: str
    excerto_b: str
    confianca: float  # 0.0 a 1.0


@dataclass
class EventoCronologia:
    """Evento numa cronologia forense."""
    data: str  # "15/11/2025"
    hora: Optional[str] = None  # "14:30"
    autor: str = ""
    conteudo: str = ""
    tipo: str = "mensagem"  # "mensagem" | "pagamento" | "decisao" | "documento"
    tags: list[str] = field(default_factory=list)


@dataclass
class SecaoNarrativa:
    """Uma seção da narrativa forense (introdução, desenvolvimento, conclusão)."""
    titulo: str
    conteudo: str  # Markdown-compatível (parágrafos separados por \n\n)


@dataclass
class LaudoData:
    """Dados completos de um laudo forense.

    Passado como entrada pro LaudoGenerator. Todos os campos exceto os
    obrigatórios têm defaults apropriados.
    """
    # Obrigatórios — identificação do caso
    caso_id: str                      # Ex: "VST-2026-0001"
    titulo: str                       # Ex: "Análise Forense · EE Santa Quitéria"
    periodo_inicio: str               # Ex: "15/11/2025"
    periodo_fim: str                  # Ex: "28/03/2026"
    operador: str                     # Ex: "Lucas Fernandes Leite"

    # Metadata forense
    corpus_hash: str = ""             # Hash do corpus analisado (auditabilidade)
    total_mensagens: int = 0
    total_documentos: int = 0
    total_audios: int = 0
    total_correlacoes: int = 0

    # Identificação do cliente/caso
    cliente: str = ""                 # Nome do advogado ou parte que encomendou
    processo: str = ""                # Número do processo judicial, se houver
    objeto: str = ""                  # Objeto da perícia em 1-2 frases

    # Conteúdo narrativo
    resumo_executivo: str = ""        # Resumo em 1 parágrafo
    secoes_narrativa: list[SecaoNarrativa] = field(default_factory=list)
    cronologia: list[EventoCronologia] = field(default_factory=list)
    correlacoes: list[Correlacao] = field(default_factory=list)

    # Controle de versão
    versao_laudo: str = "1.0"
    data_geracao: str = field(default_factory=lambda: datetime.now().strftime("%d/%m/%Y"))

    # Configuração de renderização
    incluir_marca_dagua_certificacao: bool = False
    incluir_cronologia_completa: bool = True
    incluir_ground_truth: bool = False

    def __post_init__(self):
        """Auto-gera corpus_hash se não fornecido, usando caso_id como seed."""
        if not self.corpus_hash:
            seed = f"{self.caso_id}-{self.periodo_inicio}-{self.periodo_fim}".encode()
            self.corpus_hash = hashlib.sha256(seed).hexdigest()[:12]


# =============================================================================
# GENERATOR — Classe principal que produz o PDF
# =============================================================================

# Paths relativos ao arquivo do módulo
MODULE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = MODULE_DIR / "templates"
STATIC_DIR = MODULE_DIR / "static"
FONTS_DIR = MODULE_DIR / "fonts"


class LaudoGenerator:
    """Gera laudos forenses em PDF com identidade Vestígio.

    Uso típico:
        gen = LaudoGenerator()
        gen.generate(laudo_data, output_path="laudo.pdf")

    Configurações opcionais:
        gen = LaudoGenerator(
            templates_dir="/custom/templates",
            fonts_dir="/custom/fonts",
        )
    """

    def __init__(
        self,
        templates_dir: Optional[Path] = None,
        static_dir: Optional[Path] = None,
        fonts_dir: Optional[Path] = None,
    ):
        self.templates_dir = Path(templates_dir) if templates_dir else TEMPLATES_DIR
        self.static_dir = Path(static_dir) if static_dir else STATIC_DIR
        self.fonts_dir = Path(fonts_dir) if fonts_dir else FONTS_DIR

        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def _font_face_css(self) -> str:
        """Gera @font-face com fontes Vestígio embarcadas em base64."""
        fonts = {
            "EBGaramond-VF.ttf": ("EB Garamond", "normal"),
            "EBGaramond-Italic-VF.ttf": ("EB Garamond", "italic"),
            "Inter-VF.ttf": ("Inter", "normal"),
            "JetBrainsMono-VF.ttf": ("JetBrains Mono", "normal"),
        }
        blocks = []
        for filename, (family, style) in fonts.items():
            path = self.fonts_dir / filename
            if not path.exists():
                raise FileNotFoundError(f"Fonte requerida não encontrada: {path}")
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            blocks.append(f"""
@font-face {{
  font-family: '{family}';
  src: url(data:font/ttf;base64,{b64}) format('truetype');
  font-weight: 100 900;
  font-style: {style};
}}""")
        return "\n".join(blocks)

    def _read_brand_css(self) -> str:
        """Lê o CSS da marca Vestígio."""
        path = self.static_dir / "laudo.css"
        if not path.exists():
            raise FileNotFoundError(f"CSS do laudo não encontrado: {path}")
        return path.read_text(encoding="utf-8")

    def render_html(self, data: LaudoData) -> str:
        """Renderiza o template HTML sem converter em PDF.

        Útil pra testes ou pra visualização em browser.
        """
        template = self.jinja_env.get_template("laudo.html")
        return template.render(laudo=data)

    def generate(
        self,
        data: LaudoData,
        output_path: str | Path,
    ) -> Path:
        """Gera o laudo como PDF e salva em output_path.

        Returns:
            Path do arquivo gerado
        """
        html_content = self.render_html(data)
        full_css = self._font_face_css() + "\n" + self._read_brand_css()

        font_config = FontConfiguration()
        css = CSS(string=full_css, font_config=font_config)
        html = HTML(string=html_content, base_url=str(MODULE_DIR))

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        html.write_pdf(
            str(output_path),
            stylesheets=[css],
            font_config=font_config,
        )
        return output_path


# =============================================================================
# CLI — Uso via linha de comando
# =============================================================================

if __name__ == "__main__":
    import sys
    print("Vestígio Laudo Generator")
    print("Uso: importar o módulo em seu código Python.")
    print()
    print("Exemplo mínimo:")
    print("  from vestigio_laudo import LaudoGenerator, LaudoData")
    print("  data = LaudoData(caso_id='TEST', titulo='Teste', ...)")
    print("  LaudoGenerator().generate(data, 'out.pdf')")
    sys.exit(0)
