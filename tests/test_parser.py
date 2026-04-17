"""Testes do parser do _chat.txt — formatos pt-BR (dash + bracket) e casos de borda."""

from __future__ import annotations

from pathlib import Path

import pytest

from rdo_agent.parser import (
    MessageType,
    ParsedMessage,
    parse_chat_file,
)

FIXTURES = Path(__file__).parent / "fixtures"
DASH_FIXTURE = FIXTURES / "sample_chat_dash.txt"
BRACKET_FIXTURE = FIXTURES / "sample_chat_bracket.txt"


# Posições conhecidas em ambas as fixtures (mesma estrutura).
IDX_SYSTEM_CRIPTO = 0
IDX_SYSTEM_GRUPO = 1
IDX_TEXT_SIMPLES = 2
IDX_TEXT_MULTILINHA = 3
IDX_STICKER = 4
IDX_MEDIA_ANEXADO = 5
IDX_MEDIA_ARQUIVO_ANEXADO = 6
IDX_MEDIA_FORMATO_B_COM_ESPACO = 7
IDX_MEDIA_PTT = 8
IDX_DELETED = 9
IDX_EDITED_COM_ESPACO = 10
IDX_EDITED_SEM_ESPACO = 11

EXPECTED_MESSAGE_COUNT = 12


@pytest.fixture(scope="module")
def dash_messages() -> list[ParsedMessage]:
    return parse_chat_file(DASH_FIXTURE)


@pytest.fixture(scope="module")
def bracket_messages() -> list[ParsedMessage]:
    return parse_chat_file(BRACKET_FIXTURE)


# ---------------------------------------------------------------------------
# Detecção de formato
# ---------------------------------------------------------------------------


def test_detects_dash_format(dash_messages: list[ParsedMessage]) -> None:
    assert len(dash_messages) == EXPECTED_MESSAGE_COUNT


def test_detects_bracket_format(bracket_messages: list[ParsedMessage]) -> None:
    assert len(bracket_messages) == EXPECTED_MESSAGE_COUNT


def test_unknown_format_raises(tmp_path: Path) -> None:
    bad = tmp_path / "not_a_chat.txt"
    bad.write_text("isto não é um export do WhatsApp\nlinha 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="formato não reconhecido"):
        parse_chat_file(bad)


# ---------------------------------------------------------------------------
# Casos por tipo — dash format
# ---------------------------------------------------------------------------


def test_parses_text(dash_messages: list[ParsedMessage]) -> None:
    msg = dash_messages[IDX_TEXT_SIMPLES]
    assert msg.message_type == MessageType.TEXT
    assert msg.sender == "Maria Souza"
    assert msg.content == "Bom dia equipe"
    assert msg.edited is False
    assert msg.media_filename is None


def test_parses_system_message(dash_messages: list[ParsedMessage]) -> None:
    msg = dash_messages[IDX_SYSTEM_CRIPTO]
    assert msg.message_type == MessageType.SYSTEM
    assert msg.sender is None
    assert "criptografia" in msg.content


def test_parses_sticker(dash_messages: list[ParsedMessage]) -> None:
    msg = dash_messages[IDX_STICKER]
    assert msg.message_type == MessageType.STICKER
    assert msg.sender == "Maria Souza"


def test_parses_deleted(dash_messages: list[ParsedMessage]) -> None:
    msg = dash_messages[IDX_DELETED]
    assert msg.message_type == MessageType.DELETED
    assert msg.sender == "Maria Souza"


def test_parses_edited_flag(dash_messages: list[ParsedMessage]) -> None:
    msg = dash_messages[IDX_EDITED_COM_ESPACO]
    assert msg.message_type == MessageType.TEXT
    assert msg.edited is True
    assert msg.content == "Mensagem original aqui"
    assert "<Esta mensagem foi editada>" not in msg.content


def test_parses_edited_message_without_trailing_space(
    dash_messages: list[ParsedMessage],
) -> None:
    """T2: marker de edição grudado no conteúdo, sem espaço antes."""
    msg = dash_messages[IDX_EDITED_SEM_ESPACO]
    assert msg.message_type == MessageType.TEXT
    assert msg.edited is True
    assert msg.content == "outro texto"
    assert "<Esta mensagem foi editada>" not in msg.content


# ---------------------------------------------------------------------------
# Mídia
# ---------------------------------------------------------------------------


def test_parses_media_anexado(dash_messages: list[ParsedMessage]) -> None:
    """Formato A: <anexado: NOME.ext>"""
    msg = dash_messages[IDX_MEDIA_ANEXADO]
    assert msg.message_type == MessageType.MEDIA_REF
    assert msg.media_filename == "VID-20260312-WA0007.mp4"


def test_parses_media_arquivo_anexado(dash_messages: list[ParsedMessage]) -> None:
    """Formato B: NOME.ext (arquivo anexado)"""
    msg = dash_messages[IDX_MEDIA_ARQUIVO_ANEXADO]
    assert msg.message_type == MessageType.MEDIA_REF
    assert msg.media_filename == "IMG-20260312-WA0015.jpg"


def test_parses_media_formato_b_com_espaco_no_nome(
    dash_messages: list[ParsedMessage],
) -> None:
    """T1: nome de arquivo com espaço, ex: 'IMG-... (1).jpg'."""
    msg = dash_messages[IDX_MEDIA_FORMATO_B_COM_ESPACO]
    assert msg.message_type == MessageType.MEDIA_REF
    assert msg.media_filename == "IMG-20260312-WA0015 (1).jpg"


def test_parses_media_ptt_audio(dash_messages: list[ParsedMessage]) -> None:
    msg = dash_messages[IDX_MEDIA_PTT]
    assert msg.message_type == MessageType.MEDIA_REF
    assert msg.media_filename == "PTT-20260312-WA0007.opus"


# ---------------------------------------------------------------------------
# Multi-linha
# ---------------------------------------------------------------------------


def test_multiline_continuation(dash_messages: list[ParsedMessage]) -> None:
    msg = dash_messages[IDX_TEXT_MULTILINHA]
    assert msg.message_type == MessageType.TEXT
    assert msg.sender == "João Silva"
    assert msg.content == "Bom dia\nVamos começar a concretagem hoje\nA previsão é boa"


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def test_parses_latin1_fallback(tmp_path: Path) -> None:
    """Exports antigos vinham em latin-1; parser deve cair no fallback."""
    txt = tmp_path / "chat_latin1.txt"
    # "ção" em latin-1: c, \xe7, \xe3, o — \xe7\xe3 é byte sequence inválida em utf-8
    txt.write_bytes("12/03/2026 09:00 - Maria: bom dia, ação iniciada\n".encode("latin-1"))

    msgs = parse_chat_file(txt)
    assert len(msgs) == 1
    assert msgs[0].sender == "Maria"
    assert msgs[0].content == "bom dia, ação iniciada"


# ---------------------------------------------------------------------------
# Integração — fixture inteira por formato
# ---------------------------------------------------------------------------


def test_parse_full_fixture_dash(dash_messages: list[ParsedMessage]) -> None:
    types = [m.message_type for m in dash_messages]
    assert types == [
        MessageType.SYSTEM,
        MessageType.SYSTEM,
        MessageType.TEXT,
        MessageType.TEXT,
        MessageType.STICKER,
        MessageType.MEDIA_REF,
        MessageType.MEDIA_REF,
        MessageType.MEDIA_REF,
        MessageType.MEDIA_REF,
        MessageType.DELETED,
        MessageType.TEXT,  # editada
        MessageType.TEXT,  # editada sem espaço
    ]
    # Ordenação preservada por line_number
    assert [m.line_number for m in dash_messages] == sorted(m.line_number for m in dash_messages)


def test_parse_full_fixture_bracket(bracket_messages: list[ParsedMessage]) -> None:
    """Bracket deve produzir os mesmos tipos e remetentes na mesma ordem."""
    dash = parse_chat_file(DASH_FIXTURE)
    assert [m.message_type for m in bracket_messages] == [m.message_type for m in dash]
    assert [m.sender for m in bracket_messages] == [m.sender for m in dash]
    assert [m.media_filename for m in bracket_messages] == [m.media_filename for m in dash]
