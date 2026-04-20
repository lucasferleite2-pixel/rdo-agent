"""
Detector de qualidade de transcricao — Sprint 3 Camada 1.

Classifica cada transcricao Whisper em uma de 3 categorias:
  - 'coerente': texto aproveitavel pelo classificador sem revisao humana
  - 'suspeita': conteudo parcialmente aproveitavel, requer revisao humana
  - 'ilegivel': ruido (loops, alucinacoes); requer revisao humana ou rejeicao

A saida popula classifications.quality_flag + human_review_needed e define
o estado inicial do row (pending_classify se coerente, pending_review
caso contrario).

Segue padrao canonico do transcriber (retry 3x com backoff 1s+3s, logging
per-tentativa em api_calls, validacao lazy de OPENAI_API_KEY).

Custo esperado: ~USD 0.01 para 105 transcricoes da vault EVERALDO.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime

from rdo_agent.orchestrator import Task
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_text
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

MODEL = "gpt-4o-mini-2024-07-18"
TEMPERATURE = 0.0
COST_USD_PER_1K_INPUT = 0.00015
COST_USD_PER_1K_OUTPUT = 0.00060
RETRY_DELAYS_SEC: tuple[float, float] = (1.0, 3.0)

PROMPT_SYSTEM = """Voce eh um detector de qualidade de transcricao automatica em portugues brasileiro.

CONTEXTO
Audios de WhatsApp de canteiro de obra em Minas Gerais. Sotaque mineiro rural, fala coloquial, Word Error Rate (WER) baseline ~46%. Termos tecnicos esperados: ripa, tesoura, tela, granilite, telha, MIG, pilar, laje, cobertura, vergalhao. Termos comerciais esperados: orcamento, adiantamento, pagamento, PIX, acordo, prazo, medicao.

TAREFA
Classifique a transcricao em EXATAMENTE UMA categoria:
- "coerente": texto faz sentido em portugues, conteudo semantico identificavel, imperfeicoes de sotaque e regionalismos toleraveis. Nao precisa ser perfeita — precisa ser compreensivel por um humano lendo.
- "suspeita": texto tem passagens claramente incoerentes (palavras inventadas, mudancas abruptas de assunto, frases sem sentido), mas ha conteudo aproveitavel identificavel em parte da transcricao.
- "ilegivel": texto eh majoritariamente inutil — loops de palavras repetidas consecutivamente, frases sem sentido predominam, palavras inventadas em maioria, ou transcricao eh sentinel de erro.

REGRAS
- Texto vazio ou sentinel "# rdo-agent: sem fala detectada" -> "ilegivel"
- Mesma palavra repetida >10 vezes consecutivas -> "ilegivel"
- Um termo tecnico distorcido isoladamente (MIG/amiga, ripa/repa) NAO torna suspeita; regionalismo eh esperado
- Sotaque forte com trocas de pronomes (voce/ce, esta/ta, estou/to) NAO torna suspeita
- Se ha qualquer frase que um humano entenderia sem contexto externo, considere "coerente" ou no minimo "suspeita" — reserve "ilegivel" para o que eh realmente irrecuperavel

SAIDA
Responda APENAS em JSON valido, sem markdown, sem preambulo, sem backticks.
Formato exato:
{"flag": "coerente" | "suspeita" | "ilegivel", "reasoning": "uma frase curta justificando"}"""


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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


def _compute_cost_usd(tokens_input: int, tokens_output: int, model: str) -> float:
    if model != MODEL:
        log.warning(
            f"_compute_cost_usd: modelo inesperado {model}; "
            f"fallback para preco de {MODEL}."
        )
    return (
        tokens_input / 1000.0 * COST_USD_PER_1K_INPUT
        + tokens_output / 1000.0 * COST_USD_PER_1K_OUTPUT
    )


def _log_api_call(
    conn: sqlite3.Connection,
    *,
    obra: str,
    request_json: str,
    response_json: str | None,
    tokens_input: int | None,
    tokens_output: int | None,
    cost_usd: float | None,
    started_at: str,
    finished_at: str | None,
    error_message: str | None,
    error_type: str | None,
    latency_ms: int | None,
) -> int:
    request_hash = sha256_text(request_json)
    response_hash = sha256_text(response_json) if response_json else None
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
            obra, "openai", "chat.completions", request_hash, response_hash,
            request_json, response_json, tokens_input, tokens_output,
            cost_usd, started_at, finished_at, error_message,
            latency_ms, MODEL, error_type, _now_iso_utc(),
        ),
    )
    return cur.lastrowid


def _call_detector_with_retry(
    client,
    transcription_text: str,
    conn: sqlite3.Connection,
    obra: str,
) -> tuple[dict, int, int, int, int]:
    """
    Retorna (parsed_json, tokens_in, tokens_out, latency_ms, api_call_id_ok).
    Cada tentativa eh logada em api_calls (erro ou sucesso).
    """
    request_payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": PROMPT_SYSTEM},
            {"role": "user", "content": transcription_text},
        ],
        "temperature": TEMPERATURE,
        "response_format": {"type": "json_object"},
    }
    request_json = json.dumps(request_payload, ensure_ascii=False, sort_keys=True)

    last_exc: Exception | None = None

    for attempt in range(3):
        if attempt > 0:
            time.sleep(RETRY_DELAYS_SEC[attempt - 1])

        started_at = _now_iso_utc()
        t0 = time.monotonic()

        try:
            resp = client.chat.completions.create(**request_payload)
            latency_ms = int((time.monotonic() - t0) * 1000)
            finished_at = _now_iso_utc()

            tokens_input = resp.usage.prompt_tokens if resp.usage else 0
            tokens_output = resp.usage.completion_tokens if resp.usage else 0
            cost = _compute_cost_usd(tokens_input, tokens_output, MODEL)

            content = resp.choices[0].message.content or "{}"
            response_json = json.dumps(
                {
                    "content": content,
                    "usage": {
                        "prompt_tokens": tokens_input,
                        "completion_tokens": tokens_output,
                    },
                },
                ensure_ascii=False, sort_keys=True,
            )

            api_call_id = _log_api_call(
                conn,
                obra=obra, request_json=request_json, response_json=response_json,
                tokens_input=tokens_input, tokens_output=tokens_output,
                cost_usd=cost, started_at=started_at, finished_at=finished_at,
                error_message=None, error_type=None, latency_ms=latency_ms,
            )

            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"detector returned invalid JSON: {content[:160]!r}"
                ) from exc

            return parsed, tokens_input, tokens_output, latency_ms, api_call_id

        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            finished_at = _now_iso_utc()
            _log_api_call(
                conn,
                obra=obra, request_json=request_json, response_json=None,
                tokens_input=None, tokens_output=None, cost_usd=None,
                started_at=started_at, finished_at=finished_at,
                error_message=f"{type(exc).__name__}: {str(exc)[:200]}",
                error_type=_classify_error_type(exc),
                latency_ms=latency_ms,
            )
            last_exc = exc
            if not _is_retryable(exc) or attempt == 2:
                raise

    assert last_exc is not None
    raise last_exc


def detect_quality(
    conn: sqlite3.Connection,
    *,
    obra: str,
    transcription_file_id: str,
    transcription_text: str,
) -> dict:
    """
    Nivel baixo: chama detector + loga api_calls. NAO persiste em
    classifications (isso fica em detect_quality_handler).
    """
    client = _get_openai_client()
    parsed, tin, tout, latency_ms, api_call_id = _call_detector_with_retry(
        client, transcription_text, conn, obra,
    )

    flag = (parsed.get("flag") or "").strip().lower()
    if flag not in ("coerente", "suspeita", "ilegivel"):
        raise RuntimeError(f"detector returned unexpected flag: {flag!r}")

    reasoning = (parsed.get("reasoning") or "").strip()

    return {
        "flag": flag,
        "reasoning": reasoning,
        "api_call_id": api_call_id,
        "tokens_input": tin,
        "tokens_output": tout,
        "latency_ms": latency_ms,
    }


def detect_quality_handler(task: Task, conn: sqlite3.Connection) -> str | None:
    """
    Nivel alto (orchestrator-compatible).
    Payload esperado: {"transcription_file_id": "<file_id>"}
    """
    transcription_file_id = task.payload.get("transcription_file_id")
    if not transcription_file_id:
        raise ValueError("payload sem transcription_file_id")

    row = conn.execute(
        "SELECT text FROM transcriptions WHERE obra = ? AND file_id = ?",
        (task.obra, transcription_file_id),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"transcription nao encontrada: obra={task.obra} "
            f"file_id={transcription_file_id}"
        )

    transcription_text = row[0] or ""
    source_sha256 = sha256_text(transcription_text)

    existing = conn.execute(
        "SELECT id FROM classifications WHERE obra = ? AND source_file_id = ?",
        (task.obra, transcription_file_id),
    ).fetchone()
    if existing is not None:
        log.info(
            f"classifications ja existe para obra={task.obra} "
            f"file_id={transcription_file_id}; skip."
        )
        return f"classifications:{existing[0]}"

    result = detect_quality(
        conn,
        obra=task.obra,
        transcription_file_id=transcription_file_id,
        transcription_text=transcription_text,
    )

    flag = result["flag"]
    human_review_needed = 0 if flag == "coerente" else 1
    semantic_status = "pending_classify" if flag == "coerente" else "pending_review"

    cur = conn.execute(
        """
        INSERT INTO classifications (
            obra, source_file_id, source_type,
            quality_flag, quality_reasoning, human_review_needed,
            quality_api_call_id, quality_model,
            source_sha256, semantic_status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task.obra, transcription_file_id, "transcription",
            flag, result["reasoning"], human_review_needed,
            result["api_call_id"], MODEL,
            source_sha256, semantic_status,
            _now_iso_utc(), None,
        ),
    )
    classifications_id = cur.lastrowid
    conn.commit()
    return f"classifications:{classifications_id}"
