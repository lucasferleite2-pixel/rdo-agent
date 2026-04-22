"""
Retry de tasks OCR/Vision com JSON truncado/sentinel — Sprint 4 Op11 #12.

Identifica rows em visual_analyses_active cujo analysis_json contem
markers de falha:
  - `_sentinel` presente (malformed_json_response sentinel)
  - Texto "Unterminated string" em analysis_json (truncate mid-response)
  - `confidence=0.0` combinado com keywords de erro

Para cada, enfileira OCR_FIRST task nova (que pode rotear pra Vision V2
com prompt calibrado Op9, ou pra OCR se agora parecer documento).

Uso:
    python scripts/retry_failed_ocr.py --obra EVERALDO_SANTAQUITERIA [--dry-run]
    rdo-agent process --task-type ocr_first --obra EVERALDO_SANTAQUITERIA
"""

from __future__ import annotations

import argparse
import sys

SENTINEL_MARKERS: tuple[str, ...] = (
    '"_sentinel"',
    "Unterminated string",
    '"reason":"malformed_json_response"',
    '"reason": "malformed',
)


def _find_suspect_analyses(conn, obra: str) -> list[dict]:
    """
    Lista visual_analyses_active + image-fonte cujo analysis_json
    contem algum sentinel marker OU confidence=0.0.
    """
    marker_likes = " OR ".join(
        ["va.analysis_json LIKE ?" for _ in SENTINEL_MARKERS]
    )
    sql = f"""
        SELECT va.id, va.file_id AS json_fid, va.confidence,
               va.analysis_json,
               f_src.file_id AS source_fid,
               f_src.file_path AS source_path,
               f_src.file_type AS source_type,
               parent.file_type AS parent_type
        FROM visual_analyses_active va
        LEFT JOIN files f_json ON f_json.file_id = va.file_id
        LEFT JOIN files f_src ON f_src.file_id = f_json.derived_from
        LEFT JOIN files parent ON parent.file_id = f_src.derived_from
        WHERE va.obra = ?
          AND (va.confidence = 0.0 OR {marker_likes})
    """
    params = [obra] + [f"%{m}%" for m in SENTINEL_MARKERS]
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _enqueue_retry(conn, obra: str, source_fid: str, source_path: str) -> int | None:
    """Enfileira OCR_FIRST nova. Skip se ja pending/running."""
    from rdo_agent.orchestrator import Task, TaskStatus, TaskType, enqueue

    existing = conn.execute(
        """SELECT id FROM tasks WHERE obra=? AND task_type='ocr_first'
           AND json_extract(payload, '$.file_id')=?
           AND status IN ('pending','running')""",
        (obra, source_fid),
    ).fetchone()
    if existing:
        return None
    t = Task(
        id=None, task_type=TaskType.OCR_FIRST,
        payload={"file_id": source_fid, "file_path": source_path},
        status=TaskStatus.PENDING, depends_on=[],
        obra=obra, created_at="", priority=5,
    )
    return enqueue(conn, t)


def retry(conn, obra: str, dry_run: bool = False) -> dict:
    suspects = _find_suspect_analyses(conn, obra)
    result = {
        "suspeitas": len(suspects),
        "enqueued": 0,
        "skipped_no_source": 0,
        "skipped_already_pending": 0,
        "details": [],
    }
    for s in suspects:
        entry = {
            "va_id": s["id"],
            "json_fid": s["json_fid"],
            "source_fid": s["source_fid"],
            "source_path": s["source_path"],
            "source_type": s["source_type"],
            "parent_type": s["parent_type"],
        }
        result["details"].append(entry)

        if not s["source_fid"] or not s["source_path"]:
            result["skipped_no_source"] += 1
            continue
        if dry_run:
            result["enqueued"] += 1
            continue
        tid = _enqueue_retry(
            conn, obra, s["source_fid"], s["source_path"],
        )
        if tid is None:
            result["skipped_already_pending"] += 1
        else:
            result["enqueued"] += 1

    return result


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
        result = retry(conn, args.obra, dry_run=args.dry_run)
    finally:
        conn.close()

    print(f"[ok] {'DRY-RUN' if args.dry_run else 'APPLIED'}")
    print(f"[ok] visual_analyses suspeitas encontradas: {result['suspeitas']}")
    print(f"[ok] tasks OCR_FIRST enfileiradas: {result['enqueued']}")
    print(f"[ok] skipped (sem source): {result['skipped_no_source']}")
    print(f"[ok] skipped (ja pending): {result['skipped_already_pending']}")
    if result["details"]:
        print("\nDetalhes:")
        for d in result["details"][:20]:
            print(f"  - va_id={d['va_id']} json={d['json_fid']} "
                  f"source={d['source_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
