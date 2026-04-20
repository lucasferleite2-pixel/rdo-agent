"""
Classificador semantico — Sprint 3 Camada 3.

Atribui a cada classification (`pending_classify`) 1-2 categorias
semanticas pre-calibradas, com confidence e reasoning. Popula
`classifications.categories/confidence_model/reasoning/...` e muda
`semantic_status` para `classified`.

Segue padrao canonico do quality_detector (retry 3x backoff 1s+3s,
logging per-tentativa em api_calls, validacao lazy de OPENAI_API_KEY).

Entrada: prioriza `classifications.human_corrected_text` (Fase 2);
fallback `transcriptions.text` original. NUNCA classifica linhas em
`semantic_status='rejected'`. Idempotente: re-rodar sobre linha ja
`classified` e no-op.

Custo esperado: ~USD 0.30 para 105 transcricoes (~72 pending_classify
pos-detector + revisao).
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

VALID_CATEGORIES: tuple[str, ...] = (
    "negociacao_comercial",
    "pagamento",
    "cronograma",
    "especificacao_tecnica",
    "solicitacao_servico",
    "material",
    "reporte_execucao",
    "off_topic",
    "ilegivel",
)

PROMPT_SYSTEM = """Voce eh um classificador semantico de transcricoes de audio de canteiro de obra em Minas Gerais.

CONTEXTO ESTAVEL
- Lucas = contratante (Vale Nobre / Ideia Marketing)
- Everaldo = prestador terceirizado (pessoa fisica, equipe de 2-3 ajudantes)
- Obra = EE Santa Quiteria (CODESC 75817), contrato SEE-MG
- Escopo contratual: cobertura (tesouras + ripas + telhas) + granilite do piso
- Transcricao tem WER baseline ~46% (sotaque mineiro rural, fala coloquial)

CATEGORIAS VALIDAS (9 codigos exatos, use tal qual)
1. negociacao_comercial — discussao de valor, termos, proposta, contrapropostas, acordo
2. pagamento — mecanica de pagamento (adiantamento, forma, PIX, chave, comprovante, quando recebe)
3. cronograma — prazos, datas, encontros, checagens de andamento, "amanha eu vou"
4. especificacao_tecnica — como o trabalho deve ser feito (medidas, metodo, dimensoes)
5. solicitacao_servico — pedido explicito de execucao de um servico pontual
6. material — insumo como objeto principal (qual telha, qual ripa, quantidade)
7. reporte_execucao — relato do que foi feito no canteiro (passou, terminou, colocou)
8. off_topic — conversa fora do escopo contratual (cumprimento, conversa pessoal)
9. ilegivel — transcricao degradada, nao classificavel (loops, frases sem sentido)

REGRAS DE FRONTEIRA
- "vamos fechar em 10" -> negociacao_comercial (discutindo valor)
- "me manda a chave pix" -> pagamento (mecanica)
- "daqui a umas cinco horas eu libero" -> cronograma
- "nao pode ficar mais baixo que dois metros" -> especificacao_tecnica
- "ce nao consegue ir la hoje soltar os cano?" -> solicitacao_servico
- "pedido de 21 telhas de 5 e 10" -> material
- "ja coloquei a tesoura" -> reporte_execucao
- Cumprimento puro / smalltalk -> off_topic
- Loop "nao nao nao" ou frase incoerente -> ilegivel
- Adiantamento envolve pagamento E negociacao_comercial -> multi-label 2 categorias

EXEMPLOS CALIBRADOS (extraidos da obra real, anonimizados)
Texto: "Ô Lux, pode sim, é que se tu tiver vindo, tu me liga. Eu dei o tapa aqui fora, eu vou lá na hora."
-> {"categories":["cronograma"],"confidence":0.85,"reasoning":"Everaldo combinando encontro"}

Texto: "Me manda a chave fixa, me manda o dinheiro que eu trouxe aí."
-> {"categories":["pagamento"],"confidence":0.9,"reasoning":"pedido de chave PIX"}

Texto: "O Lucas, você acha que você vai conseguir mandar uns R$ XXXX, R$ XXXX como eu te falei pra você, dos 11 lá?"
-> {"categories":["pagamento","negociacao_comercial"],"confidence":0.85,"reasoning":"pedido de adiantamento dentro de negociacao"}

Texto: "o louco so te mandar medida da telha ai voce vai pedir 21 telha de 5 e 10 e 21 ate de 8 e 20"
-> {"categories":["material","especificacao_tecnica"],"confidence":0.8,"reasoning":"pedido de telha com medidas especificas"}

Texto: "Ô, doutor, eu fiz os cálculos que eu tinha pensado que você tinha falado 11 mais os 13. Vamos fechar em 10?"
-> {"categories":["negociacao_comercial"],"confidence":0.9,"reasoning":"contraproposta de valor final"}

SAIDA
Responda APENAS em JSON valido, sem markdown, sem preambulo, sem backticks.
Formato exato:
{"categories": ["primary", "optional_secondary"], "confidence": 0.0-1.0, "reasoning": "frase curta"}

Limite: maximo 2 categorias. Primary primeiro. Se single-label, array com 1 elemento."""


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


def _call_classifier_with_retry(
    client,
    text: str,
    conn: sqlite3.Connection,
    obra: str,
) -> tuple[dict, int, int, int, int]:
    """Retorna (parsed_json, tokens_in, tokens_out, latency_ms, api_call_id)."""
    request_payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": PROMPT_SYSTEM},
            {"role": "user", "content": text},
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
                    f"classifier returned invalid JSON: {content[:160]!r}"
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


def _validate_response(parsed: dict) -> tuple[list[str], float, str]:
    """
    Valida resposta do classificador contra contrato.

    Regras:
      - `categories` eh lista de 1-2 strings, todas ∈ VALID_CATEGORIES
      - `confidence` float ∈ [0.0, 1.0]
      - `reasoning` string (pode ser vazia)

    Raise RuntimeError com detalhe se invalido.
    """
    cats = parsed.get("categories")
    if not isinstance(cats, list) or not 1 <= len(cats) <= 2:
        raise RuntimeError(
            f"classifier returned invalid categories: {cats!r} (expected 1-2 strings)"
        )
    for c in cats:
        if c not in VALID_CATEGORIES:
            raise RuntimeError(
                f"classifier returned unknown category: {c!r} "
                f"(valid: {VALID_CATEGORIES})"
            )

    conf = parsed.get("confidence")
    try:
        conf_f = float(conf)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"classifier returned non-numeric confidence: {conf!r}"
        ) from exc
    if not 0.0 <= conf_f <= 1.0:
        raise RuntimeError(
            f"classifier returned confidence out of range: {conf_f}"
        )

    reasoning = parsed.get("reasoning") or ""
    if not isinstance(reasoning, str):
        reasoning = str(reasoning)

    return list(cats), conf_f, reasoning.strip()


def classify_text(
    conn: sqlite3.Connection,
    *,
    obra: str,
    text: str,
) -> dict:
    """
    Nivel baixo: chama classificador + loga api_calls. NAO persiste em
    classifications (isso fica em classify_handler).
    """
    client = _get_openai_client()
    parsed, tin, tout, latency_ms, api_call_id = _call_classifier_with_retry(
        client, text, conn, obra,
    )
    categories, confidence, reasoning = _validate_response(parsed)
    return {
        "categories": categories,
        "confidence": confidence,
        "reasoning": reasoning,
        "api_call_id": api_call_id,
        "tokens_input": tin,
        "tokens_output": tout,
        "latency_ms": latency_ms,
    }


def classify_handler(task: Task, conn: sqlite3.Connection) -> str | None:
    """
    Nivel alto (orchestrator-compatible).
    Payload esperado: {"classifications_id": <int>}

    Logica:
      1. Busca row de classifications pelo id; raise se nao existir
      2. Se semantic_status='rejected' -> skip (retorna sem chamar API)
      3. Se semantic_status='classified' -> skip (idempotencia)
      4. Texto = human_corrected_text (se setado) | transcriptions.text
      5. classify_text() -> popula classifications via UPDATE
      6. Transiciona semantic_status -> 'classified', updated_at
    """
    classifications_id = task.payload.get("classifications_id")
    if classifications_id is None:
        raise ValueError("payload sem classifications_id")

    row = conn.execute(
        """SELECT id, source_file_id, semantic_status, human_corrected_text,
                  human_reviewed
           FROM classifications WHERE obra = ? AND id = ?""",
        (task.obra, classifications_id),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"classification nao encontrada: obra={task.obra} "
            f"id={classifications_id}"
        )

    cls_id = row[0]
    source_file_id = row[1]
    status = row[2]
    human_corrected = row[3]

    if status == "rejected":
        log.info(
            f"classification id={cls_id} esta rejected; skip classifier."
        )
        return f"classifications:{cls_id}:skipped_rejected"

    if status == "classified":
        log.info(
            f"classification id={cls_id} ja classified; idempotencia — skip."
        )
        return f"classifications:{cls_id}:skipped_classified"

    if human_corrected:
        text = human_corrected
    else:
        t_row = conn.execute(
            "SELECT text FROM transcriptions WHERE obra = ? AND file_id = ?",
            (task.obra, source_file_id),
        ).fetchone()
        if t_row is None:
            raise RuntimeError(
                f"transcription nao encontrada para source_file_id={source_file_id}"
            )
        text = t_row[0] or ""

    result = classify_text(conn, obra=task.obra, text=text)

    now = _now_iso_utc()
    conn.execute(
        """
        UPDATE classifications SET
            categories = ?,
            confidence_model = ?,
            reasoning = ?,
            classifier_api_call_id = ?,
            classifier_model = ?,
            semantic_status = 'classified',
            updated_at = ?
        WHERE id = ?
        """,
        (
            json.dumps(result["categories"], ensure_ascii=False),
            result["confidence"],
            result["reasoning"],
            result["api_call_id"],
            MODEL,
            now,
            cls_id,
        ),
    )
    conn.commit()
    return f"classifications:{cls_id}"
