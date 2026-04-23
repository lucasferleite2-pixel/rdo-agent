"""
adaptive.py — entrevista conduzida por Claude Sonnet 4.6 (Fase D2).

Contrato de turno:
  - Envia: historico da conversa + YAML acumulado + metadado (obra)
  - Recebe: JSON com
      {
        "next_question": "pergunta em PT-BR",
        "accumulated_yaml_fragment": {...},  # dict a mesclar
        "is_complete": bool,
        "notes_for_operator": "string opcional de guidance"
      }

Loop encerra quando `is_complete=True` OU operador digita STOP.

Dependencias: anthropic (ja em uso pelo narrator). Import lazy do
client para nao forcar ANTHROPIC_API_KEY em testes que mockam.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol

from rdo_agent.ground_truth.loader import (
    GroundTruthValidationError,
    _parse_root,
)
from rdo_agent.ground_truth.schema import GroundTruth
from rdo_agent.gt_extractor.interview import InterviewInput, STOP_TOKENS
from rdo_agent.gt_extractor.prompts_adaptive import (
    GT_EXTRACTOR_SYSTEM_PROMPT,
)


MODEL = "claude-sonnet-4-6"
TEMPERATURE = 0.2
MAX_TOKENS = 2048
MAX_TURNS_DEFAULT = 30


class _ClientProtocol(Protocol):
    """Shape minimo do anthropic client (pra injetar fake nos testes)."""

    def create(self, **kwargs: Any) -> Any: ...


class AdaptiveInterviewError(Exception):
    """Falha de parse do turno (Claude produziu JSON invalido 3x)."""


@dataclass
class AdaptiveTurn:
    """Resultado de um turno pra inspecao/teste."""

    question: str
    fragment: dict
    is_complete: bool
    notes: str | None


def _extract_json_block(text: str) -> dict | None:
    """
    Procura o ULTIMO bloco ```json ... ``` no output do modelo.
    Se nao houver cerca, tenta parse direto.
    """
    # 1) Match ```json...```
    m = list(re.finditer(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text))
    if m:
        try:
            return json.loads(m[-1].group(1))
        except json.JSONDecodeError:
            pass
    # 2) Fallback: primeiro { até último } correspondente
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def _deep_merge(dst: dict, src: dict) -> dict:
    """
    Merge recursivo. src wins em leaves; listas sao concatenadas
    (com dedup por id se itens forem dicts com `id`).
    """
    for k, v in src.items():
        if k not in dst:
            dst[k] = v
            continue
        if isinstance(dst[k], dict) and isinstance(v, dict):
            dst[k] = _deep_merge(dst[k], v)
        elif isinstance(dst[k], list) and isinstance(v, list):
            merged = list(dst[k])
            existing_ids = {
                x.get("id") for x in merged
                if isinstance(x, dict) and "id" in x
            }
            for item in v:
                if (isinstance(item, dict) and "id" in item
                        and item["id"] in existing_ids):
                    continue
                merged.append(item)
            dst[k] = merged
        else:
            dst[k] = v
    return dst


def _get_client() -> _ClientProtocol:
    """Wrapper pra lazy-init. Reusa config do narrator."""
    from rdo_agent.utils import config
    key = config.get().anthropic_api_key
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY ausente. Configure .env para usar o "
            "extrator adaptativo (modo --adaptive)."
        )
    from anthropic import Anthropic
    client = Anthropic(api_key=key, timeout=120.0, max_retries=2)
    return client.messages


def _run_turn(
    client: _ClientProtocol,
    history: list[dict],
    current_yaml: dict,
    obra: str,
) -> AdaptiveTurn:
    """Uma chamada ao modelo + parse do JSON de resposta."""
    user_msg = (
        "YAML acumulado ate agora (JSON):\n"
        f"```json\n{json.dumps(current_yaml, ensure_ascii=False, indent=2)}"
        f"\n```\n\n"
        f"Obra id: {obra}\n\n"
        "Historico da conversa (last 10):\n"
    )
    for entry in history[-10:]:
        role = entry["role"]
        content = entry["content"]
        user_msg += f"- {role}: {content}\n"
    user_msg += (
        "\nBaseado no YAML acumulado e no historico, gere o proximo turno "
        "seguindo o formato JSON do system prompt."
    )

    response = client.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=GT_EXTRACTOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    # Compativel com anthropic.Messages: .content[0].text
    text_parts = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", ""))
    text = "".join(text_parts)

    parsed = _extract_json_block(text)
    if parsed is None:
        raise AdaptiveInterviewError(
            f"Nao achei bloco JSON parseavel na resposta:\n{text[:300]}"
        )

    return AdaptiveTurn(
        question=str(parsed.get("next_question") or ""),
        fragment=parsed.get("accumulated_yaml_fragment") or {},
        is_complete=bool(parsed.get("is_complete", False)),
        notes=parsed.get("notes_for_operator"),
    )


def run_adaptive_interview(
    inp: InterviewInput,
    *,
    client: _ClientProtocol | None = None,
    max_turns: int = MAX_TURNS_DEFAULT,
) -> GroundTruth:
    """
    Entrevista adaptativa (Fase D2). Requer ANTHROPIC_API_KEY (a menos
    que `client` seja injetado pra testes).

    Retorna GroundTruth pronto. Se operador aborta ou completa antes do
    YAML minimo (obra_real.nome + canal.id), levanta
    GroundTruthValidationError.
    """
    c = client if client is not None else _get_client()
    history: list[dict] = []
    current_yaml: dict = {}

    for _ in range(max_turns):
        turn = _run_turn(c, history, current_yaml, inp.obra)
        current_yaml = _deep_merge(current_yaml, turn.fragment)

        if turn.notes:
            inp.output_fn(f"[nota] {turn.notes}")

        if turn.is_complete:
            break

        if not turn.question:
            # Modelo nao fez pergunta mas nao completou — bailout
            break

        answer = inp.input_fn(f"{turn.question}\n> ").strip()
        if answer in STOP_TOKENS:
            inp.output_fn("Encerrando entrevista adaptativa.")
            break

        history.append({"role": "assistant", "content": turn.question})
        history.append({"role": "user", "content": answer})

    # Parse final usando o loader canonico — valida schema
    try:
        return _parse_root(current_yaml)
    except GroundTruthValidationError:
        # Se faltar campo obrigatorio, repropaga com contexto
        raise


__all__ = [
    "AdaptiveInterviewError",
    "AdaptiveTurn",
    "MAX_TURNS_DEFAULT",
    "MODEL",
    "_deep_merge",
    "_extract_json_block",
    "_run_turn",
    "run_adaptive_interview",
]
