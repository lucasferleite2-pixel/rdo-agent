"""
Narrator — Sprint 5 Fase A.

Chama Sonnet 4.6 com dossier + prompts e retorna NarrationResult.
Espelha padrao canonico de ocr_extractor/financial_ocr:
  - retry 3x nativo (SDK Anthropic)
  - timeout 60s (narrativa eh maior que OCR — mais tempo razoavel)
  - logging per-tentativa em api_calls (provider='anthropic',
    endpoint='messages')
  - validacao lazy de ANTHROPIC_API_KEY
  - parse robusto da auto-avaliacao JSON ao final da narrativa

NarrationResult:
  markdown_text: narrativa em markdown (inclui bloco self_assessment)
  markdown_body: so a narrativa (sem bloco JSON)
  self_assessment: dict do bloco {"self_assessment": {...}}
  model: "claude-sonnet-4-6"
  prompt_version: "narrator_v1"
  api_call_id: id da row em api_calls
  cost_usd: custo calculado
  prompt_tokens / completion_tokens
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from rdo_agent.forensic_agent.prompts import (
    NARRATOR_SYSTEM_PROMPT_V1,
    NARRATOR_USER_TEMPLATE,
)
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_text
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

MODEL: str = "claude-sonnet-4-6"
PROMPT_VERSION: str = "narrator_v1"
TEMPERATURE: float = 0.1
MAX_TOKENS: int = 4096
ANTHROPIC_TIMEOUT_SEC: float = 60.0
ANTHROPIC_MAX_RETRIES: int = 3
RETRY_DELAYS_SEC: tuple[float, float] = (1.0, 3.0)

# Pricing Sonnet 4.6 (oficial Anthropic, USD por 1M tokens).
# Input: $3.00 / Output: $15.00. Podem mudar — validar periodicamente.
PRICING_USD_PER_TOKEN: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input":  3.00 / 1_000_000,
        "output": 15.00 / 1_000_000,
    },
}


@dataclass
class NarrationResult:
    """Resultado de uma narracao — dados estruturados do modelo."""

    markdown_text: str
    markdown_body: str
    self_assessment: dict
    model: str
    prompt_version: str
    api_call_id: int | None
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    is_malformed: bool = False
    malformed_reason: str | None = None


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _get_anthropic_client():
    """
    Retorna cliente Anthropic com timeout/retry nativos.

    Raises:
        RuntimeError: se ANTHROPIC_API_KEY ausente.
    """
    key = config.get().anthropic_api_key
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY ausente. Configure via .env na raiz do projeto "
            "(ver .env.example). Obtenha chave em "
            "https://console.anthropic.com/settings/keys."
        )
    from anthropic import Anthropic
    return Anthropic(
        api_key=key,
        timeout=ANTHROPIC_TIMEOUT_SEC,
        max_retries=ANTHROPIC_MAX_RETRIES,
    )


def _classify_error_type(exc: Exception) -> str:
    """Traduz excecoes Anthropic em strings pra api_calls.error_type."""
    import anthropic

    if isinstance(exc, anthropic.APIConnectionError):
        return "connection"
    if isinstance(exc, anthropic.RateLimitError):
        return "rate_limit"
    if isinstance(exc, anthropic.APITimeoutError):
        return "timeout"
    if isinstance(exc, anthropic.AuthenticationError):
        return "auth_error"
    if isinstance(exc, anthropic.NotFoundError):
        return "not_found"
    if isinstance(exc, anthropic.BadRequestError):
        return "bad_request"
    return "api_error"


def _is_retryable(exc: Exception) -> bool:
    import anthropic
    return isinstance(
        exc,
        (anthropic.APIConnectionError, anthropic.RateLimitError,
         anthropic.APITimeoutError),
    )


def _compute_cost_usd(
    prompt_tokens: int, completion_tokens: int, model: str,
) -> float:
    if model not in PRICING_USD_PER_TOKEN:
        log.warning("modelo %s sem preco conhecido; reportando 0.0", model)
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
            obra, "anthropic", "messages",
            request_hash, response_hash,
            request_json, response_json,
            prompt_tokens, completion_tokens, cost_usd,
            started_at, finished_at, error_message,
            latency_ms, MODEL, error_type, _now_iso_utc(),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _extract_self_assessment(markdown: str) -> tuple[dict, str]:
    """
    Extrai bloco self_assessment ao final do markdown e retorna:
      (self_assessment_dict, markdown_body_sem_bloco)

    O modelo deve retornar ```json { "self_assessment": {...} } ``` ao final.
    Se nao encontrar, retorna ({}, markdown_completo) e flagga como malformado
    no caller.
    """
    # Busca ultimo bloco ```json ... ```
    pattern = r"```json\s*(\{[\s\S]*?\})\s*```\s*$"
    m = re.search(pattern, markdown.strip())
    if not m:
        return {}, markdown
    try:
        parsed = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}, markdown

    sa = parsed.get("self_assessment") if isinstance(parsed, dict) else None
    if not isinstance(sa, dict):
        return {}, markdown

    # Remove bloco do body
    body = markdown[: m.start()].rstrip()
    return sa, body


def _build_malformed_result(
    raw_text: str, reason: str,
    prompt_tokens: int, completion_tokens: int,
    api_call_id: int | None, cost_usd: float,
) -> NarrationResult:
    return NarrationResult(
        markdown_text=raw_text,
        markdown_body=raw_text,
        self_assessment={},
        model=MODEL,
        prompt_version=PROMPT_VERSION,
        api_call_id=api_call_id,
        cost_usd=cost_usd,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        is_malformed=True,
        malformed_reason=reason,
    )


def _call_anthropic_with_retry(
    client, dossier_json: str, scope: str,
    conn: sqlite3.Connection, obra: str,
) -> tuple[str, int, int, int]:
    """
    Invoca Anthropic messages com retry 3x (backoff 1s + 3s).
    Retorna (text_content, prompt_tokens, completion_tokens, api_call_id).
    """
    user_content = NARRATOR_USER_TEMPLATE.format(
        dossier_json=dossier_json, scope=scope,
    )
    request_body = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "system": NARRATOR_SYSTEM_PROMPT_V1,
        "messages": [{"role": "user", "content": user_content}],
    }
    request_json = json.dumps(
        request_body, ensure_ascii=False, sort_keys=True,
    )
    request_hash = sha256_text(request_json)

    last_exc: Exception | None = None
    for attempt in range(3):
        started_dt = datetime.now(UTC)
        started_at = started_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        try:
            response = client.messages.create(**request_body)
        except Exception as exc:
            finished_dt = datetime.now(UTC)
            latency_ms = int((finished_dt - started_dt).total_seconds() * 1000)
            err_type = _classify_error_type(exc)
            _log_api_call(
                conn,
                obra=obra, request_hash=request_hash,
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
            log.warning(
                "Narrator tentativa %d falhou (%s); retry em %.1fs",
                attempt + 1, err_type, RETRY_DELAYS_SEC[attempt],
            )
            time.sleep(RETRY_DELAYS_SEC[attempt])
            continue

        # Sucesso
        finished_dt = datetime.now(UTC)
        latency_ms = int((finished_dt - started_dt).total_seconds() * 1000)

        # Anthropic response: .content eh lista de TextBlock; pegamos o 1o
        text_parts = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
        text_content = "".join(text_parts)

        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
        cost_usd = _compute_cost_usd(prompt_tokens, completion_tokens, MODEL)

        response_json_str = json.dumps(
            {
                "text": text_content,
                "usage": {
                    "input_tokens": prompt_tokens,
                    "output_tokens": completion_tokens,
                },
                "stop_reason": getattr(response, "stop_reason", None),
            },
            ensure_ascii=False, sort_keys=True,
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
        conn.commit()
        return text_content, prompt_tokens, completion_tokens, api_call_id

    assert last_exc is not None
    raise last_exc


def narrate(
    dossier: dict,
    conn: sqlite3.Connection,
) -> NarrationResult:
    """
    Nivel publico: serializa dossier, chama Sonnet 4.6, parsa self_assessment.

    Args:
        dossier: dict retornado por build_day_dossier ou build_obra_overview.
        conn: conexao com api_calls pra logging.

    Returns:
        NarrationResult completo. Se malformed (sem self_assessment),
        is_malformed=True e assessment={}.

    Raises:
        RuntimeError: ANTHROPIC_API_KEY ausente.
        anthropic.* errors nao-retryaveis propagam.
    """
    client = _get_anthropic_client()
    dossier_json = json.dumps(dossier, ensure_ascii=False, indent=2)
    obra = dossier.get("obra", "")
    scope = dossier.get("scope", "")

    text, pt, ct, api_call_id = _call_anthropic_with_retry(
        client, dossier_json, scope, conn, obra,
    )
    cost = _compute_cost_usd(pt, ct, MODEL)

    self_assessment, body = _extract_self_assessment(text)

    is_malformed = not self_assessment
    reason = "missing_self_assessment_block" if is_malformed else None

    return NarrationResult(
        markdown_text=text,
        markdown_body=body,
        self_assessment=self_assessment,
        model=MODEL,
        prompt_version=PROMPT_VERSION,
        api_call_id=api_call_id,
        cost_usd=cost,
        prompt_tokens=pt,
        completion_tokens=ct,
        is_malformed=is_malformed,
        malformed_reason=reason,
    )


__all__ = [
    "ANTHROPIC_MAX_RETRIES",
    "ANTHROPIC_TIMEOUT_SEC",
    "MODEL",
    "PRICING_USD_PER_TOKEN",
    "PROMPT_VERSION",
    "NarrationResult",
    "narrate",
]
