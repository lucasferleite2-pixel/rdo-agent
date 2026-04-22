"""
Backfill one-shot do superseded_by para rows V2 ja criadas pelo
reprocess retroativo Op9 antes da Divida #10 ser resolvida — Sprint 4 Op11.

Logica:
  Para cada image_file_id (file_id em `files` com file_type='image'),
  acha todas as visual_analyses cuja JSON file deriva desse image_file_id.
  Se houver >1, marca as mais antigas (created_at ASC) como superseded
  pela mais recente.

Nao re-analisa nada. So propaga superseded_by pra rows existentes.

Uso:
    python scripts/backfill_superseded_by.py --obra EVERALDO_SANTAQUITERIA [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def backfill(conn, obra: str, dry_run: bool = False) -> dict:
    """Retorna dict com contadores: grupos, rows_marcadas."""
    # Pega todas as visual_analyses agrupadas por imagem-fonte
    rows = conn.execute(
        """
        SELECT va.id, va.file_id AS json_fid, va.created_at,
               va.superseded_by,
               f_json.derived_from AS image_fid
        FROM visual_analyses va
        LEFT JOIN files f_json ON f_json.file_id = va.file_id
        WHERE va.obra = ?
          AND f_json.derived_from IS NOT NULL
        ORDER BY f_json.derived_from, va.created_at
        """,
        (obra,),
    ).fetchall()

    by_image: dict[str, list] = {}
    for r in rows:
        image_fid = r["image_fid"]
        if not image_fid:
            continue
        by_image.setdefault(image_fid, []).append(r)

    grupos_multi = 0
    rows_marcadas = 0
    now = _now_iso()

    for _image_fid, analyses in by_image.items():
        if len(analyses) < 2:
            continue
        grupos_multi += 1
        # Sort por created_at asc (mais antiga -> mais nova)
        analyses_sorted = sorted(analyses, key=lambda r: r["created_at"])
        newest = analyses_sorted[-1]
        newest_id = newest["id"]

        for old in analyses_sorted[:-1]:
            if old["superseded_by"] is not None:
                continue  # ja marcada
            if dry_run:
                rows_marcadas += 1
                continue
            conn.execute(
                """
                UPDATE visual_analyses
                SET superseded_by = ?, superseded_at = ?
                WHERE id = ? AND superseded_by IS NULL
                """,
                (newest_id, now, old["id"]),
            )
            rows_marcadas += 1

    if not dry_run:
        conn.commit()

    return {
        "grupos_multi": grupos_multi,
        "rows_marcadas": rows_marcadas,
        "total_imagens_com_analyses": len(by_image),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--obra", required=True)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from rdo_agent.orchestrator import init_db
    from rdo_agent.utils import config

    vault_path = config.get().vault_path(args.obra)
    db_path = vault_path / "index.sqlite"
    if not db_path.exists():
        print(f"[err] banco nao encontrado: {db_path}", file=sys.stderr)
        return 1

    conn = init_db(vault_path)
    try:
        result = backfill(conn, args.obra, dry_run=args.dry_run)
    finally:
        conn.close()

    print(f"[ok] {'DRY-RUN' if args.dry_run else 'APPLIED'}")
    print(f"[ok] total_imagens_com_analyses: {result['total_imagens_com_analyses']}")
    print(f"[ok] grupos_multi (>=2 analyses): {result['grupos_multi']}")
    print(f"[ok] rows_marcadas como superseded: {result['rows_marcadas']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
