"""
Financial OCR — Sprint 4 Op8.

Toma o texto bruto extraido pelo `ocr_extractor` em um comprovante
financeiro (PIX/TED/boleto/nota/recibo) e estrutura os campos
tabulares via segunda chamada gpt-4o-mini com prompt dedicado.

Fluxo (chamado pelo `ocr_first_handler`):

    raw_text (OCR output)
         │
         ▼
    extract_financial_fields(raw_text)  <- chama LLM estrutural
         │
         ▼
    FinancialRecord (dataclass)
         │
         ▼
    save_financial_record(conn, ...)    <- INSERT em financial_records

Separado do `ocr_extractor` porque:
  - Usa prompt diferente (extracao tabular, nao OCR)
  - Pode ser chamado independentemente (re-processar texto bruto sem
    redo OCR)
  - Testes de _parse_currency_to_cents sao puros (sem FakeClient)
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from rdo_agent.ocr_extractor.prompts import (
    FINANCIAL_STRUCTURE_SYSTEM,
    FINANCIAL_STRUCTURE_USER_TEMPLATE,
)
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_text
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

MODEL: str = "gpt-4o-mini"
TEMPERATURE = 0.0
RESPONSE_FORMAT = {"type": "json_object"}
RETRY_DELAYS_SEC: tuple[float, float] = (1.0, 3.0)

PRICING_USD_PER_TOKEN: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15 / 1_000_000, "output": 0.60 / 1_000_000},
}

REQUIRED_FINANCIAL_FIELDS: tuple[str, ...] = (
    "doc_type",
    "valor_centavos",
    "moeda",
    "data_transacao",
    "hora_transacao",
    "pagador_nome",
    "pagador_doc",
    "recebedor_nome",
    "recebedor_doc",
    "chave_pix",
    "descricao",
    "instituicao_origem",
    "instituicao_destino",
    "confidence",
)

VALID_DOC_TYPES: tuple[str, ...] = (
    "pix", "ted", "doc", "boleto", "nota_fiscal", "recibo", "outro",
)


@dataclass
class FinancialRecord:
    """Record estruturado extraido de um comprovante financeiro."""

    doc_type: str
    valor_centavos: int | None
    moeda: str
    data_transacao: str | None
    hora_transacao: str | None
    pagador_nome: str | None
    pagador_doc: str | None
    recebedor_nome: str | None
    recebedor_doc: str | None
    chave_pix: str | None
    descricao: str | None
    instituicao_origem: str | None
    instituicao_destino: str | None
    confidence: float


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_currency_to_cents(s: str | None) -> int | None:
    """
    Converte string de valor monetario brasileiro em centavos (int).

    Aceita variantes:
        "R$ 3.500,00"   -> 350000
        "R$ 3.500"      -> 350000
        "3500.00"       -> 350000  (ponto decimal estilo en-US)
        "3500,00"       -> 350000
        "3500"          -> 350000
        "3500,5"        -> 350050  (1 casa decimal)
        "R$ 30"         -> 3000
        "  R$  42,30 "  -> 4230
        ""              -> None
        "invalid"       -> None
        None            -> None

    Heuristica para separador decimal:
      - se contem tanto "," quanto ".":
          - "," aparece depois do ultimo "." -> "," eh decimal (pt-BR)
          - "." aparece depois da ultima "," -> "." eh decimal (en-US)
      - se contem so ",": eh decimal
      - se contem so ".": eh decimal (en-US) — desde que nao seja milhares
        separator claro (3+ digitos apos ponto). Na pratica, "3.500"
        sem vigula eh ambiguo; tratamos como centavos-ausentes (3500).
    """
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None

    # Remove "R$", espacos, e quaisquer caracteres nao-numericos exceto "," "."
    cleaned = re.sub(r"[^\d,.\-]", "", s)
    if not cleaned:
        return None

    negative = cleaned.startswith("-")
    if negative:
        cleaned = cleaned[1:]

    has_comma = "," in cleaned
    has_dot = "." in cleaned

    try:
        if has_comma and has_dot:
            # Decimal eh o que vier por ultimo
            last_comma = cleaned.rfind(",")
            last_dot = cleaned.rfind(".")
            if last_comma > last_dot:
                # pt-BR: 3.500,00
                int_part = cleaned[:last_comma].replace(".", "").replace(",", "")
                dec_part = cleaned[last_comma + 1:]
            else:
                # en-US: 3,500.00
                int_part = cleaned[:last_dot].replace(",", "").replace(".", "")
                dec_part = cleaned[last_dot + 1:]
        elif has_comma:
            # so virgula — pt-BR com decimal
            last_comma = cleaned.rfind(",")
            int_part = cleaned[:last_comma]
            dec_part = cleaned[last_comma + 1:]
            # "3,500" (milhares estilo pt-BR raro sem decimal)
            # Se dec_part tem 3 digitos e nao ha outro separador anterior,
            # eh ambiguo — assumimos decimal (pior caso +2%/100%, mas
            # situacao rara em BR onde sempre se usa ","  para decimal)
        elif has_dot:
            # so ponto
            last_dot = cleaned.rfind(".")
            int_part = cleaned[:last_dot]
            dec_part = cleaned[last_dot + 1:]
            # "3.500" sem decimal pt-BR — se dec_part tem 3 digitos,
            # provavelmente milhares: tratamos como int
            if len(dec_part) == 3 and int_part:
                int_part = int_part + dec_part
                dec_part = ""
        else:
            int_part = cleaned
            dec_part = ""

        # Normaliza decimal para 2 casas (padding ou truncate)
        if not dec_part:
            cents = int(int_part) * 100
        else:
            if len(dec_part) == 1:
                dec_part += "0"
            elif len(dec_part) > 2:
                dec_part = dec_part[:2]
            cents = int(int_part or "0") * 100 + int(dec_part)
    except ValueError:
        return None

    if negative:
        cents = -cents
    return cents


def _get_openai_client():
    key = config.get().openai_api_key
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY ausente. Configure via .env na raiz do projeto "
            "(ver .env.example). Obtenha chave em "
            "https://platform.openai.com/api-keys."
        )
    from openai import OpenAI
    return OpenAI(api_key=key)


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
        return 0.0
    p = PRICING_USD_PER_TOKEN[model]
    return prompt_tokens * p["input"] + completion_tokens * p["output"]


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
            latency_ms, MODEL, error_type, _now_iso_utc(),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _call_financial_structure_with_retry(
    client, raw_text: str, obra: str, conn: sqlite3.Connection,
) -> tuple[dict, int]:
    user_content = FINANCIAL_STRUCTURE_USER_TEMPLATE.format(raw_text=raw_text)
    request_body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": FINANCIAL_STRUCTURE_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        "temperature": TEMPERATURE,
        "response_format": RESPONSE_FORMAT,
    }
    request_json = json.dumps(request_body, ensure_ascii=False, sort_keys=True)
    request_hash = sha256_text(request_json)

    last_exc: Exception | None = None
    for attempt in range(3):
        started_dt = datetime.now(UTC)
        started_at = started_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        try:
            response = client.chat.completions.create(**request_body)
        except Exception as exc:
            finished_dt = datetime.now(UTC)
            latency_ms = int((finished_dt - started_dt).total_seconds() * 1000)
            err_type = _classify_error_type(exc)
            _log_api_call(
                conn,
                obra=obra,
                request_hash=request_hash,
                request_json=request_json,
                response_hash=None, response_json=None,
                prompt_tokens=None, completion_tokens=None,
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
            time.sleep(RETRY_DELAYS_SEC[attempt])
            continue

        finished_dt = datetime.now(UTC)
        latency_ms = int((finished_dt - started_dt).total_seconds() * 1000)

        content = ""
        try:
            content = response.choices[0].message.content or ""
        except (AttributeError, IndexError):
            content = ""

        # tokens/cost
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        cost_usd = _compute_cost_usd(prompt_tokens, completion_tokens, MODEL)

        response_json_str = json.dumps(
            {"content": content, "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }}, ensure_ascii=False, sort_keys=True,
        )

        api_call_id = _log_api_call(
            conn,
            obra=obra,
            request_hash=request_hash,
            request_json=request_json,
            response_hash=sha256_text(response_json_str),
            response_json=response_json_str,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            started_at=started_at,
            finished_at=finished_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            latency_ms=latency_ms,
            error_message=None,
            error_type=None,
        )

        try:
            parsed = json.loads(content) if content else {}
        except json.JSONDecodeError:
            parsed = {}
        return parsed, api_call_id

    assert last_exc is not None
    raise last_exc


def _coerce_record(parsed: dict) -> FinancialRecord:
    """
    Converte dict do LLM em FinancialRecord, com saneamento:
      - valor_centavos aceita int OR string: se string, usa _parse_currency_to_cents
      - doc_type invalido -> 'outro'
      - confidence forca range [0,1]
      - todos os outros campos str/None
    """
    def _opt_str(v) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    # doc_type
    doc_type = (parsed.get("doc_type") or "outro").lower()
    if doc_type not in VALID_DOC_TYPES:
        doc_type = "outro"

    # valor_centavos
    valor = parsed.get("valor_centavos")
    if isinstance(valor, int):
        valor_cents = valor
    elif isinstance(valor, float):
        valor_cents = int(round(valor))
    elif isinstance(valor, str):
        valor_cents = _parse_currency_to_cents(valor)
    else:
        valor_cents = None

    # confidence
    try:
        conf = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    # moeda (default BRL)
    moeda = _opt_str(parsed.get("moeda")) or "BRL"

    return FinancialRecord(
        doc_type=doc_type,
        valor_centavos=valor_cents,
        moeda=moeda,
        data_transacao=_opt_str(parsed.get("data_transacao")),
        hora_transacao=_opt_str(parsed.get("hora_transacao")),
        pagador_nome=_opt_str(parsed.get("pagador_nome")),
        pagador_doc=_opt_str(parsed.get("pagador_doc")),
        recebedor_nome=_opt_str(parsed.get("recebedor_nome")),
        recebedor_doc=_opt_str(parsed.get("recebedor_doc")),
        chave_pix=_opt_str(parsed.get("chave_pix")),
        descricao=_opt_str(parsed.get("descricao")),
        instituicao_origem=_opt_str(parsed.get("instituicao_origem")),
        instituicao_destino=_opt_str(parsed.get("instituicao_destino")),
        confidence=conf,
    )


def extract_financial_fields(
    raw_text: str,
    obra: str,
    conn: sqlite3.Connection,
) -> tuple[FinancialRecord, int]:
    """
    Chama gpt-4o-mini com FINANCIAL_STRUCTURE_SYSTEM sobre texto bruto
    de OCR. Retorna FinancialRecord + api_call_id.

    Raises:
        RuntimeError se OPENAI_API_KEY ausente.
        openai.* errors nao retryaveis propagam.
    """
    client = _get_openai_client()
    parsed, api_call_id = _call_financial_structure_with_retry(
        client, raw_text, obra, conn,
    )
    record = _coerce_record(parsed)
    return record, api_call_id


def save_financial_record(
    conn: sqlite3.Connection,
    *,
    obra: str,
    source_file_id: str,
    raw_ocr_text: str,
    record: FinancialRecord,
    api_call_id: int | None,
) -> int | None:
    """
    INSERT em financial_records. Idempotente via UNIQUE(obra, source_file_id)
    — se ja existe, retorna None e loga info.

    Returns:
        id da row inserida, ou None se ja existia.
    """
    now = _now_iso_utc()
    try:
        cur = conn.execute(
            """
            INSERT INTO financial_records (
                obra, source_file_id, doc_type, valor_centavos, moeda,
                data_transacao, hora_transacao,
                pagador_nome, pagador_doc, recebedor_nome, recebedor_doc,
                chave_pix, descricao,
                instituicao_origem, instituicao_destino,
                raw_ocr_text, confidence, api_call_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obra, source_file_id, record.doc_type, record.valor_centavos,
                record.moeda,
                record.data_transacao, record.hora_transacao,
                record.pagador_nome, record.pagador_doc,
                record.recebedor_nome, record.recebedor_doc,
                record.chave_pix, record.descricao,
                record.instituicao_origem, record.instituicao_destino,
                raw_ocr_text, record.confidence, api_call_id, now,
            ),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError as exc:
        log.info(
            "financial_record ja existe para %s (%s); skip: %s",
            obra, source_file_id, exc,
        )
        return None


__all__ = [
    "MODEL",
    "REQUIRED_FINANCIAL_FIELDS",
    "VALID_DOC_TYPES",
    "FinancialRecord",
    "_coerce_record",
    "_parse_currency_to_cents",
    "extract_financial_fields",
    "save_financial_record",
]
