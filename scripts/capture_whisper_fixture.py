#!/usr/bin/env python
"""
Captura golden fixture do Whisper (uso único, custo ~R$ 0.01).

Roda 1x manualmente — NÃO em CI por default (geraria cobrança recorrente).
Idempotente: se a fixture já existe, não chama a API. Imprime sha256 do
conteúdo salvo para o usuário comparar com o painel da OpenAI.

Exit code:
    0 = sucesso (fixture já existia OU foi capturada agora)
    1 = falha (ffmpeg ausente, API error, chave inválida, etc.)

Uso:
    .venv/bin/python scripts/capture_whisper_fixture.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from rdo_agent.transcriber import (
    LANGUAGE,
    MODEL,
    RESPONSE_FORMAT,
    TEMPERATURE,
    _get_openai_client,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "tests" / "fixtures" / "whisper_golden_response.json"
)


def main() -> int:
    if FIXTURE_PATH.exists():
        sha = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
        print(f"Fixture já existe em {FIXTURE_PATH}")
        print(f"SHA-256: {sha}")
        print("Nada a fazer — delete o arquivo se quiser re-capturar.")
        return 0

    if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0:
        print("ERRO: ffmpeg não está no PATH. Instale antes.", file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = Path(tmp.name)

    try:
        # Áudio sintético 3s: tone 440Hz. Curto para minimizar custo.
        # Whisper suporta .wav 16kHz mono — mesmo formato que o extract_audio
        # gera a partir de vídeos, então a fixture é representativa do fluxo.
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-ar", "16000", "-ac", "1",
                str(audio_path),
            ],
            check=True,
        )

        client = _get_openai_client()

        print(f"Chamando Whisper ({MODEL}, language={LANGUAGE}, temperature={TEMPERATURE})...")
        with open(audio_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model=MODEL,
                language=LANGUAGE,
                temperature=TEMPERATURE,
                response_format=RESPONSE_FORMAT,
                file=f,
            )

        response_dict = (
            response.model_dump() if hasattr(response, "model_dump") else dict(response)
        )

        content = json.dumps(response_dict, indent=2, ensure_ascii=False, default=str)
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

        print(f"Fixture SHA-256: {sha}")
        print("Compare com dashboard da OpenAI para confirmar que a call é esta.")

        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE_PATH.write_text(content, encoding="utf-8")
        print(f"Salvo em {FIXTURE_PATH}")
        return 0
    except Exception as exc:
        print(f"ERRO: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        audio_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
