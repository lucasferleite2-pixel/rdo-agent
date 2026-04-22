"""
Ingestor de mensagens de texto puro WhatsApp — Sprint 4 Op1.

Converte mensagens WhatsApp sem anexo (sem media_ref) em entradas no
pipeline de classificacao semantica. NAO passa pelo detector de
qualidade — texto escrito tem qualidade por definicao (nao ha WER).

Decisao de design (ver ADR-003 pendente):
  briefing Op1 pediu source_file_id=NULL, mas coluna eh NOT NULL. Usamos
  linha sintetica em `files` por mensagem (file_type='message',
  file_path='') pra respeitar constraint NOT NULL + FK + autorizacao
  "1 ALTER TABLE" (apenas source_message_id em classifications).

Idempotente: re-rodar nao duplica classifications (check via
source_message_id UNIQUE-logicamente-por-obra). Mensagens ja classificadas
sao puladas.

NAO chama API. Apenas ingere linhas. O classificador semantico roda
depois sobre as linhas pending_classify.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from rdo_agent.utils.hashing import sha256_text
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

DERIVATION_METHOD = "text_message_from_whatsapp"
QUALITY_FLAG = "coerente"
QUALITY_REASONING = "texto escrito, sem WER"
SOURCE_TYPE = "text_message"
SEMANTIC_STATUS = "pending_classify"
SYNTHETIC_FILE_TYPE = "message"

# Mensagens cujo content eh metadado WhatsApp sem conteudo aproveitavel
NOISE_PATTERNS: tuple[str, ...] = (
    "As mensagens e ligações são protegidas",
    "Ligação de voz",
    "Chamada perdida",
    "Esta mensagem foi apagada",
)


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _synthetic_file_id(message_id: str) -> str:
    """
    file_id sintetico unico por mensagem. Deterministico: sha256(message_id)
    truncado. Diferente do padrao f_ (arquivos reais) pra clara distincao.
    """
    return f"m_{sha256_text(message_id)[:12]}"


def _build_noise_filter() -> tuple[str, tuple[str, ...]]:
    """Retorna (where_clause_extra, params) para excluir mensagens-ruido."""
    clauses = ["content NOT LIKE ?" for _ in NOISE_PATTERNS]
    params = tuple(f"%{p}%" for p in NOISE_PATTERNS)
    return " AND " + " AND ".join(clauses), params


def ingest_text_messages(
    conn: sqlite3.Connection, obra: str,
) -> dict:
    """
    Ingere mensagens de texto puro (sem media_ref) de `messages` como
    candidatas a classificacao semantica.

    Para cada mensagem elegivel nao-previamente-ingerida:
      1. Computa sha256(content)
      2. INSERT OR IGNORE em files (linha sintetica, file_type='message')
      3. INSERT em classifications com source_type='text_message',
         source_message_id=message_id, quality_flag='coerente',
         semantic_status='pending_classify'

    Args:
        conn: conexao SQLite com schema migrado (source_message_id presente)
        obra: isolamento por obra

    Returns:
        dict com chaves:
          - candidates: mensagens elegiveis (antes de filtro de duplicatas)
          - inserted: novas classifications criadas
          - skipped_existing: ja ingeridas em sessao anterior
          - skipped_empty: content vazio apos strip
    """
    noise_where, noise_params = _build_noise_filter()
    sql = f"""
        SELECT message_id, timestamp_whatsapp, sender, content
        FROM messages
        WHERE obra = ?
          AND is_deleted = 0
          AND (media_ref IS NULL OR media_ref = '')
          {noise_where}
        ORDER BY timestamp_whatsapp
    """
    candidates = conn.execute(sql, (obra, *noise_params)).fetchall()

    now = _now_iso_utc()
    inserted = 0
    skipped_existing = 0
    skipped_empty = 0

    for row in candidates:
        message_id = row["message_id"]
        content = (row["content"] or "").strip()
        if not content:
            skipped_empty += 1
            continue

        existing = conn.execute(
            "SELECT 1 FROM classifications WHERE obra = ? AND source_message_id = ?",
            (obra, message_id),
        ).fetchone()
        if existing is not None:
            skipped_existing += 1
            continue

        content_sha = sha256_text(content)
        file_id = _synthetic_file_id(message_id)

        conn.execute(
            """
            INSERT OR IGNORE INTO files (
                file_id, obra, file_path, file_type, sha256, size_bytes,
                derived_from, derivation_method, referenced_by_message,
                timestamp_resolved, timestamp_source,
                semantic_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id, obra, "", SYNTHETIC_FILE_TYPE, content_sha,
                len(content.encode("utf-8")),
                None, DERIVATION_METHOD, message_id,
                row["timestamp_whatsapp"], "whatsapp_txt",
                "awaiting_classification", now,
            ),
        )

        conn.execute(
            """
            INSERT INTO classifications (
                obra, source_file_id, source_type, source_message_id,
                quality_flag, quality_reasoning, human_review_needed,
                source_sha256, semantic_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obra, file_id, SOURCE_TYPE, message_id,
                QUALITY_FLAG, QUALITY_REASONING, 0,
                content_sha, SEMANTIC_STATUS, now,
            ),
        )
        inserted += 1

    conn.commit()
    log.info(
        "ingest_text_messages obra=%s candidates=%d inserted=%d "
        "skipped_existing=%d skipped_empty=%d",
        obra, len(candidates), inserted, skipped_existing, skipped_empty,
    )
    return {
        "candidates": len(candidates),
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "skipped_empty": skipped_empty,
    }


__all__ = [
    "DERIVATION_METHOD",
    "QUALITY_FLAG",
    "SEMANTIC_STATUS",
    "SOURCE_TYPE",
    "SYNTHETIC_FILE_TYPE",
    "ingest_text_messages",
]
