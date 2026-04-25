"""
Extrator de frames adaptativos de video — Sprint 4 Op3b.

[PROMOVIDO em Sessão 9]: a lógica foi movida para
``rdo_agent.video.extract_frames_for_video``. Este script permanece
como CLI shim por compat com invocações históricas. Novos callers
devem usar diretamente:

    from rdo_agent.video import extract_frames_for_video, process_videos_pending

    # Para 1 vídeo específico:
    result = extract_frames_for_video(conn, obra, video_file_id)

    # Para drenar todos os vídeos pendentes do corpus:
    counts = process_videos_pending(conn, obra)

Usage (legado):
    python scripts/extract_video_frames.py --obra <codesc> [file_id ...]

Se nenhum file_id for passado, usa lista interna de dias-chave
EVERALDO_SANTAQUITERIA (Sprint 4 Op3b briefing).

Output em stdout: JSON com contagem de frames criados por video.
"""

from __future__ import annotations

import argparse
import json
import sys

from rdo_agent.orchestrator import init_db
from rdo_agent.utils import config
from rdo_agent.video import extract_frames_for_video

DEFAULT_KEY_DATE_VIDEOS: tuple[str, ...] = (
    "f_ecb7374a8b76",  # 08/04
    "f_1f5d5c030375",  # 14/04
    "f_1def40a04f4e",  # 14/04
    "f_1f818f64eefa",  # 15/04
    "f_ef77117947ca",  # 15/04
    "f_445a0975174b",  # 15/04
    "f_e68d7a6ac115",  # 15/04
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrai frames adaptativos de videos. "
                    "(Shim para rdo_agent.video.extract_frames_for_video.)",
    )
    parser.add_argument("--obra", required=True, help="CODESC da obra")
    parser.add_argument(
        "video_file_ids", nargs="*",
        help="Lista de file_ids de video (default: lista de dias-chave EVERALDO)",
    )
    args = parser.parse_args()

    ids = tuple(args.video_file_ids) or DEFAULT_KEY_DATE_VIDEOS
    conn = init_db(config.get().vault_path(args.obra))
    results = []
    for vid in ids:
        try:
            r = extract_frames_for_video(conn, args.obra, vid)
        except Exception as exc:
            r = {"video_file_id": vid, "error": f"{type(exc).__name__}: {exc}"}
        results.append(r)
    conn.close()

    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
