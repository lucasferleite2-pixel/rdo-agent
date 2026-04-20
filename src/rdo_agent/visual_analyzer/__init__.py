"""
Analisador Visual GPT-4 Vision — Camada 1 (Sprint 2 §Fase 3).

Espelha o padrão do transcriber: dois níveis públicos, persistência em
files + media_derivations, sentinel em disco para respostas JSON mal-
formadas (contrato do banco preservado: visual_analyses.analysis_json
recebe o mesmo sentinel JSON, mantendo shape parseável).

Decisões arquiteturais (alinhadas à retrospectiva da Fase 2):

  1. SDK `openai>=1.30` fixado (já em pyproject.toml).
  2. System prompt em pt-BR para contexto de canteiro; `temperature=0`
     para reprodutibilidade exigida pelo laudo probatório;
     `response_format={"type": "json_object"}` força saída JSON.
  3. Schema SQL intocado — tabela `visual_analyses` já existia
     (Blueprint §7.2). Nenhum ALTER TABLE novo nesta fase.
  4. Sentinel textual para JSON mal-formado ou incompleto — mesmo
     motivo do transcriber (sha256 estável, auditoria humana).
  5. Classificação de erros idêntica: APIConnectionError /
     RateLimitError / APITimeoutError retryable; AuthenticationError /
     BadRequestError / NotFoundError propagam.
  6. `api_calls` é log (cada tentativa vira row); `files` /
     `visual_analyses` / `media_derivations` são dado operacional
     (check-and-insert garante idempotência).

Arquitetura de teste (4 camadas, docs/SPRINT2_PLAN.md §4):
  - Unitários: monkeypatch _get_openai_client → FakeClient.
  - Golden fixture: tests/fixtures/vision_golden_response.json
    (capturada — commit 230374f).
  - Smoke manual + E2E: rodam contra GPT-4o Vision real.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from rdo_agent.orchestrator import Task
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_file, sha256_text
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

# Modelo configurável via env VISION_MODEL (SPRINT2_PLAN §3). Default
# gpt-4o-mini — ~US$ 0.003/imagem, equilíbrio custo/qualidade para canteiro.
MODEL: str = os.getenv("VISION_MODEL", "gpt-4o-mini")
TEMPERATURE = 0.0
RESPONSE_FORMAT = {"type": "json_object"}
RETRY_DELAYS_SEC: tuple[float, float] = (1.0, 3.0)  # antes das tentativas 2 e 3

# Campos obrigatórios do JSON estruturado retornado pelo modelo
# (SPRINT2_PLAN §Q1 + §Fase 3). Ordem preservada para determinismo
# de hashing do response.
REQUIRED_FIELDS: tuple[str, ...] = (
    "elementos_construtivos",
    "atividade_em_curso",
    "condicoes_ambiente",
    "observacoes_tecnicas",
)

# Pricing oficial da OpenAI em USD por token (platform.openai.com/pricing).
# Atualização manual quando a OpenAI muda tabela; warning se modelo
# não-mapeado for usado (cost=0 ao invés de quebrar).
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

SYSTEM_PROMPT = (
    "Você é um engenheiro civil analisando fotos de canteiro de obra no Brasil. "
    "Descreva APENAS o que é visível na imagem. Se não tem certeza de algum "
    "elemento, marque como 'não identificado'. NUNCA invente detalhes. "
    "Responda exclusivamente em JSON válido, em português do Brasil, com "
    "exatamente as 4 chaves: elementos_construtivos, atividade_em_curso, "
    "condicoes_ambiente, observacoes_tecnicas. Cada valor é uma string "
    "descritiva (200-2000 caracteres somados)."
)

USER_PROMPT = (
    "Analise a foto deste canteiro de obra e retorne o JSON com os 4 campos "
    "obrigatórios. Seja específico sobre materiais, estruturas, atividades "
    "em andamento, clima/iluminação visíveis e qualquer observação técnica "
    "relevante (riscos, não-conformidades, etapas concluídas)."
)


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _get_openai_client():
    """
    Retorna cliente OpenAI. Sem cache — o custo de instanciação é
    irrelevante frente ao HTTP do Vision, e caching complica isolamento
    de testes (cada monkeypatch de config não propagaria).

    Raises:
        RuntimeError: OPENAI_API_KEY ausente. Mensagem orientativa
            aponta para .env.example + dashboard da OpenAI.
    """
    key = config.get().openai_api_key
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY ausente. Configure via .env na raiz do projeto "
            "(ver .env.example). Obtenha chave em "
            "https://platform.openai.com/api-keys."
        )
    from openai import OpenAI
    return OpenAI(api_key=key)


def _guess_mime_type(path: Path) -> str:
    """Fallback para image/jpeg quando extensão desconhecida."""
    return MIME_TYPES.get(path.suffix.lower(), "image/jpeg")


def _encode_image_data_url(image_path: Path) -> str:
    """Lê bytes da imagem e codifica como data URL base64 para envio ao Vision."""
    mime = _guess_mime_type(image_path)
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _build_malformed_sentinel(
    image_file_id: str,
    image_rel_path: str,
    image_sha256: str,
    raw_content: str,
    reason: str,
) -> dict:
    """
    Marcador JSON para respostas do Vision sem schema válido. Preserva
    contrato do banco (visual_analyses.analysis_json parseável como JSON)
    e inclui source_sha256 para garantir unicidade do file_id derivado
    mesmo quando múltiplas imagens devolvem sentinel. Paralelo ao
    _build_empty_sentinel_audio do transcriber.
    """
    return {
        "_sentinel": "malformed_json_response",
        "reason": reason,
        "source_file_id": image_file_id,
        "source_path": image_rel_path,
        "source_sha256": image_sha256,
        "model": MODEL,
        "raw_response": raw_content,
        # Campos obrigatórios preenchidos como não-identificados para
        # queries que presumem schema — classificador da Sprint 3 pode
        # filtrar por _sentinel != null e tratar como rejeição.
        "elementos_construtivos": "não identificado",
        "atividade_em_curso": "não identificado",
        "condicoes_ambiente": "não identificado",
        "observacoes_tecnicas": f"sentinel: {reason}",
    }


def _classify_error_type(exc: Exception) -> str:
    """Traduz exceção do SDK em string curta para api_calls.error_type.
    Ordem importa: subclasses antes de superclasses."""
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
    """APIConnectionError, RateLimitError, APITimeoutError são transientes."""
    import openai

    return isinstance(
        exc,
        (openai.APIConnectionError, openai.RateLimitError, openai.APITimeoutError),
    )


def _compute_cost_usd(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    """Custo por tokens (input + output). Modelos sem pricing mapeado retornam 0.0."""
    if model not in PRICING_USD_PER_TOKEN:
        log.warning("modelo %s sem preço conhecido; reportando 0.0", model)
        return 0.0
    p = PRICING_USD_PER_TOKEN[model]
    return prompt_tokens * p["input"] + completion_tokens * p["output"]


def _validate_schema(payload: object) -> tuple[bool, str]:
    """
    Verifica se payload é dict com todas as REQUIRED_FIELDS não-vazias.

    Retorna (ok, reason) — reason curto identifica motivo da falha
    para logar no sentinel. Útil no teste de JSON mal-formado.
    """
    if not isinstance(payload, dict):
        return False, f"not_a_dict:{type(payload).__name__}"
    missing = [k for k in REQUIRED_FIELDS if k not in payload]
    if missing:
        return False, f"missing_fields:{','.join(missing)}"
    empty = [k for k in REQUIRED_FIELDS if not payload.get(k)]
    if empty:
        return False, f"empty_fields:{','.join(empty)}"
    total_chars = sum(len(str(payload.get(k, ""))) for k in REQUIRED_FIELDS)
    if total_chars < 100:
        return False, f"response_too_short:{total_chars}_chars"
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
) -> int:
    """INSERT em api_calls, retorna api_call_id (lastrowid)."""
    now = _now_iso_utc()
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
            obra,
            "openai",
            "chat.completions.create",
            request_hash,
            response_hash,
            request_json,
            response_json,
            prompt_tokens,
            completion_tokens,
            cost_usd,
            started_at,
            finished_at,
            error_message,
            latency_ms,
            MODEL,
            error_type,
            now,
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _call_vision_with_retry(
    client,
    image_path: Path,
    obra: str,
    conn: sqlite3.Connection,
) -> tuple[dict, int]:
    """
    Invoca Vision com retry (3 tentativas, backoff 1s + 3s).

    Cada tentativa — sucesso ou falha — gera registro em api_calls:
      - Falha retryable: error_type preenchido, response_hash=NULL, dorme e retenta.
      - Falha não-retryable: error_type preenchido, propaga (run_worker → FAILED).
      - Sucesso: error_type=NULL, response_hash preenchido.

    Retorna (response_dict, api_call_id). response_dict é o dump completo
    do ChatCompletion (inclui choices + usage) — parse do JSON estruturado
    fica para analyze_image/visual_analysis_handler.
    """
    image_data_url = _encode_image_data_url(image_path)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": USER_PROMPT},
                {"type": "image_url", "image_url": {"url": image_data_url, "detail": "high"}},
            ],
        },
    ]

    # Para o hash do request, não inclui o data URL (reduz ruído no log e
    # evita duplicar bytes da imagem em api_calls — o input_size_bytes
    # captura o tamanho).
    request_body_for_hash = {
        "model": MODEL,
        "temperature": TEMPERATURE,
        "response_format": RESPONSE_FORMAT,
        "image_filename": image_path.name,
        "image_sha256": sha256_file(image_path),
        "image_detail": "high",
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
                "Vision tentativa %d falhou (%s); retry em %.1fs",
                attempt + 1, err_type, delay,
            )
            time.sleep(delay)
            continue

        # Sucesso
        finished_dt = datetime.now(UTC)
        latency_ms = int((finished_dt - started_dt).total_seconds() * 1000)
        response_dict = (
            response.model_dump() if hasattr(response, "model_dump") else dict(response)
        )
        response_json = json.dumps(
            response_dict, sort_keys=True, ensure_ascii=False, default=str,
        )
        response_hash = sha256_text(response_json)

        usage = response_dict.get("usage")
        if not usage:
            log.warning(
                "Vision response sem campo 'usage'; cost_usd será 0.0 — "
                "auditar api_call subsequente (request_hash=%s)",
                request_hash,
            )
            prompt_tokens = 0
            completion_tokens = 0
        else:
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

    # Inalcançável — loop sempre retorna no sucesso ou raise no último attempt.
    assert last_exc is not None
    raise last_exc


def _extract_content_string(response_dict: dict) -> str:
    """
    Extrai `choices[0].message.content` do dump do ChatCompletion.
    Retorna "" se estrutura inesperada — o sentinel cuida do caso.
    """
    choices = response_dict.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content") or ""


def analyze_image(
    image_path: Path,
    obra: str,
    conn: sqlite3.Connection,
) -> tuple[dict, int]:
    """
    Baixo nível — chama Vision, loga em api_calls, retorna (response_dict, api_call_id).

    response_dict é o dump completo do ChatCompletion. O parse do JSON
    estruturado (e fallback para sentinel se mal-formado) é feito no
    visual_analysis_handler (alto nível).
    """
    client = _get_openai_client()
    return _call_vision_with_retry(client, image_path, obra, conn)


def visual_analysis_handler(task: Task, conn: sqlite3.Connection) -> str | None:
    """
    Handler para tasks VISUAL_ANALYSIS consumidas por run_worker.

    Pipeline (espelha transcribe_handler):
        1. Resolve vault_path via config.get().
        2. Lê registro da imagem-fonte em files (herança de metadata).
        3. analyze_image() → ChatCompletion dump + api_call_id.
        4. Extrai content string, parseia JSON, valida schema.
           Se qualquer etapa falha: sentinel com raw_response,
           confidence=0.0, low_confidence=1.
           Se OK: analysis_dict = JSON estruturado, confidence=1.0.
        5. Escreve JSON em /30_visual/<image>.analysis.json.
        6. INSERT OR IGNORE em files (derived .json,
           semantic_status='awaiting_classification').
        7. Check-and-insert em visual_analyses (schema não tem
           UNIQUE(file_id); guardrail manual preserva idempotência).
        8. INSERT OR IGNORE em media_derivations.
        9. UPDATE files da imagem-fonte: semantic_status='analyzed'.
        10. conn.commit() atômico no fim.

    Returns:
        file_id do .json derivado (tasks.result_ref).
    """
    payload = task.payload
    image_file_id = payload["file_id"]
    image_rel_path = payload["file_path"]

    obra = task.obra
    vault_path = config.get().vault_path(obra)
    image_path = vault_path / image_rel_path

    src_row = conn.execute(
        "SELECT file_id, sha256, referenced_by_message, timestamp_resolved, timestamp_source "
        "FROM files WHERE file_id = ?",
        (image_file_id,),
    ).fetchone()
    if src_row is None:
        raise RuntimeError(
            f"imagem {image_file_id} não encontrada em files (obra={obra})"
        )

    response_dict, api_call_id = analyze_image(image_path, obra, conn)

    content_str = _extract_content_string(response_dict)
    sentinel_reason = ""

    try:
        parsed = json.loads(content_str) if content_str else None
    except json.JSONDecodeError as exc:
        parsed = None
        sentinel_reason = f"json_decode_error:{exc.msg}"

    if parsed is None and not sentinel_reason:
        sentinel_reason = "empty_content"

    if parsed is not None:
        ok, reason = _validate_schema(parsed)
        if not ok:
            sentinel_reason = reason
            parsed = None

    if parsed is None:
        analysis_dict = _build_malformed_sentinel(
            image_file_id=image_file_id,
            image_rel_path=image_rel_path,
            image_sha256=src_row["sha256"],
            raw_content=content_str,
            reason=sentinel_reason,
        )
        confidence = 0.0
        log.warning(
            "Vision JSON inválido: %s (reason=%s)",
            image_path.name, sentinel_reason,
        )
    else:
        analysis_dict = parsed
        confidence = 1.0

    analysis_json_str = json.dumps(
        analysis_dict, ensure_ascii=False, sort_keys=True,
    )

    json_filename = f"{image_path.name}.analysis.json"
    json_path = vault_path / "30_visual" / json_filename
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(analysis_json_str, encoding="utf-8")

    json_sha = sha256_file(json_path)
    json_file_id = f"f_{json_sha[:12]}"
    json_rel_path = f"30_visual/{json_filename}"
    now = _now_iso_utc()

    conn.execute(
        """
        INSERT OR IGNORE INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            derived_from, derivation_method, referenced_by_message,
            timestamp_resolved, timestamp_source, semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            json_file_id,
            obra,
            json_rel_path,
            "text",
            json_sha,
            json_path.stat().st_size,
            image_file_id,
            f"{MODEL} (temperature={TEMPERATURE})",
            src_row["referenced_by_message"],
            src_row["timestamp_resolved"],
            src_row["timestamp_source"],
            "awaiting_classification",
            now,
        ),
    )

    # visual_analyses não tem UNIQUE(file_id) no schema — check-and-insert
    # manual garante idempotência sem alterar schema.
    existing = conn.execute(
        "SELECT 1 FROM visual_analyses WHERE file_id = ?", (json_file_id,),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO visual_analyses (
                obra, file_id, analysis_json, confidence, api_call_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                obra,
                json_file_id,
                analysis_json_str,
                confidence,
                api_call_id,
                now,
            ),
        )

    conn.execute(
        """
        INSERT OR IGNORE INTO media_derivations (
            obra, source_file_id, derived_file_id, derivation_method, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (obra, image_file_id, json_file_id, f"{MODEL} vision", now),
    )

    conn.execute(
        "UPDATE files SET semantic_status = 'analyzed' WHERE file_id = ?",
        (image_file_id,),
    )

    conn.commit()
    return json_file_id


__all__ = [
    "MODEL",
    "PRICING_USD_PER_TOKEN",
    "REQUIRED_FIELDS",
    "RESPONSE_FORMAT",
    "TEMPERATURE",
    "analyze_image",
    "visual_analysis_handler",
]
