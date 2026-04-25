"""
Transcritor Whisper — Camada 1 (Sprint 2 §Fase 2).

Espelha o padrão do document_extractor: dois níveis públicos, persistência
em files + media_derivations, sentinel em disco para transcrições vazias
(contrato do banco preservado: transcriptions.text="" quando sem fala).

Diferenças em relação ao document_extractor:
  - Chama API externa (OpenAI Whisper) → logging granular em api_calls
    por tentativa (3 tentativas, backoff 1s + 3s, retry apenas para
    erros transientes: APIConnectionError, RateLimitError, APITimeoutError).
  - Cada tentativa que falha grava row em api_calls com error_type
    específico (connection/rate_limit/timeout). Tentativa bem-sucedida
    grava row com error_type=NULL e response_hash preenchido.
  - Validação lazy de OPENAI_API_KEY em _get_openai_client — falha
    explícita com mensagem orientativa só no momento do handler.

Arquitetura de teste (4 camadas, docs/SPRINT2_PLAN.md §4):
  - Unitários: monkeypatch _get_openai_client → FakeClient.
  - Golden fixture: tests/fixtures/whisper_golden_response.json
    (capturado uma única vez via scripts/capture_whisper_fixture.py).
  - Smoke manual + E2E: rodam contra Whisper real.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from rdo_agent.orchestrator import Task
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_file, sha256_text
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

MODEL = "whisper-1"
LANGUAGE = "pt"
TEMPERATURE = 0.0
RESPONSE_FORMAT = "verbose_json"
COST_USD_PER_MINUTE = 0.006  # whisper-1 rate oficial (platform.openai.com/pricing)
RETRY_DELAYS_SEC: tuple[float, float] = (1.0, 3.0)  # antes das tentativas 2 e 3
LOW_CONFIDENCE_THRESHOLD = 0.5


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _get_openai_client():
    """
    Retorna cliente OpenAI. Sem cache — o custo de instanciação é
    irrelevante frente ao HTTP do Whisper, e caching complica isolamento
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


def _build_empty_sentinel_audio(
    audio_file_id: str,
    audio_rel_path: str,
    audio_sha256: str,
    duration_sec: float,
    language: str,
) -> str:
    """
    Marcador textual para áudios sem fala transcrita (silêncio, ruído,
    ou áudio muito curto). Mesma motivação do _build_empty_sentinel do
    document_extractor: sha256 de "" é constante → colisão de file_id
    entre múltiplos .txt vazios. Sentinel inclui source_sha256, garantindo
    unicidade.
    """
    return (
        "# rdo-agent: sem fala detectada\n"
        f"# transcriber: {MODEL} (language={language}, temperature={TEMPERATURE})\n"
        f"# source_file_id: {audio_file_id}\n"
        f"# source_path: {audio_rel_path}\n"
        f"# source_sha256: {audio_sha256}\n"
        f"# duration_sec: {duration_sec:.2f}\n"
        "# note: áudio provavelmente silencioso, sem voz ou muito curto\n"
    )


def _classify_error_type(exc: Exception) -> str:
    """
    Traduz exceção do SDK em string curta para api_calls.error_type.
    Ordem importa: subclasses antes de superclasses.
    """
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


def _compute_cost_usd(duration_sec: float, model: str) -> float:
    """Custo por duração de áudio. whisper-1 é $0.006/min."""
    if model != "whisper-1":
        log.warning("modelo %s sem preço conhecido; reportando 0.0", model)
        return 0.0
    return (duration_sec / 60.0) * COST_USD_PER_MINUTE


def _log_api_call(
    conn: sqlite3.Connection,
    *,
    obra: str,
    request_hash: str,
    request_json: str,
    response_hash: str | None,
    response_json: str | None,
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
            "audio.transcriptions.create",
            request_hash,
            response_hash,
            request_json,
            response_json,
            None,  # tokens_input — Whisper não reporta tokens
            None,  # tokens_output
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


def _call_whisper_with_retry(
    client,
    audio_path: Path,
    obra: str,
    conn: sqlite3.Connection,
) -> tuple[dict, int]:
    """
    Invoca Whisper com retry (3 tentativas, backoff 1s + 3s).

    Cada tentativa — sucesso ou falha — gera um registro em api_calls:
      - Falha retryable: error_type preenchido, response_hash=NULL, dorme e retenta.
      - Falha não-retryable: error_type preenchido, propaga (run_worker → FAILED).
      - Sucesso: error_type=NULL, response_hash preenchido.

    Retorna (response_dict, api_call_id) da tentativa bem-sucedida —
    api_call_id vai para transcriptions.api_call_id (referência única).
    """
    request_body = {
        "model": MODEL,
        "language": LANGUAGE,
        "temperature": TEMPERATURE,
        "response_format": RESPONSE_FORMAT,
        "file": audio_path.name,  # nome para identificação; bytes vão pelo form
    }
    request_json = json.dumps(request_body, sort_keys=True, ensure_ascii=False)
    request_hash = sha256_text(request_json)

    last_exc: Exception | None = None
    for attempt in range(3):
        started_dt = datetime.now(UTC)
        started_at = started_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        try:
            with open(audio_path, "rb") as f:
                # Whisper rejeita .opus por nome mesmo quando o container é OGG/Opus
                # válido. Renomeamos só o nome de upload; o disco e o request_hash
                # abaixo continuam capturando o nome original (registro forense).
                upload_name = audio_path.name
                if audio_path.suffix.lower() == ".opus":
                    upload_name = audio_path.stem + ".ogg"
                response = client.audio.transcriptions.create(
                    model=MODEL,
                    language=LANGUAGE,
                    temperature=TEMPERATURE,
                    response_format=RESPONSE_FORMAT,
                    file=(upload_name, f, "audio/ogg"),
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
                cost_usd=0.0,
                started_at=started_at,
                finished_at=finished_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                latency_ms=latency_ms,
                error_message=f"{err_type}: {exc}",
                error_type=err_type,
            )
            conn.commit()  # persiste log da falha mesmo se propagarmos exceção
            last_exc = exc
            if not _is_retryable(exc) or attempt >= 2:
                raise
            delay = RETRY_DELAYS_SEC[attempt]
            log.warning(
                "Whisper tentativa %d falhou (%s); retry em %.1fs",
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
        duration_sec = float(response_dict.get("duration") or 0.0)
        cost_usd = _compute_cost_usd(duration_sec, MODEL)

        api_call_id = _log_api_call(
            conn,
            obra=obra,
            request_hash=request_hash,
            request_json=request_json,
            response_hash=response_hash,
            response_json=response_json,
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


def _compute_confidence(segments: list[dict]) -> float:
    """
    Média de exp(avg_logprob) pelos segmentos do verbose_json do Whisper.
    Retorna 0.0 se não houver segments ou se nenhum tiver avg_logprob.
    Valor resultante fica em [0, 1].
    """
    if not segments:
        return 0.0
    vals = [math.exp(s["avg_logprob"]) for s in segments if "avg_logprob" in s]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def transcribe_audio(
    audio_path: Path,
    obra: str,
    conn: sqlite3.Connection,
) -> tuple[dict, int]:
    """
    Baixo nível — chama Whisper, loga em api_calls, retorna (response_dict, api_call_id).

    Não persiste em transcriptions/files/media_derivations — isso é
    responsabilidade do transcribe_handler (alto nível).
    """
    client = _get_openai_client()
    return _call_whisper_with_retry(client, audio_path, obra, conn)


def transcribe_handler(task: Task, conn: sqlite3.Connection) -> str | None:
    """
    Handler para tasks TRANSCRIBE consumidas por run_worker.

    Pipeline:
        1. Resolve vault_path via config.get().
        2. Lê registro do áudio-fonte em files (herança de metadata).
        3. transcribe_audio() → resposta Whisper + api_call_id.
        4. Se text vazio: escreve sentinel no .txt, confidence=0.0,
           low_confidence=1. Senão: escreve texto transcrito.
        5. INSERT em files (derived .txt, semantic_status='awaiting_classification').
        6. INSERT em transcriptions (check-and-insert — schema não tem
           UNIQUE(file_id) em transcriptions; guardrail manual garante
           idempotência do handler sem alterar schema).
        7. INSERT OR IGNORE em media_derivations.
        8. UPDATE files do áudio-fonte: semantic_status='transcribed'.
        9. conn.commit() atômico no fim.

    Returns:
        file_id do .txt derivado (tasks.result_ref).
    """
    payload = task.payload
    audio_file_id = payload["file_id"]
    audio_rel_path = payload["file_path"]

    obra = task.obra
    vault_path = config.get().vault_path(obra)
    audio_path = vault_path / audio_rel_path

    src_row = conn.execute(
        "SELECT file_id, sha256, referenced_by_message, timestamp_resolved, timestamp_source "
        "FROM files WHERE file_id = ?",
        (audio_file_id,),
    ).fetchone()
    if src_row is None:
        raise RuntimeError(f"áudio {audio_file_id} não encontrado em files (obra={obra})")

    response_dict, api_call_id = transcribe_audio(audio_path, obra, conn)

    text = (response_dict.get("text") or "").strip()
    language = response_dict.get("language") or LANGUAGE
    duration_sec = float(response_dict.get("duration") or 0.0)
    segments = response_dict.get("segments") or []
    segments_json = json.dumps(segments, ensure_ascii=False, default=str)

    txt_filename = f"{audio_path.name}.transcription.txt"
    txt_path = vault_path / "20_transcriptions" / txt_filename
    txt_path.parent.mkdir(parents=True, exist_ok=True)

    if text:
        txt_path.write_text(text, encoding="utf-8")
        confidence = _compute_confidence(segments)
    else:
        sentinel = _build_empty_sentinel_audio(
            audio_file_id=audio_file_id,
            audio_rel_path=audio_rel_path,
            audio_sha256=src_row["sha256"],
            duration_sec=duration_sec,
            language=language,
        )
        txt_path.write_text(sentinel, encoding="utf-8")
        confidence = 0.0
        log.warning(
            "Whisper sem fala detectada: %s (duration=%.2fs)",
            audio_path.name, duration_sec,
        )

    low_confidence = 1 if confidence < LOW_CONFIDENCE_THRESHOLD else 0

    txt_sha = sha256_file(txt_path)
    txt_file_id = f"f_{txt_sha[:12]}"
    txt_rel_path = f"20_transcriptions/{txt_filename}"
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
            txt_file_id,
            obra,
            txt_rel_path,
            "text",
            txt_sha,
            txt_path.stat().st_size,
            audio_file_id,
            f"whisper-1 (language={language}, temperature={TEMPERATURE})",
            src_row["referenced_by_message"],
            src_row["timestamp_resolved"],
            src_row["timestamp_source"],
            "awaiting_classification",
            now,
        ),
    )

    # transcriptions não tem UNIQUE(file_id) no schema — verificação manual
    # garante idempotência sem ALTER TABLE adicional.
    existing_transcription = conn.execute(
        "SELECT 1 FROM transcriptions WHERE file_id = ?", (txt_file_id,),
    ).fetchone()
    if existing_transcription is None:
        conn.execute(
            """
            INSERT INTO transcriptions (
                obra, file_id, text, language, segments_json,
                confidence, low_confidence, api_call_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obra,
                txt_file_id,
                text,
                language,
                segments_json,
                confidence,
                low_confidence,
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
        (obra, audio_file_id, txt_file_id, f"whisper-1 language={language}", now),
    )

    conn.execute(
        "UPDATE files SET semantic_status = 'transcribed' WHERE file_id = ?",
        (audio_file_id,),
    )

    conn.commit()
    return txt_file_id


def transcribe_pending(
    conn: sqlite3.Connection,
    obra: str,
    *,
    max_audios: int | None = None,
    force: bool = False,
    on_skip=None,
    on_done=None,
    on_fail=None,
) -> dict[str, int]:
    """
    Loop de orquestração — Sessão 8 / dívida #45.

    Drena tasks ``TRANSCRIBE`` pendentes e despacha pra
    :func:`transcribe_handler`, integrado com a infra do GRUPO 2:

    - **PipelineStateManager** (Sessão 6) — claim/complete/fail
      atômicos, recuperáveis via ``reset_running``.
    - **StructuredLogger** (Sessão 6) — emite ``stage_start``,
      ``stage_done``, ``stage_failed`` e ``cost_event`` por áudio.
    - **CostQuota** (Sessão 6) — bloqueia se total acumulado de
      transcribe ultrapassar quota diária.
    - **CircuitBreaker** ``openai_whisper`` (Sessão 6) — pausa
      quando OpenAI Whisper API der falhas consecutivas.

    Idempotência: cada áudio tem ``file_id`` determinístico
    (``sha256[:12]``). Se já existe transcrição para esse ``file_id``
    e ``force=False``, pula sem chamar Whisper (poupando $0.006/min
    real). ``force=True`` ainda usa o guardrail interno do
    transcribe_handler (que só insere se transcriptions estiver
    vazio para o file_id) — caller que quiser retranscrever de
    verdade precisa apagar a row primeiro (não é responsabilidade
    deste loop).

    Args:
        conn: SQLite com tasks/transcriptions já populadas.
        obra: identificador do canal/corpus.
        max_audios: ``None`` = drenar tudo. Caso útil em testes.
        force: ``True`` ignora idempotência (re-claim). Default
            ``False`` skipa áudios já transcritos.
        on_skip / on_done / on_fail: callbacks opcionais para teste/
            UI; recebem ``(file_id, ctx_dict)``.

    Returns:
        dict com ``processed`` / ``skipped`` / ``failed`` counts.
    """
    from rdo_agent.observability import (
        CircuitOpenError,
        CostQuota,
        QuotaExceededError,
        StructuredLogger,
    )
    from rdo_agent.observability.resilience import get_openai_whisper_circuit
    from rdo_agent.orchestrator import TaskType
    from rdo_agent.pipeline_state import PipelineStateManager

    state = PipelineStateManager(conn)
    logger = StructuredLogger(obra)
    quota = CostQuota(corpus_id=obra)
    breaker = get_openai_whisper_circuit()

    counts = {"processed": 0, "skipped": 0, "failed": 0}
    cumulative_cost_usd = 0.0

    while True:
        if max_audios is not None and (
            counts["processed"] + counts["skipped"] + counts["failed"]
        ) >= max_audios:
            break

        task = state.claim(obra, task_type=TaskType.TRANSCRIBE)
        if task is None:
            break

        file_id = task.payload.get("file_id", "")

        # Idempotência: já transcrito? (usa column transcriptions.file_id)
        if not force:
            existing = conn.execute(
                "SELECT id FROM transcriptions WHERE file_id = ?",
                (file_id,),
            ).fetchone()
            if existing is not None:
                state.complete(task.id, result_ref=str(existing[0]))
                logger.emit(
                    "transcribe_skipped",
                    file_id=file_id, reason="already_done",
                )
                counts["skipped"] += 1
                if on_skip:
                    on_skip(file_id, {"existing_id": existing[0]})
                continue

        # Quota
        try:
            quota.check_or_raise(cumulative_cost_usd)
        except QuotaExceededError as e:
            state.fail(task.id, f"quota: {e}")
            logger.stage_failed(
                "transcribe", file_id, "quota_exceeded", str(e),
            )
            counts["failed"] += 1
            break  # próxima execução pode retentar

        logger.stage_start("transcribe", file_id)
        t0 = time.time()
        try:
            result_ref = breaker.call(transcribe_handler, task, conn)
        except CircuitOpenError as e:
            state.fail(task.id, f"circuit: {e}")
            logger.stage_failed(
                "transcribe", file_id, "circuit_open", str(e),
            )
            counts["failed"] += 1
            break  # circuit aberto: não adianta tentar próximo
        except Exception as e:
            duration_ms = int((time.time() - t0) * 1000)
            error_type = _classify_error_type(e)
            state.fail(task.id, str(e))
            logger.stage_failed(
                "transcribe", file_id, error_type, str(e),
                duration_ms=duration_ms,
            )
            counts["failed"] += 1
            if on_fail:
                on_fail(file_id, {"error_type": error_type, "error": str(e)})
            continue

        duration_ms = int((time.time() - t0) * 1000)
        # Recupera api_call_id e cost_usd da row inserida em api_calls
        last = conn.execute(
            "SELECT id, cost_usd FROM api_calls "
            "WHERE obra = ? AND endpoint = 'audio.transcriptions' "
            "ORDER BY id DESC LIMIT 1",
            (obra,),
        ).fetchone()
        cost_usd = float(last[1] or 0.0) if last is not None else 0.0
        cumulative_cost_usd += cost_usd

        state.complete(task.id, result_ref=result_ref)
        logger.cost_event(
            api="openai", model=MODEL,
            tokens_in=0, tokens_out=0, cost_usd=cost_usd,
            stage="transcribe", file_id=file_id,
        )
        logger.stage_done("transcribe", file_id, duration_ms)
        counts["processed"] += 1
        if on_done:
            on_done(file_id, {"cost_usd": cost_usd, "duration_ms": duration_ms})

    return counts


__all__ = [
    "COST_USD_PER_MINUTE",
    "LANGUAGE",
    "MODEL",
    "RESPONSE_FORMAT",
    "TEMPERATURE",
    "transcribe_audio",
    "transcribe_handler",
    "transcribe_pending",
]
