"""
Gerador de RDO piloto — Sprint 3 Fase 4 (Camada 4).

Agrega `classifications` de um dia especifico da obra em RDO markdown
e, opcionalmente, PDF (via weasyprint). NAO chama API.

Uso:
    python scripts/generate_rdo_piloto.py \\
        --obra EVERALDO_SANTAQUITERIA --data 2026-04-08
    python scripts/generate_rdo_piloto.py \\
        --obra EVERALDO_SANTAQUITERIA --data 2026-04-08 --output-dir reports

Exit codes:
    0 — RDO gerado (markdown e possivelmente PDF)
    1 — zero classifications para a data informada
    2 — banco nao encontrado

Dependencia opcional: weasyprint. Se ausente, gera apenas markdown.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

# Ordem canonica das secoes do RDO. ilegivel fica em "Notas forenses".
CATEGORY_HEADERS: list[tuple[str, str]] = [
    ("negociacao_comercial", "Negociações comerciais"),
    ("pagamento", "Pagamentos"),
    ("cronograma", "Cronograma e prazos"),
    ("especificacao_tecnica", "Especificações técnicas"),
    ("solicitacao_servico", "Solicitações de serviço"),
    ("material", "Materiais"),
    ("reporte_execucao", "Reporte de execução"),
    ("off_topic", "Eventos fora de escopo (off-topic)"),
]


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fetch_classified_rows(
    conn: sqlite3.Connection, obra: str, date: str,
) -> list[sqlite3.Row]:
    """
    Retorna rows classifications.classified cujo audio-fonte tem
    timestamp_resolved no dia informado. Ordenado cronologicamente.
    """
    sql = """
        SELECT
            c.id AS classification_id,
            c.source_file_id,
            c.categories,
            c.confidence_model,
            c.reasoning AS classifier_reasoning,
            c.human_reviewed,
            c.human_corrected_text,
            t.text AS transcription_text,
            f_trans.timestamp_resolved AS ts_trans,
            f_audio.file_path AS audio_path,
            f_audio.timestamp_resolved AS ts_audio,
            m.timestamp_whatsapp AS ts_msg
        FROM classifications c
        LEFT JOIN transcriptions t
            ON t.obra = c.obra AND t.file_id = c.source_file_id
        LEFT JOIN files f_trans
            ON f_trans.file_id = c.source_file_id
        LEFT JOIN files f_audio
            ON f_audio.file_id = f_trans.derived_from
        LEFT JOIN messages m
            ON m.message_id = f_audio.referenced_by_message
        WHERE c.obra = ?
          AND c.semantic_status = 'classified'
          AND DATE(COALESCE(f_audio.timestamp_resolved, f_trans.timestamp_resolved))
              = DATE(?)
        ORDER BY COALESCE(f_audio.timestamp_resolved, f_trans.timestamp_resolved)
    """
    return list(conn.execute(sql, (obra, date)).fetchall())


def _extract_hhmm(ts_iso: str | None) -> str:
    """Extrai HH:MM do timestamp ISO. Retorna '--:--' se ausente/invalido."""
    if not ts_iso:
        return "--:--"
    try:
        # Aceita tanto "2026-04-08T09:15:00Z" quanto variantes com microsegundos
        cleaned = ts_iso.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).strftime("%H:%M")
    except ValueError:
        return "--:--"


def _primary_category(categories_json: str) -> str:
    """Retorna primeiro elemento do JSON array; '' se invalido."""
    try:
        cats = json.loads(categories_json)
        if isinstance(cats, list) and cats:
            return str(cats[0])
    except (json.JSONDecodeError, TypeError):
        pass
    return ""


def _group_by_primary(rows: list[sqlite3.Row]) -> dict[str, list[sqlite3.Row]]:
    by: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        primary = _primary_category(r["categories"])
        by.setdefault(primary, []).append(r)
    return by


def _format_item_line(row: sqlite3.Row) -> str:
    """
    Uma linha markdown por classification.
    Exemplo:
      - [09:15] [REVISADO] file_id=file_trans_07 — Ô Lucas, daqui a...
    """
    ts_iso = row["ts_audio"] or row["ts_trans"] or row["ts_msg"]
    hhmm = _extract_hhmm(ts_iso)
    tag = "[REVISADO]" if row["human_reviewed"] else "[NÃO REVISADO]"
    text = row["human_corrected_text"] or row["transcription_text"] or "(texto ausente)"
    # Proteger markdown contra newlines no texto (compressao visual)
    text_flat = " ".join(text.split())
    return (
        f"- [{hhmm}] {tag} "
        f"file_id=`{row['source_file_id']}` — {text_flat}"
    )


def render_markdown(
    obra: str, date: str, rows: list[sqlite3.Row],
) -> str:
    by_cat = _group_by_primary(rows)
    total = len(rows)
    reviewed = sum(1 for r in rows if r["human_reviewed"])

    lines: list[str] = []
    lines.append(f"# RDO — EE Santa Quitéria — {date}")
    lines.append("")
    lines.append(f"**Obra:** {obra}")
    lines.append(f"**Data:** {date}")
    lines.append(f"**Gerado em:** {_now_iso_utc()}")
    lines.append("")
    lines.append("## Resumo do dia")
    lines.append("")
    lines.append(f"- Eventos classificados: **{total}**")
    lines.append(f"- Revisados por humano: **{reviewed}**")
    lines.append(f"- Não revisados (classificados direto pelo detector): **{total - reviewed}**")
    lines.append("")

    for code, header in CATEGORY_HEADERS:
        items = by_cat.get(code, [])
        lines.append(f"## {header}")
        lines.append("")
        if not items:
            lines.append("_(nenhum evento desta categoria)_")
        else:
            for item in items:
                lines.append(_format_item_line(item))
        lines.append("")

    # ilegivel -> Notas forenses
    ilegivel_items = by_cat.get("ilegivel", [])
    lines.append("## Notas forenses")
    lines.append("")
    lines.append(f"- Eventos marcados como ilegíveis: **{len(ilegivel_items)}**")
    if ilegivel_items:
        for r in ilegivel_items:
            lines.append(
                f"  - file_id=`{r['source_file_id']}` — transcrição degradada"
            )
    # Se houver categorias fora do vocabulario esperado (nao deveria acontecer)
    unknown = [k for k in by_cat if k not in [c for c, _ in CATEGORY_HEADERS] + ["ilegivel", ""]]
    if unknown:
        lines.append(f"- ⚠ Categorias inesperadas encontradas: {unknown}")
    empty_cat = by_cat.get("", [])
    if empty_cat:
        lines.append(f"- ⚠ {len(empty_cat)} classifications sem category primary valido")
    lines.append("")

    return "\n".join(lines)


def _markdown_to_pdf(markdown_text: str, output_pdf: Path) -> bool:
    """
    Gera PDF via weasyprint a partir de HTML minimal do markdown.

    Faz uma renderizacao HTML bem simples (nao usa biblioteca markdown —
    faz uma conversao manual de headers/bullets). Objetivo: PDF legivel,
    nao tipografia perfeita.

    Retorna True se PDF foi gerado; False se weasyprint indisponivel.
    """
    try:
        from weasyprint import HTML
    except ImportError:
        return False

    html_lines = ["<html><head><meta charset='utf-8'>"]
    html_lines.append("<style>")
    html_lines.append(
        "body{font-family:Helvetica,Arial,sans-serif;font-size:10pt;"
        "line-height:1.35;margin:1.5cm;}"
    )
    html_lines.append("h1{font-size:16pt;border-bottom:2px solid #333;}")
    html_lines.append("h2{font-size:12pt;margin-top:1em;color:#333;}")
    html_lines.append("code{background:#eee;padding:0 3px;border-radius:3px;}")
    html_lines.append("em{color:#888;}")
    html_lines.append("li{margin:0.2em 0;}")
    html_lines.append("</style></head><body>")

    in_list = False
    for raw in markdown_text.split("\n"):
        line = raw.rstrip()
        if line.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h1>{_html_escape(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{_html_escape(line[3:])}</h2>")
        elif line.startswith("- ") or line.startswith("  - "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_md_inline(line.lstrip('- '))}</li>")
        elif line.strip() == "":
            if in_list:
                html_lines.append("</ul>")
                in_list = False
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{_md_inline(line)}</p>")
    if in_list:
        html_lines.append("</ul>")
    html_lines.append("</body></html>")

    HTML(string="\n".join(html_lines)).write_pdf(str(output_pdf))
    return True


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _md_inline(s: str) -> str:
    """Converte inline markdown basico: **bold**, `code`, _italic_."""
    out = _html_escape(s)
    # **bold**
    parts: list[str] = []
    rest = out
    while "**" in rest:
        pre, _, rest2 = rest.partition("**")
        parts.append(pre)
        mid, _, rest2 = rest2.partition("**")
        parts.append(f"<strong>{mid}</strong>")
        rest = rest2
    parts.append(rest)
    out = "".join(parts)
    # `code`
    parts = []
    rest = out
    while "`" in rest:
        pre, _, rest2 = rest.partition("`")
        parts.append(pre)
        mid, _, rest2 = rest2.partition("`")
        parts.append(f"<code>{mid}</code>")
        rest = rest2
    parts.append(rest)
    out = "".join(parts)
    # _italic_  (simples, pula se tiver underscore no code)
    parts = []
    rest = out
    while "_" in rest and rest.count("_") >= 2:
        pre, _, rest2 = rest.partition("_")
        parts.append(pre)
        mid, _, rest2 = rest2.partition("_")
        parts.append(f"<em>{mid}</em>")
        rest = rest2
    parts.append(rest)
    return "".join(parts)


def generate_rdo(
    conn: sqlite3.Connection,
    *,
    obra: str,
    date: str,
    output_dir: Path,
) -> dict:
    """
    Gera RDO markdown (+ PDF se weasyprint disponivel).

    Returns:
        dict com chaves:
          - markdown_path: Path do .md gerado
          - pdf_path: Path do .pdf ou None
          - total: numero de classifications incluidas
          - reviewed: numero human_reviewed=1 incluidas
    """
    rows = _fetch_classified_rows(conn, obra, date)
    if not rows:
        raise RuntimeError(
            f"Nenhuma classification classificada para obra={obra} data={date}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    base = f"rdo_piloto_{obra}_{date}"
    md_path = output_dir / f"{base}.md"
    md_text = render_markdown(obra, date, rows)
    md_path.write_text(md_text, encoding="utf-8")

    pdf_path: Path | None = output_dir / f"{base}.pdf"
    assert pdf_path is not None
    try:
        pdf_ok = _markdown_to_pdf(md_text, pdf_path)
    except Exception as exc:
        # weasyprint pode falhar por causa de system deps (pango/cairo ausentes)
        print(
            f"[warn] falha ao gerar PDF ({type(exc).__name__}: {exc}); "
            f"apenas markdown foi gerado.", file=sys.stderr,
        )
        pdf_ok = False
    if not pdf_ok:
        pdf_path = None

    reviewed = sum(1 for r in rows if r["human_reviewed"])
    return {
        "markdown_path": md_path,
        "pdf_path": pdf_path,
        "total": len(rows),
        "reviewed": reviewed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera RDO piloto de um dia especifico da obra.",
    )
    parser.add_argument("--obra", required=True, help="CODESC da obra")
    parser.add_argument("--data", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--output-dir", default="reports", help="Diretorio de saida (default reports/)",
    )
    args = parser.parse_args()

    from rdo_agent.utils import config
    vault_path = config.get().vault_path(args.obra)
    db_path = vault_path / "index.sqlite"
    if not db_path.exists():
        print(f"[err] banco nao encontrado: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        try:
            result = generate_rdo(
                conn, obra=args.obra, date=args.data,
                output_dir=Path(args.output_dir),
            )
        except RuntimeError as exc:
            print(f"[err] {exc}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    print(f"[ok] markdown: {result['markdown_path']}")
    if result["pdf_path"]:
        print(f"[ok] pdf:      {result['pdf_path']}")
    else:
        print("[warn] PDF nao gerado (weasyprint indisponivel ou falhou).")
    print(
        f"[ok] eventos incluidos: {result['total']} "
        f"(revisados: {result['reviewed']}, nao-revisados: "
        f"{result['total'] - result['reviewed']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
