"""
OCR Extractor — Camada 0.5 (Sprint 4 Op8 — pipeline OCR-first).

Primeiro passo obrigatorio em qualquer imagem. Separa duas semanticas
que antes o Vision tentava fazer num so prompt:

  1. "Documento fotografado" (comprovante PIX, nota fiscal, oficio,
     protocolo) — requer extracao LITERAL de texto. OCR ganha.
  2. "Foto de cena real" (canteiro, material, estrutura) — requer
     descricao semantica. Vision ganha.

Decidido via OCR + heuristica `word_count >= OCR_TEXT_THRESHOLD` +
flag `is_document` retornada pelo modelo.

Espelha padrao canonico do `visual_analyzer/__init__.py`:
  - retry 3x com backoff 1s+3s
  - logging per-tentativa em api_calls
  - sentinel JSON para respostas malformadas
  - validacao lazy de OPENAI_API_KEY

Entrega publica:
  - `extract_text_from_image(image_path, obra, conn) -> (OCRResult, api_call_id)`
  - `OCRResult` dataclass com text, word_count, is_document,
    doc_type_hint, confidence, cost_usd
  - `ocr_first_handler(task, conn)` — orchestrator-compatible
    (Sprint 4 Op8 Fase 5)
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rdo_agent.ocr_extractor.prompts import (
    FINANCIAL_DOC_TYPE_HINTS,
    OCR_EXTRACT_SYSTEM,
    OCR_EXTRACT_USER_TEMPLATE,
)
from rdo_agent.orchestrator import (
    Task,
    TaskStatus,
    TaskType,
    enqueue,
)
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_file, sha256_text
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

MODEL: str = "gpt-4o-mini"
TEMPERATURE = 0.0
RESPONSE_FORMAT = {"type": "json_object"}
RETRY_DELAYS_SEC: tuple[float, float] = (1.0, 3.0)

# Limiar em palavras acima do qual uma imagem eh considerada "documento"
# (pipeline OCR-first enche classifications com source_type='document'
# em vez de chamar Vision). Ajustavel via env OCR_TEXT_THRESHOLD.
OCR_TEXT_THRESHOLD: int = int(os.getenv("OCR_TEXT_THRESHOLD", "15"))

# Pricing OpenAI gpt-4o-mini (igual ao quality_detector / semantic_classifier).
PRICING_USD_PER_TOKEN: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15 / 1_000_000, "output": 0.60 / 1_000_000},
    "gpt-4o":      {"input": 2.50 / 1_000_000, "output": 10.00 / 1_000_000},
}

MIME_TYPES: dict[str, str] = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
    ".gif":  "image/gif",
}

REQUIRED_OCR_FIELDS: tuple[str, ...] = (
    "text",
    "word_count",
    "char_count",
    "is_document",
    "doc_type_hint",
    "confidence",
)


@dataclass
class OCRResult:
    """Resultado estruturado de uma chamada OCR sobre uma imagem."""

    text: str
    word_count: int
    char_count: int
    is_document: bool
    doc_type_hint: str | None
    confidence: float
    cost_usd: float
    is_malformed: bool = False
    malformed_reason: str | None = None

    @property
    def is_financial_document(self) -> bool:
        """True se o hint aponta para comprovante bancario."""
        return (
            self.is_document
            and self.doc_type_hint in FINANCIAL_DOC_TYPE_HINTS
        )

    @property
    def has_sufficient_text(self) -> bool:
        """True se word_count >= OCR_TEXT_THRESHOLD."""
        return self.word_count >= OCR_TEXT_THRESHOLD


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# Sprint 4 Op11 Divida #9 — timeout/retry nativos do SDK OpenAI.
# Default 600s do SDK pendurava workers por 10 min em API degradada
# (observado Op9 Fase 5). Valores calibrados: 30s por call (Vision
# responde em 2-15s normal; 30s cobre p99 + ainda aborta rapido quando
# degradado) + 3 retries automaticos pelo SDK pra transient errors.
OPENAI_CLIENT_TIMEOUT_SEC: float = 30.0
OPENAI_CLIENT_MAX_RETRIES: int = 3


def _get_openai_client():
    key = config.get().openai_api_key
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY ausente. Configure via .env na raiz do projeto "
            "(ver .env.example). Obtenha chave em "
            "https://platform.openai.com/api-keys."
        )
    from openai import OpenAI
    return OpenAI(
        api_key=key,
        timeout=OPENAI_CLIENT_TIMEOUT_SEC,
        max_retries=OPENAI_CLIENT_MAX_RETRIES,
    )


def _guess_mime_type(path: Path) -> str:
    return MIME_TYPES.get(path.suffix.lower(), "image/jpeg")


def _encode_image_data_url(image_path: Path) -> str:
    mime = _guess_mime_type(image_path)
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _classify_error_type(exc: Exception) -> str:
    import openai

    if isinstance(exc, openai.APIConnectionError):
        return "connection"
    if isinstance(exc, openai.RateLimitError):
        return "rate_limit"
    if isinstance(exc, openai.APITimeoutError):
        return "timeout"
    if isinstance(exc, openai.AuthenticationError):
        return "auth_error"
    if isinstance(exc, openai.NotFoundError):
        return "not_found"
    if isinstance(exc, openai.BadRequestError):
        return "bad_request"
    return "api_error"


def _is_retryable(exc: Exception) -> bool:
    import openai
    return isinstance(
        exc,
        (openai.APIConnectionError, openai.RateLimitError, openai.APITimeoutError),
    )


def _compute_cost_usd(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    if model not in PRICING_USD_PER_TOKEN:
        log.warning("modelo %s sem preco conhecido; reportando 0.0", model)
        return 0.0
    p = PRICING_USD_PER_TOKEN[model]
    return prompt_tokens * p["input"] + completion_tokens * p["output"]


def _validate_ocr_schema(payload: object) -> tuple[bool, str]:
    """Verifica se payload eh dict com REQUIRED_OCR_FIELDS + tipos validos."""
    if not isinstance(payload, dict):
        return False, f"not_a_dict:{type(payload).__name__}"
    missing = [k for k in REQUIRED_OCR_FIELDS if k not in payload]
    if missing:
        return False, f"missing_fields:{','.join(missing)}"
    if not isinstance(payload.get("text"), str):
        return False, "text_not_string"
    if not isinstance(payload.get("word_count"), int):
        return False, "word_count_not_int"
    if not isinstance(payload.get("is_document"), bool):
        return False, "is_document_not_bool"
    conf = payload.get("confidence")
    try:
        conf_f = float(conf)
    except (TypeError, ValueError):
        return False, "confidence_not_numeric"
    if not 0.0 <= conf_f <= 1.0:
        return False, f"confidence_out_of_range:{conf_f}"
    return True, "ok"


def _log_api_call(
    conn: sqlite3.Connection,
    *,
    obra: str,
    request_hash: str,
    request_json: str,
    response_hash: str | None,
    response_json: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    cost_usd: float,
    started_at: str,
    finished_at: str,
    latency_ms: int,
    error_message: str | None,
    error_type: str | None,
    model: str = MODEL,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO api_calls (
            obra, provider, endpoint, request_hash, response_hash,
            request_json, response_json, tokens_input, tokens_output,
            cost_usd, started_at, finished_at, error_message,
            latency_ms, model, error_type, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            obra, "openai", "chat.completions.create",
            request_hash, response_hash,
            request_json, response_json,
            prompt_tokens, completion_tokens, cost_usd,
            started_at, finished_at, error_message,
            latency_ms, model, error_type, _now_iso_utc(),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _call_ocr_with_retry(
    client,
    image_path: Path,
    obra: str,
    conn: sqlite3.Connection,
) -> tuple[dict, int]:
    """
    Invoca gpt-4o-mini Vision pra OCR com retry 3x (backoff 1s+3s).

    Retorna (parsed_json_da_resposta, api_call_id). parsed_json_da_resposta
    eh o dict literal retornado pelo modelo (ja decoded). Se schema invalido
    ou decode falhar, o helper retorna um sentinel dict com _sentinel
    marker — caller decide como tratar.
    """
    image_data_url = _encode_image_data_url(image_path)
    messages = [
        {"role": "system", "content": OCR_EXTRACT_SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": OCR_EXTRACT_USER_TEMPLATE},
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url, "detail": "high"},
                },
            ],
        },
    ]
    request_body_for_hash = {
        "model": MODEL,
        "temperature": TEMPERATURE,
        "response_format": RESPONSE_FORMAT,
        "image_filename": image_path.name,
        "image_sha256": sha256_file(image_path),
        "image_detail": "high",
        "prompt_kind": "ocr_extract",
    }
    request_json = json.dumps(
        request_body_for_hash, sort_keys=True, ensure_ascii=False,
    )
    request_hash = sha256_text(request_json)

    last_exc: Exception | None = None
    for attempt in range(3):
        started_dt = datetime.now(UTC)
        started_at = started_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        try:
            response = client.chat.completions.create(
                model=MODEL,
                temperature=TEMPERATURE,
                response_format=RESPONSE_FORMAT,
                messages=messages,
            )
        except Exception as exc:
            finished_dt = datetime.now(UTC)
            latency_ms = int((finished_dt - started_dt).total_seconds() * 1000)
            err_type = _classify_error_type(exc)
            _log_api_call(
                conn,
                obra=obra,
                request_hash=request_hash,
                request_json=request_json,
                response_hash=None,
                response_json=None,
                prompt_tokens=None,
                completion_tokens=None,
                cost_usd=0.0,
                started_at=started_at,
                finished_at=finished_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                latency_ms=latency_ms,
                error_message=f"{err_type}: {exc}",
                error_type=err_type,
            )
            conn.commit()
            last_exc = exc
            if not _is_retryable(exc) or attempt >= 2:
                raise
            delay = RETRY_DELAYS_SEC[attempt]
            log.warning(
                "OCR tentativa %d falhou (%s); retry em %.1fs",
                attempt + 1, err_type, delay,
            )
            time.sleep(delay)
            continue

        finished_dt = datetime.now(UTC)
        latency_ms = int((finished_dt - started_dt).total_seconds() * 1000)
        response_dict = (
            response.model_dump() if hasattr(response, "model_dump") else dict(response)
        )
        response_json = json.dumps(
            response_dict, sort_keys=True, ensure_ascii=False, default=str,
        )
        response_hash = sha256_text(response_json)

        usage = response_dict.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        cost_usd = _compute_cost_usd(prompt_tokens, completion_tokens, MODEL)

        api_call_id = _log_api_call(
            conn,
            obra=obra,
            request_hash=request_hash,
            request_json=request_json,
            response_hash=response_hash,
            response_json=response_json,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            started_at=started_at,
            finished_at=finished_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            latency_ms=latency_ms,
            error_message=None,
            error_type=None,
        )
        return response_dict, api_call_id

    assert last_exc is not None
    raise last_exc


def _extract_content_string(response_dict: dict) -> str:
    choices = response_dict.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content") or ""


def _build_sentinel(reason: str, raw: str) -> dict:
    """Sentinel retornado quando JSON malformado ou schema invalido."""
    return {
        "_sentinel": "malformed_ocr_response",
        "reason": reason,
        "raw_response": raw,
        "text": "",
        "word_count": 0,
        "char_count": 0,
        "is_document": False,
        "doc_type_hint": None,
        "confidence": 0.0,
    }


def extract_text_from_image(
    image_path: Path,
    obra: str,
    conn: sqlite3.Connection,
) -> tuple[OCRResult, int]:
    """
    Nivel publico baixo: chama OCR + loga em api_calls. NAO persiste
    em documents/classifications (isso fica no ocr_first_handler).

    Returns:
        (OCRResult, api_call_id)

    Raises:
        RuntimeError: OPENAI_API_KEY ausente
        openai.AuthenticationError / BadRequestError / NotFoundError:
            propagadas (nao retryaveis)
        RuntimeError: se 3 tentativas retryaveis esgotarem
    """
    client = _get_openai_client()
    response_dict, api_call_id = _call_ocr_with_retry(
        client, image_path, obra, conn,
    )
    content_str = _extract_content_string(response_dict)

    try:
        parsed = json.loads(content_str) if content_str else None
    except json.JSONDecodeError as exc:
        parsed = None
        sentinel = _build_sentinel(
            f"json_decode_error:{exc.msg}", content_str,
        )
        log.warning(
            "OCR JSON invalido: %s (reason=%s)",
            image_path.name, sentinel["reason"],
        )
        return _result_from_sentinel(sentinel, 0.0), api_call_id

    if parsed is None:
        sentinel = _build_sentinel("empty_content", content_str)
        return _result_from_sentinel(sentinel, 0.0), api_call_id

    ok, reason = _validate_ocr_schema(parsed)
    if not ok:
        sentinel = _build_sentinel(reason, content_str)
        log.warning(
            "OCR schema invalido: %s (reason=%s)", image_path.name, reason,
        )
        return _result_from_sentinel(sentinel, 0.0), api_call_id

    # Sucesso: converte dict validado em OCRResult.
    # cost_usd aqui eh 0.0 pq ja foi logado em api_calls.
    # Para retornar o cost real pro caller, re-lemos da api_calls row.
    cost_row = conn.execute(
        "SELECT cost_usd FROM api_calls WHERE id = ?", (api_call_id,),
    ).fetchone()
    cost_usd = float(cost_row[0] or 0.0) if cost_row else 0.0

    return OCRResult(
        text=str(parsed["text"]),
        word_count=int(parsed["word_count"]),
        char_count=int(parsed["char_count"]),
        is_document=bool(parsed["is_document"]),
        doc_type_hint=parsed.get("doc_type_hint"),
        confidence=float(parsed["confidence"]),
        cost_usd=cost_usd,
        is_malformed=False,
        malformed_reason=None,
    ), api_call_id


def _result_from_sentinel(sentinel: dict, cost_usd: float) -> OCRResult:
    return OCRResult(
        text=sentinel.get("text") or "",
        word_count=int(sentinel.get("word_count") or 0),
        char_count=int(sentinel.get("char_count") or 0),
        is_document=bool(sentinel.get("is_document") or False),
        doc_type_hint=sentinel.get("doc_type_hint"),
        confidence=float(sentinel.get("confidence") or 0.0),
        cost_usd=cost_usd,
        is_malformed=True,
        malformed_reason=sentinel.get("reason"),
    )


# ---------------------------------------------------------------------------
# Sprint 4 Op8 Fase 5 — handler orchestrator-compatible
# ---------------------------------------------------------------------------


def _is_video_frame(conn: sqlite3.Connection, file_id: str) -> bool:
    """
    Sprint 4 Op11 Divida #11 — True se `file_id` eh frame extraido de
    video.

    Heuristica: segue `files.derived_from` e checa se o parent tem
    `file_type='video'`. Frames tipicamente nao contem texto e chamar
    OCR neles eh desperdicio — economiza ~R\\$ 0.005/frame em vaults
    com muitos videos.
    """
    row = conn.execute(
        """
        SELECT parent.file_type
        FROM files f
        LEFT JOIN files parent ON parent.file_id = f.derived_from
        WHERE f.file_id = ?
        """,
        (file_id,),
    ).fetchone()
    if row is None:
        return False
    return (row["file_type"] or "").lower() == "video"


def ocr_first_handler(task: Task, conn: sqlite3.Connection) -> str | None:
    """
    Handler principal do pipeline OCR-first.

    Fluxo:
      0. (Op11 #11) Se imagem eh frame de video, PULA OCR e enfileira
         VISUAL_ANALYSIS direto — economia de calls gpt-4o-mini.
      1. Le imagem de task.payload['file_id'] + 'file_path'
      2. Roda OCR (extract_text_from_image)
      3. Decide rota:
         - DOCUMENTO (is_document=True AND word_count >= OCR_TEXT_THRESHOLD):
             * Persiste texto extraido em documents + files derivado .txt
             * Se is_financial_document: chama financial_ocr +
               salva em financial_records
             * Cria linha em classifications source_type='document'
               com quality_flag='coerente' (OCR tem confidence-real),
               semantic_status='pending_classify'. Classifier normal
               pode rodar depois.
             * NAO enfileira VISUAL_ANALYSIS
         - FOTO (caso contrario — incluindo sentinel malformed):
             * Enfileira VISUAL_ANALYSIS (fluxo atual preservado)
      4. Retorna result_ref informativo

    Nao reprocessa imagens ja processadas: guardrail via
    check em files.derived_from onde derivation_method LIKE 'ocr_first%'.
    """
    # Lazy import pra evitar dependencia circular em testes
    from rdo_agent.financial_ocr import (
        extract_financial_fields,
        save_financial_record,
    )

    payload = task.payload
    image_file_id = payload["file_id"]
    image_rel_path = payload["file_path"]

    obra = task.obra

    # Op11 #11 — frames de video pulam OCR: enfileira VA direto.
    if _is_video_frame(conn, image_file_id):
        enqueue(
            conn,
            Task(
                id=None, task_type=TaskType.VISUAL_ANALYSIS,
                payload={
                    "file_id": image_file_id,
                    "file_path": image_rel_path,
                },
                status=TaskStatus.PENDING, depends_on=[],
                obra=obra, created_at="", priority=5,
            ),
        )
        conn.commit()
        return "routed:visual_analysis (skipped_ocr:video_frame)"

    vault_path = config.get().vault_path(obra)
    image_path = vault_path / image_rel_path

    src_row = conn.execute(
        "SELECT file_id, sha256, referenced_by_message, "
        "timestamp_resolved, timestamp_source "
        "FROM files WHERE file_id = ?",
        (image_file_id,),
    ).fetchone()
    if src_row is None:
        raise RuntimeError(
            f"imagem {image_file_id} nao encontrada em files (obra={obra})"
        )

    ocr_result, ocr_api_call_id = extract_text_from_image(image_path, obra, conn)

    # Route: FOTO ou OCR falhou -> VISUAL_ANALYSIS
    if ocr_result.is_malformed or not (
        ocr_result.is_document and ocr_result.has_sufficient_text
    ):
        enqueue(
            conn,
            Task(
                id=None, task_type=TaskType.VISUAL_ANALYSIS,
                payload={
                    "file_id": image_file_id,
                    "file_path": image_rel_path,
                },
                status=TaskStatus.PENDING, depends_on=[],
                obra=obra, created_at="", priority=5,
            ),
        )
        conn.commit()
        return (
            f"routed:visual_analysis "
            f"(word_count={ocr_result.word_count},"
            f"is_document={ocr_result.is_document},"
            f"malformed={ocr_result.is_malformed})"
        )

    # Route: DOCUMENTO — persiste texto + cria document row
    now = _now_iso_utc()
    txt_filename = f"{image_path.name}.ocr.txt"
    txt_path = vault_path / "20_transcriptions" / txt_filename
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(ocr_result.text, encoding="utf-8")
    txt_sha = sha256_file(txt_path)
    txt_file_id = f"f_{txt_sha[:12]}"
    txt_rel_path = f"20_transcriptions/{txt_filename}"

    conn.execute(
        """
        INSERT OR IGNORE INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            derived_from, derivation_method, referenced_by_message,
            timestamp_resolved, timestamp_source, semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            txt_file_id, obra, txt_rel_path, "text",
            txt_sha, txt_path.stat().st_size,
            image_file_id, f"ocr_first:{MODEL}",
            src_row["referenced_by_message"],
            src_row["timestamp_resolved"],
            src_row["timestamp_source"],
            "awaiting_classification", now,
        ),
    )

    conn.execute(
        """
        INSERT OR IGNORE INTO documents (
            obra, file_id, text, page_count, extraction_method,
            api_call_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            obra, txt_file_id, ocr_result.text, 1,
            f"ocr_first:{MODEL}", ocr_api_call_id, now,
        ),
    )

    conn.execute(
        """
        INSERT OR IGNORE INTO media_derivations (
            obra, source_file_id, derived_file_id, derivation_method,
            created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (obra, image_file_id, txt_file_id, f"ocr_first:{MODEL}", now),
    )

    # Cria classification para o texto OCR processado pelo classificador
    # semantico normal (9 categorias existentes) — idempotente via UNIQUE.
    quality_flag = "coerente" if ocr_result.confidence >= 0.5 else "suspeita"
    review_needed = 1 if ocr_result.confidence < 0.3 else 0
    semantic_status = "pending_review" if review_needed else "pending_classify"
    conn.execute(
        """
        INSERT OR IGNORE INTO classifications (
            obra, source_file_id, source_type,
            quality_flag, quality_reasoning, human_review_needed,
            quality_api_call_id, quality_model,
            source_sha256, semantic_status, created_at
        ) VALUES (?, ?, 'document', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            obra, txt_file_id,
            quality_flag,
            f"ocr_first confidence={ocr_result.confidence:.2f}, "
            f"doc_type_hint={ocr_result.doc_type_hint}",
            review_needed,
            ocr_api_call_id, MODEL,
            sha256_text(ocr_result.text),
            semantic_status, now,
        ),
    )

    # Se financeiro, extrai campos estruturados + salva em financial_records.
    # Falha da extracao nao aborta o handler — apenas loga.
    if ocr_result.is_financial_document:
        try:
            record, fin_api_call_id = extract_financial_fields(
                ocr_result.text, obra, conn,
            )
            save_financial_record(
                conn,
                obra=obra,
                source_file_id=image_file_id,
                raw_ocr_text=ocr_result.text,
                record=record,
                api_call_id=fin_api_call_id,
            )
        except Exception as exc:
            log.warning(
                "financial_ocr falhou para %s (%s); documento salvo sem "
                "extracao estrutural: %s",
                image_path.name, ocr_result.doc_type_hint, exc,
            )

    conn.execute(
        "UPDATE files SET semantic_status = 'ocr_extracted' WHERE file_id = ?",
        (image_file_id,),
    )
    conn.commit()

    return (
        f"routed:document txt_file_id={txt_file_id} "
        f"word_count={ocr_result.word_count} "
        f"hint={ocr_result.doc_type_hint}"
    )


__all__ = [
    "MODEL",
    "OCR_TEXT_THRESHOLD",
    "PRICING_USD_PER_TOKEN",
    "REQUIRED_OCR_FIELDS",
    "OCRResult",
    "extract_text_from_image",
    "ocr_first_handler",
]
