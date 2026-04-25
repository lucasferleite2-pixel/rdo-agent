"""
Validação de conteúdo do laudo Vestígio gerado.

Pattern para testes de PDF de laudo: **pyMuPDF (fitz)**, não pdfplumber.
Production code de document_extractor segue pdfplumber por escolha
legacy. pyMuPDF é tolerante a section-marks Unicode e letter-spacing
tipográfico do brandbook Vestígio.

Adicionado na Sessão 4 (dívida #37) sobre amostra real preservada em
``docs/brand/Laudo-Real-EVERALDO-v1.0.1.pdf``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

LAUDO_REAL_PATH = (
    Path(__file__).parent.parent
    / "docs"
    / "brand"
    / "Laudo-Real-EVERALDO-v1.0.1.pdf"
)


def _extract_text_normalized(pdf_path: Path) -> str:
    """
    Extrai todo o texto do PDF e normaliza whitespace para tolerar
    letter-spacing tipográfico (ex: "V E S T Í G I O" -> "VESTÍGIO"
    quando comparando "VESTIGIO"-like).

    Retorna string com whitespace colapsado a 1 espaço.
    """
    fitz = pytest.importorskip("fitz")
    doc = fitz.open(pdf_path)
    try:
        chunks = [page.get_text() for page in doc]
    finally:
        doc.close()
    raw = "\n".join(chunks)
    # Colapsa qualquer sequência de whitespace a 1 espaço (preserva
    # presença mas remove letter-spacing visual)
    return re.sub(r"\s+", " ", raw)


def _strip_letter_spacing(text: str) -> str:
    """
    Remove o efeito visual de letter-spacing nas palavras de display
    (ex: 'V E S T Í G I O' -> 'VESTÍGIO'). Conservador: só ataca
    sequências de letras maiúsculas separadas por espaço único.
    """
    return re.sub(
        r"(?:(?<=^)|(?<= ))((?:[A-ZÀ-ÝÇ][ ]){2,}[A-ZÀ-ÝÇ])(?= |$)",
        lambda m: m.group(1).replace(" ", ""),
        text,
    )


@pytest.fixture(scope="module")
def laudo_text_normalized() -> str:
    if not LAUDO_REAL_PATH.exists():
        pytest.skip(f"Amostra de laudo não encontrada: {LAUDO_REAL_PATH}")
    return _extract_text_normalized(LAUDO_REAL_PATH)


def test_laudo_pdf_has_vestigio_sections(laudo_text_normalized: str):
    """
    Laudo deve conter os marcadores institucionais do brandbook
    Vestígio: nome do produto, footer institucional, e ao menos um
    section-mark numérico de capítulo.
    """
    text = laudo_text_normalized
    text_no_spacing = _strip_letter_spacing(text)

    # Identidade Vestígio (com ou sem letter-spacing)
    assert "VESTÍGIO" in text_no_spacing or "Vestígio" in text, (
        "Marca Vestígio (display ou body) não encontrada"
    )

    # Footer institucional
    assert "Vestígio Tecnologia" in text, (
        "Footer institucional 'Vestígio Tecnologia' ausente"
    )

    # Numeração de capítulo (section-marks "01", "02", ... no display)
    assert re.search(r"\b0[1-9]\b", text), (
        "Section-marks numéricos (01, 02, ...) ausentes"
    )

    # Operador identificado
    assert "Lucas Fernandes Leite" in text, "Operador ausente"


def test_laudo_pdf_has_real_everaldo_data(laudo_text_normalized: str):
    """
    Laudo deve referenciar o corpus EVERALDO_SANTAQUITERIA e estar
    livre dos termos do laudo de exemplo (mock) — garantia de que a
    amostra preservada veio de geração real, não copy-paste.

    Termos proibidos vêm do exemplo
    ``docs/brand/Laudo-Exemplo-Santa-Quiteria.pdf`` (mock que não
    pode contaminar o laudo real).
    """
    text = laudo_text_normalized

    # Corpus referenciado (pyMuPDF pode quebrar nome longo no header,
    # então aceita o prefixo robusto)
    assert "EVERALDO" in text, "EVERALDO ausente do laudo"
    assert "SANTAQUITER" in text, "SANTAQUITER ausente do laudo"

    # Termos do mock que NÃO podem aparecer no laudo real
    forbidden = [
        "Sérgio Albernaz",  # advogado fictício do exemplo
        "Sergio Albernaz",  # variante sem acento
        "item 150107",  # SKU fictício do exemplo
        "agregados de alta dureza",  # frase técnica do mock
        "Lorem ipsum",  # placeholder
    ]
    for term in forbidden:
        assert term not in text, (
            f"Termo proibido do mock encontrado no laudo real: {term!r}"
        )
