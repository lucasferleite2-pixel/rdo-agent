"""
Parser do _chat.txt do WhatsApp — Camada 1.

Converte o arquivo .txt exportado do WhatsApp em uma lista de mensagens
estruturadas, identificando remetente, timestamp, conteúdo e referências a mídias.

Desafios conhecidos:
- Formatos de data variam (pt-BR pode ser DD/MM/YYYY ou DD/MM/YY)
- Mensagens podem ter múltiplas linhas (continuam na próxima linha)
- Anexos seguem padrão "<anexado: NOME.ext>" em pt-BR
- Mensagens apagadas: "Esta mensagem foi apagada"
- Figurinhas: "<Figurinha omitida>"
- Mensagens editadas: texto com "<Esta mensagem foi editada>"
- Respostas citadas vêm em formato específico
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class MessageType(str, Enum):
    TEXT = "text"
    MEDIA_REF = "media_ref"  # mensagem referencia uma mídia anexada
    DELETED = "deleted"
    STICKER = "sticker"
    SYSTEM = "system"  # "Fulano saiu", "Criptografia fim-a-fim", etc.


@dataclass
class ParsedMessage:
    """Uma mensagem parseada do .txt."""

    line_number: int  # linha no .txt original (para auditoria)
    timestamp_raw: str  # string original do .txt, sem conversão
    timestamp: datetime  # parseada para datetime com timezone
    sender: str | None  # None para mensagens de sistema
    content: str
    message_type: MessageType
    media_filename: str | None = None  # se MEDIA_REF, o nome do arquivo anexado
    edited: bool = False
    flags: list[str] = field(default_factory=list)  # anotações adicionais


def parse_chat_file(txt_path: Path) -> list[ParsedMessage]:
    """
    Parse completo de um _chat.txt do WhatsApp.

    Args:
        txt_path: caminho do arquivo .txt

    Returns:
        Lista de ParsedMessage em ordem de leitura (cronológica no arquivo)

    Raises:
        FileNotFoundError: se o arquivo não existir
        UnicodeDecodeError: se não conseguir decodificar (tentar utf-8 e latin-1)

    Implementação sugerida:
        1. Ler arquivo com encoding utf-8 (fallback latin-1)
        2. Iterar linhas acumulando multi-linhas em buffer
        3. Regex para identificar início de nova mensagem (começa com data)
        4. Extrair timestamp, sender, conteúdo
        5. Identificar tipo (mídia, apagada, figurinha, sistema)
        6. Se mídia: extrair filename do padrão "<anexado: ...>"
    """
    # TODO Sprint 1
    raise NotImplementedError


# Padrões regex esperados (documentação para implementação):
#
# Início de mensagem pt-BR (exemplos reais):
#   12/03/2026 09:45 - Nome Sobrenome: conteúdo
#   12/03/26 9:45 - +55 11 99999-9999: conteúdo
#   [12/03/2026, 09:45:32] Nome: conteúdo   (variação com colchetes)
#
# Mídia anexada:
#   IMG-20260312-WA0015.jpg (arquivo anexado)
#   <anexado: VID-20260312-WA0007.mp4>
#   PTT-20260312-WA0007.opus (arquivo anexado)     (áudio "push-to-talk")
#
# Mensagens de sistema (sem remetente):
#   12/03/2026 09:00 - As mensagens e as chamadas são protegidas...
#   12/03/2026 09:00 - Fulano entrou usando o link...
