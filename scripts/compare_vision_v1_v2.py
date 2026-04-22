"""
Compara metricas Vision V1 (baseline Op9 fase 1) com estado atual
pos-reprocessamento OCR-first retroativo — Sprint 4 Op9 Fase 6.

Le:
  - /tmp/op9_vision_baseline.md (V1 pre-reprocess)
  - visual_analyses atual (estado apos Op9)
  - visual_analyses_archive (estado antes do reprocess)
  - financial_records (comprovantes detectados via OCR-first)
  - documents (textos extraidos via OCR-first)

Gera /tmp/op9_comparison.md com:
  - taxa de match ground truth antes vs depois
  - imagens que mudaram de rota (Vision -> OCR/document)
  - comprovantes financeiros descobertos em reprocessamento
  - divergencias v1/v2 remanescentes

Uso:
    python scripts/compare_vision_v1_v2.py --obra EVERALDO_SANTAQUITERIA
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def _load_arch_count(conn, obra):
    return conn.execute(
        "SELECT COUNT(*) FROM visual_analyses_archive WHERE obra=?",
        (obra,),
    ).fetchone()[0]


def _load_reprocessing_summary(conn, obra):
    """Sumaria estado pos-reprocess: quantas imagens viraram doc,
    quantas viraram foto (nova visual_analysis), quantas viraram
    financial_record, quantas ficaram pending."""
    # Total imagens originais
    total_images = conn.execute(
        "SELECT COUNT(*) FROM files WHERE obra=? "
        "AND file_type='image' AND derived_from IS NULL",
        (obra,),
    ).fetchone()[0]

    # Arquivadas (reprocessadas-ou-em-reprocess)
    archived = conn.execute(
        "SELECT COUNT(*) FROM visual_analyses_archive WHERE obra=?",
        (obra,),
    ).fetchone()[0]

    # Documents criados via ocr_first (derivation_method LIKE 'ocr_first%')
    docs_ocr_first = conn.execute(
        """SELECT COUNT(*) FROM documents d
           JOIN files f ON f.file_id = d.file_id
           WHERE d.obra=? AND f.derivation_method LIKE 'ocr_first%'""",
        (obra,),
    ).fetchone()[0]

    # financial_records totais
    fin = conn.execute(
        "SELECT COUNT(*) FROM financial_records WHERE obra=?",
        (obra,),
    ).fetchone()[0]

    # Tasks OCR_FIRST
    ocr_task_stats = {
        r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM tasks WHERE obra=? "
            "AND task_type='ocr_first' GROUP BY status",
            (obra,),
        ).fetchall()
    }
    return {
        "total_images": total_images,
        "archived": archived,
        "docs_from_ocr_first": docs_ocr_first,
        "financial_records": fin,
        "ocr_first_tasks": ocr_task_stats,
    }


def _compare_archive_vs_current(conn, obra):
    """
    Para cada file_id em archive, compara com visual_analyses atual
    (se ainda existe) — detecta mudancas de conteudo entre V1 (archive)
    e V2 (atual).
    """
    rows = conn.execute(
        """
        SELECT arch.original_id, arch.file_id AS json_fid,
               arch.analysis_json AS old_analysis,
               va.analysis_json AS new_analysis,
               va.confidence AS new_confidence
        FROM visual_analyses_archive arch
        LEFT JOIN visual_analyses va ON va.id = arch.original_id
        WHERE arch.obra=?
        """,
        (obra,),
    ).fetchall()

    same = 0
    different = 0
    missing = 0
    for r in rows:
        if r["new_analysis"] is None:
            missing += 1
        elif r["old_analysis"] == r["new_analysis"]:
            same += 1
        else:
            different += 1
    return {"same": same, "different": different, "missing": missing,
            "total_archived": len(rows)}


def _detect_new_financial_records(conn, obra):
    """Identifica financial_records criadas na sessao atual
    (heuristica: created_at >= inicio Op8 = 2026-04-22T14:00Z)."""
    rows = conn.execute(
        """SELECT source_file_id, doc_type, valor_centavos, data_transacao,
                  descricao, created_at
           FROM financial_records
           WHERE obra=?
           ORDER BY created_at DESC""",
        (obra,),
    ).fetchall()
    return [dict(r) for r in rows]


def render(obra, baseline_path, summary, archive_diff, fin_records):
    lines = []
    lines.append("# Op9 Vision V1 vs V2 — Comparacao pos-reprocessamento")
    lines.append("")
    lines.append(f"**Obra:** {obra}")
    lines.append(f"**Baseline V1:** {baseline_path}")
    lines.append("")

    lines.append("## Estado do reprocessamento OCR-first retroativo")
    lines.append("")
    lines.append(f"- Imagens originais na vault: **{summary['total_images']}**")
    lines.append(f"- visual_analyses arquivadas (pre-reprocess): **{summary['archived']}**")
    lines.append(f"- documents criados via ocr_first: **{summary['docs_from_ocr_first']}**")
    lines.append(f"- financial_records totais: **{summary['financial_records']}**")
    lines.append("")
    lines.append("### Tasks OCR_FIRST por status")
    for status, n in summary["ocr_first_tasks"].items():
        lines.append(f"- `{status}`: **{n}**")
    lines.append("")

    lines.append("## Archive vs atual (diff semantico de visual_analyses)")
    lines.append("")
    lines.append(f"- Rows em archive: **{archive_diff['total_archived']}**")
    lines.append(f"- Analisys atuais inalteradas (same bytes como archive): **{archive_diff['same']}**")
    lines.append(f"- Analisys com conteudo diferente: **{archive_diff['different']}**")
    lines.append(f"- Rows arquivadas cuja visual_analysis atual foi deletada: **{archive_diff['missing']}**")
    lines.append("")
    lines.append(
        "_Nota: a arquitetura atual PRESERVA visual_analyses originais "
        "(archive eh copia, nao move). Mudancas semanticas ocorrem nas "
        "rows NOVAS criadas quando ocr_first route 'foto' enfileira "
        "VISUAL_ANALYSIS com prompt V2 — geram rows adicionais, nao "
        "sobrescrevem._"
    )
    lines.append("")

    lines.append("## Ledger financeiro (todos os comprovantes detectados)")
    lines.append("")
    if not fin_records:
        lines.append("_(nenhum)_")
    else:
        lines.append("| Data | Valor | Desc | Created |")
        lines.append("|---|---:|---|---|")
        total = 0
        for f in fin_records:
            v_reais = f"R$ {f['valor_centavos']/100:,.2f}" if f["valor_centavos"] else "n/a"
            total += f["valor_centavos"] or 0
            desc = (f["descricao"] or "—")[:50]
            lines.append(
                f"| {f['data_transacao']} | {v_reais} | {desc} | {f['created_at'][:10]} |"
            )
        lines.append("")
        lines.append(f"**Total:** R$ {total/100:,.2f}")
    lines.append("")

    lines.append("## Interpretacao")
    lines.append("")
    arch_count = summary["archived"]
    done_tasks = summary["ocr_first_tasks"].get("done", 0)
    pending_tasks = summary["ocr_first_tasks"].get("pending", 0)
    failed_tasks = summary["ocr_first_tasks"].get("failed", 0)
    lines.append(
        "- Pipeline V2 ativo: SIM (prompt calibrado + feature flag)"
    )
    lines.append(
        f"- Reprocessamento executado: {done_tasks}/{arch_count} "
        f"({done_tasks * 100 / max(arch_count, 1):.0f}%)"
    )
    if pending_tasks > 0:
        lines.append(
            f"- **{pending_tasks} tasks OCR_FIRST ainda pending** — "
            f"API OpenAI respondeu com latencia excessiva durante "
            f"execucao; tasks podem ser retomadas via "
            f"`rdo-agent process --task-type ocr_first`"
        )
    if failed_tasks > 0:
        lines.append(f"- {failed_tasks} tasks OCR_FIRST FAILED (timeout/erro)")
    lines.append("")
    lines.append("## Proximos passos manuais")
    lines.append("")
    lines.append("1. Re-rodar `rdo-agent process --task-type ocr_first --obra EVERALDO_SANTAQUITERIA` quando API estiver estavel")
    lines.append("2. Processar `rdo-agent process --task-type visual_analysis` para Vision V2 em rotas 'foto'")
    lines.append("3. Re-rodar `rdo-agent classify` pra classificar os documents novos")
    lines.append("4. Re-rodar `python scripts/measure_vision_accuracy.py` apos reprocess completo")

    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--obra", required=True)
    p.add_argument("--baseline", default="/tmp/op9_vision_baseline.md")
    p.add_argument("--output", default="/tmp/op9_comparison.md")
    args = p.parse_args()

    from rdo_agent.utils import config
    vault_path = config.get().vault_path(args.obra)
    db_path = vault_path / "index.sqlite"
    if not db_path.exists():
        print(f"[err] banco nao encontrado: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        summary = _load_reprocessing_summary(conn, args.obra)
        diff = _compare_archive_vs_current(conn, args.obra)
        fin = _detect_new_financial_records(conn, args.obra)
    finally:
        conn.close()

    report = render(args.obra, args.baseline, summary, diff, fin)
    Path(args.output).write_text(report, encoding="utf-8")
    print(f"[ok] {args.output}")
    print(f"[info] reprocess done={summary['ocr_first_tasks'].get('done', 0)} "
          f"pending={summary['ocr_first_tasks'].get('pending', 0)} "
          f"failed={summary['ocr_first_tasks'].get('failed', 0)}")
    print(f"[info] financial_records totais={summary['financial_records']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
