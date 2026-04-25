"""
Parser do _chat.txt do WhatsApp — Camada 1.

Converte o arquivo .txt exportado do WhatsApp em uma lista de mensagens
estruturadas, identificando remetente, timestamp, conteúdo e referências a mídias.

Função pura: Path → list[ParsedMessage]. Sem efeito colateral além de log.
Resolução de timezone NÃO é feita aqui — é trabalho do módulo `temporal`.

Suporta os dois formatos de export pt-BR:
  - "dash"    (Android): "12/03/2026 09:45 - Nome: conteúdo"
  - "bracket" (iOS):     "[12/03/2026, 09:45:32] Nome: conteúdo"

Detecção é feita uma única vez pela primeira linha de dados; o resto do
arquivo usa o regex correspondente. Premissa: WhatsApp não mistura formatos
no mesmo export.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from dateutil.parser import parse as dateutil_parse

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums e dataclass
# ---------------------------------------------------------------------------


class MessageType(str, Enum):
    TEXT = "text"
    MEDIA_REF = "media_ref"
    DELETED = "deleted"
    STICKER = "sticker"
    SYSTEM = "system"


@dataclass
class ParsedMessage:
    """
    Uma mensagem parseada do .txt.

    timestamp é naive (sem tzinfo). A resolução de timezone é responsabilidade
    do módulo `temporal`, que cruza esta fonte com filename / EXIF / mtime.
    """

    line_number: int
    timestamp_raw: str
    timestamp: datetime
    sender: str | None  # None para mensagens de sistema
    content: str
    message_type: MessageType
    media_filename: str | None = None
    edited: bool = False
    flags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constantes — markers e regex compiladas
# ---------------------------------------------------------------------------

EDITED_MARKER = "<Esta mensagem foi editada>"
STICKER_MARKER = "<Figurinha omitida>"
DELETED_MARKERS = frozenset({
    "Esta mensagem foi apagada",
    "Você apagou esta mensagem",
})

# Left-to-Right Mark — iOS prefixa mensagens de sistema (chamadas, avisos
# de criptografia) e referências a mídia com este caractere invisível.
LRM = "\u200e"

IOS_SYSTEM_PREFIXES = (
    "Ligação de voz",
    "Ligação de vídeo",
    "Videochamada",
    "As mensagens e ligações são protegidas",
    "As mensagens e as chamadas são protegidas",
    "Chamada perdida",
)

DASH_HEADER_RE = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{2,4})\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(?:([^:]+):\s*)?(.*)$"
)
BRACKET_HEADER_RE = re.compile(
    r"^\[(\d{1,2}/\d{1,2}/\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?:([^:]+):\s*)?(.*)$"
)

MEDIA_ANEXADO_RE = re.compile(r"^<anexado:\s*(.+?)>$")
MEDIA_ARQUIVO_ANEXADO_RE = re.compile(r"^(.+?\.\w+)\s+\(arquivo anexado\)$")


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def parse_chat_file(txt_path: Path) -> list[ParsedMessage]:
    """
    Parse completo de um _chat.txt do WhatsApp (eager).

    Wrapper sobre :func:`iter_chat_messages` para callers que querem a
    lista pronta. Para arquivos grandes (>100 MB), prefira o iterador.

    Args:
        txt_path: caminho do arquivo .txt

    Returns:
        Lista de ParsedMessage em ordem de leitura.

    Raises:
        FileNotFoundError: se o arquivo não existir.
        ValueError: se a primeira linha de dados não casar com nenhum formato conhecido.
    """
    return list(iter_chat_messages(txt_path))


def iter_chat_messages(txt_path: Path):
    """
    Versão streaming de :func:`parse_chat_file` (Sessão 7 / #41).

    Lê o arquivo linha-a-linha e yielda :class:`ParsedMessage` à
    medida que cada mensagem é finalizada. RAM peak fica bounded
    pelo tamanho da maior mensagem individual (raramente >50KB),
    independente do tamanho total do .txt.

    Premissa de formato: o WhatsApp não mistura ``dash`` e
    ``bracket`` no mesmo export. Detecção é feita pela primeira
    linha que casa com algum header — em arquivo grande, o read-once
    necessário para detectar formato fica em janela curta.

    Yields:
        ParsedMessage em ordem de leitura.
    """
    encoding = _detect_encoding(txt_path)

    # Primeira passagem para detectar formato. Lemos linhas até que uma
    # case com algum header (geralmente a primeira linha não-vazia).
    fmt: str | None = None
    detection_buffer: list[str] = []
    with txt_path.open("r", encoding=encoding) as f:
        for raw in f:
            line = raw.rstrip("\n").replace(LRM, "")
            detection_buffer.append(line)
            if line.strip():
                fmt = _detect_format_from_lines([line])
                if fmt is not None:
                    break

    if fmt is None:
        return  # arquivo vazio ou sem formato reconhecido
    header_re = DASH_HEADER_RE if fmt == "dash" else BRACKET_HEADER_RE

    # Segunda passagem real, já com formato fixado. Reabrimos o arquivo
    # para garantir streaming verdadeiro (não acumulamos detection_buffer
    # — ele é pequeno por construção mas reler do começo é semanticamente
    # mais limpo e tem custo desprezível: 1 abertura extra).
    current: ParsedMessage | None = None
    with txt_path.open("r", encoding=encoding) as f:
        for i, raw in enumerate(f, start=1):
            line = raw.rstrip("\n").replace(LRM, "")
            m = header_re.match(line)
            if m:
                if current is not None:
                    _finalize(current)
                    yield current
                date_str, time_str, sender, content = (
                    m.group(1), m.group(2), m.group(3), m.group(4),
                )
                current = ParsedMessage(
                    line_number=i,
                    timestamp_raw=f"{date_str} {time_str}",
                    timestamp=_parse_timestamp(date_str, time_str),
                    sender=sender.strip() if sender else None,
                    content=content,
                    message_type=MessageType.TEXT,
                )
            elif current is not None:
                current.content += "\n" + line
            # linha órfã antes da primeira mensagem é ignorada
    if current is not None:
        _finalize(current)
        yield current


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _read_text(txt_path: Path) -> str:
    """
    Lê o arquivo tentando utf-8; fallback para latin-1 em exports antigos.

    Remove U+200E (Left-to-Right Mark) de todo o texto. LRM é caractere
    de apresentação usado pelo iOS WhatsApp para demarcar mensagens
    especiais (anexos, chamadas, avisos de sistema). Não tem valor
    semântico ou probatório — removê-lo na leitura evita falhas sutis
    em string matches downstream (classificação, filtros, comparação).
    """
    try:
        text = txt_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        log.warning("utf-8 falhou em %s; usando latin-1", txt_path)
        text = txt_path.read_text(encoding="latin-1")
    return text.replace(LRM, "")


def _detect_encoding(txt_path: Path) -> str:
    """
    Detecta encoding probando primeiro utf-8 (default WhatsApp), com
    fallback para latin-1 (exports antigos pt-BR no Android pré-2018).

    Probe lê apenas os primeiros ~64KB — o suficiente pra detectar
    UnicodeDecodeError sem materializar arquivo de GBs em RAM.
    """
    try:
        with txt_path.open("r", encoding="utf-8") as f:
            f.read(65536)
        return "utf-8"
    except UnicodeDecodeError:
        log.warning("utf-8 falhou em %s; usando latin-1", txt_path)
        return "latin-1"


def _detect_format_from_lines(lines: list[str]) -> str | None:
    """
    Detecta o formato pela primeira linha não-vazia que casa com um header.

    Retorna "dash", "bracket", ou None se o arquivo está vazio.
    Levanta ValueError se a primeira linha não-vazia não casar com nenhum formato.
    """
    for line in lines:
        if not line.strip():
            continue
        if DASH_HEADER_RE.match(line):
            return "dash"
        if BRACKET_HEADER_RE.match(line):
            return "bracket"
        raise ValueError(f"formato não reconhecido na primeira linha: {line!r}")
    return None


def _parse_timestamp(date_str: str, time_str: str) -> datetime:
    """pt-BR usa DD/MM, então dayfirst=True. Retorna datetime naive."""
    return dateutil_parse(f"{date_str} {time_str}", dayfirst=True)


def _finalize(msg: ParsedMessage) -> None:
    """
    Aplica detecção de tipo e flags após o conteúdo multi-linha estar completo.
    Mutação in-place: ordem importa (editada primeiro, depois tipo).
    """
    # 1. Marker de edição — detectar e remover antes de classificar tipo
    stripped = msg.content.rstrip()
    if stripped.endswith(EDITED_MARKER):
        msg.content = stripped[: -len(EDITED_MARKER)].rstrip()
        msg.edited = True

    # 1.5. Mensagem de sistema iOS — o iOS atribui sender a metadados do
    # sistema (chamadas, criptografia). Detectar pelo prefixo conhecido e
    # rebatizar como SYSTEM com sender=None. LRM já foi removido no
    # _read_text (ver docstring), então startswith compara texto limpo.
    if msg.sender is not None and any(
        msg.content.startswith(p) for p in IOS_SYSTEM_PREFIXES
    ):
        msg.message_type = MessageType.SYSTEM
        msg.sender = None
        return

    # 2. Sistema = sem remetente
    if msg.sender is None:
        msg.message_type = MessageType.SYSTEM
        return

    # 3. Figurinha
    if msg.content == STICKER_MARKER:
        msg.message_type = MessageType.STICKER
        return

    # 4. Apagada
    if msg.content in DELETED_MARKERS:
        msg.message_type = MessageType.DELETED
        return

    # 5. Mídia formato A: <anexado: NOME.ext>
    m = MEDIA_ANEXADO_RE.match(msg.content)
    if m:
        msg.message_type = MessageType.MEDIA_REF
        msg.media_filename = m.group(1).strip()
        return

    # 6. Mídia formato B: NOME.ext (arquivo anexado) — aceita espaços no nome
    m = MEDIA_ARQUIVO_ANEXADO_RE.match(msg.content)
    if m:
        msg.message_type = MessageType.MEDIA_REF
        msg.media_filename = m.group(1).strip()
        return

    # Default: TEXT (já é o default no construtor, mas explícito para clareza)
    msg.message_type = MessageType.TEXT
