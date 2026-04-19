#!/usr/bin/env python
"""
Captura golden fixture do Vision (uso único, custo ~US$ 0.003).

Paralelo a scripts/capture_whisper_fixture.py. Roda 1x manualmente — NÃO
em CI por default (geraria cobrança recorrente). Idempotente: se a
fixture já existe, não chama a API. Imprime sha256 do conteúdo salvo
para o usuário comparar com o painel da OpenAI.

Imagem sintética: 64x64 PNG determinístico (cor sólida + retângulo,
sem texto). Mesmos bytes em todas as execuções.

Exit code:
    0 = sucesso (fixture já existia OU foi capturada agora)
    1 = falha (PIL ausente, API error, chave inválida, etc.)

Uso:
    .venv/bin/python scripts/capture_vision_fixture.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

from rdo_agent.visual_analyzer import (
    MODEL,
    RESPONSE_FORMAT,
    SYSTEM_PROMPT,
    TEMPERATURE,
    USER_PROMPT,
    _encode_image_data_url,
    _get_openai_client,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "tests" / "fixtures" / "vision_golden_response.json"
)


def _build_deterministic_png(png_path: Path) -> None:
    """
    64x64 PNG: fundo verde-oliva com retângulo amarelo-construção central.
    Sem texto. PIL sem pnginfo não embute timestamp, então os bytes são
    estáveis entre execuções (assumindo mesma versão do Pillow/libpng).
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (64, 64), color=(120, 140, 100))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(16, 20), (48, 44)], fill=(200, 170, 60))
    img.save(png_path, format="PNG", optimize=False)


def main() -> int:
    if FIXTURE_PATH.exists():
        print(f"{FIXTURE_PATH} já existe, pulando")
        return 0

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        image_path = Path(tmp.name)

    try:
        _build_deterministic_png(image_path)

        client = _get_openai_client()
        image_data_url = _encode_image_data_url(image_path)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": USER_PROMPT},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ]

        print(f"Chamando {MODEL} (vision), ~US$ 0.003...")
        response = client.chat.completions.create(
            model=MODEL,
            temperature=TEMPERATURE,
            response_format=RESPONSE_FORMAT,
            messages=messages,
        )

        response_dict = (
            response.model_dump() if hasattr(response, "model_dump") else dict(response)
        )

        content = json.dumps(
            response_dict,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE_PATH.write_text(content, encoding="utf-8")

        sha = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
        print(f"Salvo em {FIXTURE_PATH}")
        print(f"SHA-256: {sha}")
        return 0
    except Exception as exc:
        print(f"ERRO: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        image_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
