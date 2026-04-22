"""
Reprocessa visual_analyses antigas com pipeline OCR-first — Sprint 4 Op9.

Fluxo:
  1. Lista todas visual_analyses existentes para a obra
  2. Resolve imagem-fonte via join files (derived_from da JSON row)
  3. Arquiva row atual em visual_analyses_archive (preserva forense)
  4. Enfileira task OCR_FIRST pra imagem-fonte (pipeline Op8)
  5. Processa worker: OCR -> documento OU Vision V2 (foto) OU
     financial_records (se comprovante)
  6. Gera relatorio comparativo em /tmp/op9_reprocessing_report.md

Uso:
    # Dry-run: valida listagem, nao toca DB
    python scripts/reprocess_visual_analyses_ocr_first.py \\
        --obra EVERALDO_SANTAQUITERIA --dry-run

    # Piloto: processa 5 imagens
    python scripts/reprocess_visual_analyses_ocr_first.py \\
        --obra EVERALDO_SANTAQUITERIA --limit 5

    # Full run
    python scripts/reprocess_visual_analyses_ocr_first.py \\
        --obra EVERALDO_SANTAQUITERIA

Exit codes:
    0 — OK (mesmo com parcialmente falhadas)
    1 — banco nao encontrado
    2 — nenhuma visual_analysis encontrada
    3 — budget de custo ultrapassado
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ARCHIVE_REASON = "superseded_by_ocr_first_retroactive_sprint4_op9"

# Gate de custo DELTA desta execucao (nao absoluto da vault).
# Budget desta fase ~$0.30; margem ate $0.50. Seguranca contra loops.
COST_BUDGET_DELTA_USD = 0.50


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _list_candidates(conn: sqlite3.Connection, obra: str) -> list[dict]:
    """
    Lista visual_analyses da obra que PRECISAM ser reprocessadas.

    Criterios:
      - visual_analyses row existente
      - NAO esta em visual_analyses_archive com mesmo original_id
        (ou seja, nunca foi reprocessada antes)
      - imagem-fonte (files via derived_from) existe

    Retorna lista de dicts com: original_id, analysis_fid, source_fid,
    source_path, analysis_json (str), confidence, created_at.
    """
    rows = conn.execute(
        """
        SELECT va.id AS original_id,
               va.file_id AS analysis_fid,
               va.analysis_json,
               va.confidence,
               va.api_call_id,
               va.created_at,
               f_json.derived_from AS source_fid,
               f_src.file_path AS source_path
        FROM visual_analyses va
        LEFT JOIN files f_json ON f_json.file_id = va.file_id
        LEFT JOIN files f_src ON f_src.file_id = f_json.derived_from
        LEFT JOIN visual_analyses_archive arch ON arch.original_id = va.id
        WHERE va.obra = ?
          AND arch.id IS NULL
          AND f_src.file_id IS NOT NULL
        ORDER BY va.id
        """,
        (obra,),
    ).fetchall()
    return [dict(r) for r in rows]


def _archive_row(
    conn: sqlite3.Connection, row: dict, reason: str = ARCHIVE_REASON,
) -> int:
    """Copia visual_analyses row para archive. Retorna archive row id."""
    now = _now_iso_utc()
    cur = conn.execute(
        """
        INSERT INTO visual_analyses_archive (
            original_id, obra, file_id, analysis_json, confidence,
            api_call_id, created_at, archived_at, archive_reason
        ) SELECT id, obra, file_id, analysis_json, confidence,
                 api_call_id, created_at, ?, ?
        FROM visual_analyses WHERE id = ?
        """,
        (now, reason, row["original_id"]),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _enqueue_ocr_first_task(
    conn: sqlite3.Connection, obra: str, source_fid: str, source_path: str,
) -> int:
    """
    Enfileira OCR_FIRST task pra reprocessamento.

    Bloqueia se ja existe task pending/running (evita corrida).
    Tasks 'done' (de execucao anterior — ex: Op8) NAO bloqueiam —
    reprocessamento retroativo Op9 quer rodar dispositivos novamente
    com prompt V2. Gera nova task id (versao n+1).
    """
    from rdo_agent.orchestrator import Task, TaskStatus, TaskType, enqueue

    existing = conn.execute(
        """SELECT id FROM tasks
           WHERE obra=? AND task_type='ocr_first'
             AND json_extract(payload, '$.file_id') = ?
             AND status IN ('pending', 'running')""",
        (obra, source_fid),
    ).fetchone()
    if existing:
        return existing[0]

    t = Task(
        id=None, task_type=TaskType.OCR_FIRST,
        payload={"file_id": source_fid, "file_path": source_path},
        status=TaskStatus.PENDING, depends_on=[],
        obra=obra, created_at="", priority=5,
    )
    return enqueue(conn, t)


def _get_cumulative_cost(conn: sqlite3.Connection, obra: str) -> float:
    r = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) FROM api_calls WHERE obra=?",
        (obra,),
    ).fetchone()
    return float(r[0] or 0.0)


def reprocess(
    conn: sqlite3.Connection,
    *,
    obra: str,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    """
    Core reprocessing loop. Nao roda worker — so enfileira tasks e
    arquiva rows. O worker deve ser rodado apos (via rdo-agent process
    --task-type ocr_first) ou em chamada separada.
    """
    candidates = _list_candidates(conn, obra)
    if limit is not None:
        candidates = candidates[:limit]

    result = {
        "obra": obra,
        "dry_run": dry_run,
        "total_candidates": len(candidates),
        "archived": 0,
        "tasks_enqueued": 0,
        "tasks_already_exist": 0,
        "errors": [],
        "candidates_summary": [],
    }

    if not candidates:
        return result

    cost_before = _get_cumulative_cost(conn, obra)

    for row in candidates:
        entry = {
            "original_id": row["original_id"],
            "analysis_fid": row["analysis_fid"],
            "source_fid": row["source_fid"],
            "source_path": row["source_path"],
            "created_at": row["created_at"],
        }
        result["candidates_summary"].append(entry)

        if dry_run:
            continue

        # Cost gate — delta relativo ao inicio da execucao
        current_cost = _get_cumulative_cost(conn, obra)
        delta_cost = current_cost - cost_before
        if delta_cost > COST_BUDGET_DELTA_USD:
            result["errors"].append(
                f"COST_BUDGET_EXCEEDED: delta={delta_cost:.4f} "
                f"budget_delta={COST_BUDGET_DELTA_USD}"
            )
            break

        try:
            _archive_row(conn, row)
            result["archived"] += 1

            task_id = _enqueue_ocr_first_task(
                conn, obra, row["source_fid"], row["source_path"],
            )
            if task_id:
                result["tasks_enqueued"] += 1
        except sqlite3.IntegrityError as exc:
            # Task ja existe (UNIQUE) ou similar — log e continua
            result["tasks_already_exist"] += 1
            result["errors"].append(
                f"original_id={row['original_id']}: {type(exc).__name__}: {exc}"
            )
        except Exception as exc:
            result["errors"].append(
                f"original_id={row['original_id']}: {type(exc).__name__}: {exc}"
            )

    result["cost_before"] = cost_before
    return result


def _apply_openai_timeout_patches(timeout_sec: float = 30.0) -> None:
    """
    Monkey-patch _get_openai_client dos modulos que rodam no worker
    pra garantir timeout explicito. Resolve travamento observado em
    producao onde SDK OpenAI faz retries com timeout default de 600s,
    pendurando por minutos. Op9 nao toca em ocr_extractor/financial_ocr
    (blacklist), entao patch acontece aqui ao nivel do script.

    Config conservadora: timeout 30s + max_retries=0. Se alguma imagem
    nao receber resposta em 30s, task vai pra FAILED e proxima roda.
    Trade-off: algumas imagens podem falhar, mas evitamos travamento.
    """
    import rdo_agent.financial_ocr as fin_mod
    import rdo_agent.ocr_extractor as ocr_mod
    from rdo_agent.utils import config

    def _patched_client():
        key = config.get().openai_api_key
        if not key:
            raise RuntimeError("OPENAI_API_KEY ausente (config helper).")
        from openai import OpenAI
        return OpenAI(api_key=key, timeout=timeout_sec, max_retries=0)

    ocr_mod._get_openai_client = _patched_client
    fin_mod._get_openai_client = _patched_client


def run_worker(
    conn: sqlite3.Connection, obra: str, limit: int | None = None,
) -> dict:
    """Processa tasks OCR_FIRST pendentes enfileiradas pelo reprocess."""
    _apply_openai_timeout_patches()
    from rdo_agent.ocr_extractor import ocr_first_handler

    pending = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE obra=? AND task_type='ocr_first' "
        "AND status='pending'", (obra,),
    ).fetchone()[0]

    done = 0
    failed = 0
    processed = 0

    # simple sequential worker
    from rdo_agent.orchestrator import (
        Task,
        TaskStatus,
        TaskType,
        mark_done,
        mark_failed,
        mark_running,
    )

    while True:
        if limit is not None and processed >= limit:
            break

        row = conn.execute(
            """SELECT * FROM tasks
               WHERE obra=? AND task_type='ocr_first' AND status='pending'
               ORDER BY created_at ASC LIMIT 1""",
            (obra,),
        ).fetchone()
        if row is None:
            break
        task = Task(
            id=row["id"],
            task_type=TaskType(row["task_type"]),
            payload=json.loads(row["payload"]),
            status=TaskStatus(row["status"]),
            depends_on=json.loads(row["depends_on"]),
            obra=row["obra"],
            created_at=row["created_at"],
        )
        mark_running(conn, task.id)
        try:
            result_ref = ocr_first_handler(task, conn)
            mark_done(conn, task.id, result_ref=result_ref)
            done += 1
        except Exception as exc:
            import traceback
            mark_failed(conn, task.id, traceback.format_exc())
            failed += 1
            print(
                f"[err] task {task.id} failed: "
                f"{type(exc).__name__}: {str(exc)[:200]}",
                file=sys.stderr,
            )
        processed += 1
        time.sleep(0.3)  # pequeno throttle

    return {"pending_before": pending, "done": done, "failed": failed}


def render_report(
    reprocess_result: dict, worker_result: dict | None, cost_after: float,
) -> str:
    lines = []
    lines.append("# Op9 Reprocessing Report")
    lines.append("")
    lines.append(f"**Obra:** {reprocess_result['obra']}")
    lines.append(f"**Modo:** {'DRY-RUN' if reprocess_result['dry_run'] else 'LIVE'}")
    lines.append(f"**Data:** {_now_iso_utc()}")
    lines.append("")
    lines.append("## Sumario")
    lines.append("")
    lines.append(f"- Candidatos listados: **{reprocess_result['total_candidates']}**")
    lines.append(f"- Rows arquivados: **{reprocess_result['archived']}**")
    lines.append(
        f"- Tasks OCR_FIRST enfileiradas: **{reprocess_result['tasks_enqueued']}**"
    )
    lines.append(
        f"- Tasks ja existentes (pulados): **{reprocess_result['tasks_already_exist']}**"
    )
    if worker_result:
        lines.append(f"- Worker: {worker_result['done']} done, "
                     f"{worker_result['failed']} failed")
    lines.append(f"- Erros: {len(reprocess_result['errors'])}")
    cost_before = reprocess_result.get("cost_before", 0.0)
    lines.append(
        f"- Custo: antes US$ {cost_before:.4f}, "
        f"depois US$ {cost_after:.4f} (delta US$ {cost_after - cost_before:.4f})"
    )
    lines.append("")

    if reprocess_result["errors"]:
        lines.append("## Erros")
        lines.append("")
        for err in reprocess_result["errors"]:
            lines.append(f"- {err}")
        lines.append("")

    lines.append("## Candidatos processados")
    lines.append("")
    lines.append("| # | original_id | source_fid | source_path |")
    lines.append("|---|---:|---|---|")
    for i, c in enumerate(reprocess_result["candidates_summary"][:50], 1):
        path = (c.get("source_path") or "")[:60]
        lines.append(
            f"| {i} | {c['original_id']} | `{c['source_fid']}` | `{path}` |"
        )
    if len(reprocess_result["candidates_summary"]) > 50:
        lines.append(f"| ... | ({len(reprocess_result['candidates_summary']) - 50} mais) | | |")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--obra", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument(
        "--skip-worker", action="store_true",
        help="Enfileira tasks mas nao roda worker (execucao separada)",
    )
    p.add_argument(
        "--output", default="/tmp/op9_reprocessing_report.md",
    )
    args = p.parse_args()

    from rdo_agent.orchestrator import init_db
    from rdo_agent.utils import config
    vault_path = config.get().vault_path(args.obra)
    db_path = vault_path / "index.sqlite"
    if not db_path.exists():
        print(f"[err] banco nao encontrado: {db_path}", file=sys.stderr)
        return 1

    conn = init_db(vault_path)

    print(f"[info] obra={args.obra} dry_run={args.dry_run} limit={args.limit}")
    reprocess_result = reprocess(
        conn, obra=args.obra, dry_run=args.dry_run, limit=args.limit,
    )
    print(
        f"[info] candidatos={reprocess_result['total_candidates']} "
        f"archived={reprocess_result['archived']} "
        f"enqueued={reprocess_result['tasks_enqueued']}"
    )

    worker_result = None
    if not args.dry_run and not args.skip_worker:
        print("[info] processando worker (OCR_FIRST)...")
        worker_result = run_worker(conn, args.obra, limit=args.limit)
        print(
            f"[info] worker: done={worker_result['done']} "
            f"failed={worker_result['failed']}"
        )

    cost_after = _get_cumulative_cost(conn, args.obra)
    report = render_report(reprocess_result, worker_result, cost_after)
    Path(args.output).write_text(report, encoding="utf-8")
    print(f"[ok] {args.output}")
    print(f"[ok] cost_total_obra=US$ {cost_after:.4f}")

    conn.close()

    if reprocess_result["total_candidates"] == 0:
        return 2
    if any("COST_BUDGET_EXCEEDED" in e for e in reprocess_result["errors"]):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
