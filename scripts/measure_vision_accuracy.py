"""
Mede acuracia do prompt Vision atual contra ground truth humano — Sprint 4 Op9.

Ground truth: 11 amostras (6 imagens originais + 5 frames de video) classificadas
manualmente pelo proprietario durante revisao do Op8. Este script cruza as
analises em `visual_analyses` contra as expectativas e computa:

  - match_count: Vision "bate" com categoria esperada (heuristica de texto)
  - false_off_topic: Vision retornou "nao identificado"/"nao aplicavel" mas
    amostra deveria ter conteudo util
  - divergences: amostras com 2 analyses (v1 gpt-4o-mini + v2 gpt-4o) que
    discordaram semanticamente

Uso:
    python scripts/measure_vision_accuracy.py --obra EVERALDO_SANTAQUITERIA
    python scripts/measure_vision_accuracy.py --obra EVERALDO_SANTAQUITERIA --output /tmp/op9_baseline.md

Exit codes:
    0 — OK (mesmo com baixa acuracia — sai 0 se rodou)
    1 — banco nao encontrado
    2 — amostras ausentes no DB
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

# ----------------------------------------------------------------------------
# Ground truth — hardcoded da Sprint 4 Op9 briefing (11 amostras)
# ----------------------------------------------------------------------------

# Imagens originais: (filename_substring, expected_categoria, expected_keywords)
# expected_keywords = lista de termos-chave esperados no atividade_em_curso
#                     ou elementos_construtivos do Vision pra contar como match.
GROUND_TRUTH_IMAGES: list[dict] = [
    {
        "filename": "00000094-PHOTO-2026-04-08-11-40-02.jpg",
        "expected_category": "off_topic",
        "ground_truth": "Maquina Vonder na Feicon em Sao Paulo — oferta contextual "
                        "de equipamento proprio na negociacao",
        "keywords": ["vonder", "feicon", "equipamento", "exposicao", "feira"],
        "note": "Vision V1 diz 'Nao ha atividade de construcao' — parcialmente ok "
                "mas deveria reconhecer contexto de oferta comercial",
    },
    {
        "filename": "00000146-PHOTO-2026-04-10-12-42-17.jpg",
        "expected_category": "pagamento",
        "ground_truth": "Comprovante PIX R$3.500 para Everaldo 10/04 12:42",
        "keywords": [],
        "note": "Vision V1 diz 'Nao identificado'. Este agora eh OCR-first — "
                "Vision nao deve processar.",
        "should_be_ocr": True,
    },
    {
        "filename": "00000159-PHOTO-2026-04-10-16-20-43.jpg",
        "expected_category": "especificacao_tecnica",
        "ground_truth": "Foto do projeto do telhado, alambrado e muro da quadra",
        "keywords": ["projeto", "desenho", "plano", "planta", "alambrado",
                     "muro", "quadra"],
        "note": "Vision V1 divergente (v1 execucao, v2 desenho)",
    },
    {
        "filename": "00000179-PHOTO-2026-04-14-13-43-58.jpg",
        "expected_category": "pagamento",
        "ground_truth": "Comprovante PIX R$30 gasolina/tinta 14/04",
        "keywords": [],
        "note": "Comprovante — deve ir pra OCR-first",
        "should_be_ocr": True,
    },
    {
        "filename": "00000203-PHOTO-2026-04-15-13-06-57.jpg",
        "expected_category": "reporte_execucao",
        "ground_truth": "Medicao do tubo do alambrado — altura 1,98m",
        "keywords": ["medicao", "medida", "fita", "altura", "tubo",
                     "alambrado", "metrica"],
        "note": "Multi-label: reporte_execucao, especificacao_tecnica",
    },
    {
        "filename": "00000211-PHOTO-2026-04-15-17-13-00.jpg",
        "expected_category": "reporte_execucao",
        "ground_truth": "Tesouras e tercas no lugar — etapa finalizada",
        "keywords": ["tesoura", "terca", "telhado", "estrutura", "montada",
                     "instalada"],
        "note": "Estado final — Vision atual diz 'sem atividade' mas eh reporte",
    },
]

# Frames: (frame_rel_path_substring, expected_category, keywords)
GROUND_TRUTH_FRAMES: list[dict] = [
    {
        "filename": "frames/f_ecb7374a8b76/frame_02_p050.jpg",
        "expected_category": "off_topic",
        "ground_truth": "Telha em canteiro externo alheio (Everaldo finalizando "
                        "servico de outra pessoa antes do nosso)",
        "keywords": ["telha", "canteiro"],
        "note": "Frame de video — contexto off-topic",
    },
    {
        "filename": "frames/f_1f5d5c030375/frame_02_p050.jpg",
        "expected_category": "off_topic",
        "ground_truth": "Trator tombado — acidente do Everaldo",
        "keywords": ["trator", "tombado", "acidente"],
        "note": "Acidente contextual",
    },
    {
        "filename": "frames/f_1f818f64eefa/frame_02_p050.jpg",
        "expected_category": "reporte_execucao",
        "ground_truth": "Servico do telhado — avanco na instalacao das tercas",
        "keywords": ["telhado", "terca", "instalacao", "estrutura"],
        "note": "Vision V1 diz 'nao e possivel identificar' — erro",
    },
    {
        "filename": "frames/f_445a0975174b/frame_02_p050.jpg",
        "expected_category": "material",
        "ground_truth": "Tubo e pilar metalico",
        "keywords": ["tubo", "pilar", "metal"],
        "note": "Material presente — Vision V1 diz 'sem atividade' = off_topic errado",
    },
    {
        "filename": "frames/f_e68d7a6ac115/frame_02_p050.jpg",
        "expected_category": "reporte_execucao",
        "ground_truth": "Frame ruim, mas e possivel ver o tubo do alambrado instalado",
        "keywords": ["tubo", "alambrado"],
        "note": "Frame ruim — mas info parcial existe",
    },
]

ALL_GROUND_TRUTH = GROUND_TRUTH_IMAGES + GROUND_TRUTH_FRAMES


# ----------------------------------------------------------------------------
# Heuristicas de matching
# ----------------------------------------------------------------------------


OFF_TOPIC_MARKERS: tuple[str, ...] = (
    "nao identificado",
    "não identificado",
    "nao aplicavel",
    "não aplicável",
    "nao ha atividade",
    "não há atividade",
    "sem atividade",
    "nao e possivel identificar",
    "não é possível identificar",
    "not identified",
)


def _normalize(text: str) -> str:
    """Lowercase + remove acentos basicos pra matching robusto."""
    if not text:
        return ""
    t = text.lower()
    # Remove acentos simples (portugues comum)
    for a, b in [
        ("á", "a"), ("ã", "a"), ("â", "a"), ("à", "a"),
        ("é", "e"), ("ê", "e"),
        ("í", "i"), ("î", "i"),
        ("ó", "o"), ("ô", "o"), ("õ", "o"),
        ("ú", "u"), ("ü", "u"),
        ("ç", "c"),
    ]:
        t = t.replace(a, b)
    return t


def _is_unhelpful(analysis_text: str) -> bool:
    """True se texto Vision eh esencialmente 'nao identificado'."""
    nt = _normalize(analysis_text)
    # Se >50% do texto eh marker of non-identification, considera unhelpful
    marker_hits = sum(1 for m in OFF_TOPIC_MARKERS if m in nt)
    return marker_hits >= 2 or (len(nt) < 100 and marker_hits >= 1)


def _matches_keywords(analysis_text: str, keywords: list[str]) -> int:
    """Retorna quantos keywords aparecem no texto (case/accent-insensitive)."""
    if not keywords:
        return 0
    nt = _normalize(analysis_text)
    return sum(1 for k in keywords if _normalize(k) in nt)


# ----------------------------------------------------------------------------
# DB queries
# ----------------------------------------------------------------------------


@dataclass
class Sample:
    filename: str
    expected_category: str
    ground_truth: str
    keywords: list[str]
    note: str
    should_be_ocr: bool = False
    # Preenchidos apos query:
    found_rows: list = None  # list of dicts
    best_match_keyword_hits: int = 0
    is_unhelpful: bool = False
    has_divergence: bool = False


def _find_analyses_for_filename(
    conn: sqlite3.Connection, obra: str, filename_suffix: str,
) -> list[dict]:
    """
    Busca visual_analyses cuja imagem-fonte (via join files) tenha o
    filename_suffix no file_path. Retorna lista de dicts com file_id, analysis
    text concat, created_at.
    """
    rows = conn.execute(
        """
        SELECT va.id, va.file_id AS analysis_fid, va.analysis_json,
               va.confidence, va.created_at,
               f_json.derived_from AS source_fid,
               f_src.file_path AS source_path
        FROM visual_analyses va
        LEFT JOIN files f_json ON f_json.file_id = va.file_id
        LEFT JOIN files f_src  ON f_src.file_id  = f_json.derived_from
        WHERE va.obra = ?
          AND f_src.file_path LIKE ?
        ORDER BY va.created_at
        """,
        (obra, f"%{filename_suffix}%"),
    ).fetchall()

    out = []
    for r in rows:
        try:
            analysis = json.loads(r["analysis_json"])
        except json.JSONDecodeError:
            analysis = {}
        text_fields = []
        for k in ("atividade_em_curso", "elementos_construtivos",
                  "observacoes_tecnicas", "condicoes_ambiente"):
            v = analysis.get(k)
            if v:
                text_fields.append(f"{k}: {v}")
        out.append({
            "analysis_id": r["id"],
            "analysis_fid": r["analysis_fid"],
            "source_fid": r["source_fid"],
            "source_path": r["source_path"],
            "confidence": r["confidence"],
            "created_at": r["created_at"],
            "concat_text": "\n".join(text_fields),
            "raw_analysis": analysis,
        })
    return out


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def measure(conn: sqlite3.Connection, obra: str) -> dict:
    samples: list[Sample] = []
    for gt in ALL_GROUND_TRUTH:
        s = Sample(
            filename=gt["filename"],
            expected_category=gt["expected_category"],
            ground_truth=gt["ground_truth"],
            keywords=list(gt.get("keywords") or []),
            note=gt.get("note") or "",
            should_be_ocr=bool(gt.get("should_be_ocr")),
        )
        rows = _find_analyses_for_filename(conn, obra, s.filename)
        s.found_rows = rows
        if len(rows) >= 2:
            # divergencia se concat_text dos 2 tem baixa similaridade textual
            # heuristica simples: interseccao de palavras-chave > 30%?
            a = set(_normalize(rows[0]["concat_text"]).split())
            b = set(_normalize(rows[-1]["concat_text"]).split())
            if a and b:
                inter = len(a & b) / max(len(a | b), 1)
                s.has_divergence = inter < 0.3
        if rows:
            best_hits = 0
            worst_unhelpful = False
            for r in rows:
                hits = _matches_keywords(r["concat_text"], s.keywords)
                if hits > best_hits:
                    best_hits = hits
                if _is_unhelpful(r["concat_text"]):
                    worst_unhelpful = True
            s.best_match_keyword_hits = best_hits
            s.is_unhelpful = worst_unhelpful
        samples.append(s)

    # Metrics
    total = len(samples)
    found_in_db = sum(1 for s in samples if s.found_rows)
    match_any_keyword = sum(
        1 for s in samples if s.keywords and s.best_match_keyword_hits > 0
    )
    false_off_topic = sum(
        1 for s in samples
        if s.is_unhelpful and s.expected_category not in ("off_topic", "ilegivel")
        and not s.should_be_ocr
    )
    divergences = sum(1 for s in samples if s.has_divergence)
    ocr_samples = sum(1 for s in samples if s.should_be_ocr)

    return {
        "samples": samples,
        "total": total,
        "found_in_db": found_in_db,
        "match_any_keyword": match_any_keyword,
        "false_off_topic": false_off_topic,
        "divergences": divergences,
        "ocr_samples": ocr_samples,
    }


def render_report(result: dict, obra: str, prompt_label: str) -> str:
    lines = []
    lines.append(f"# Baseline Vision Accuracy — {prompt_label}")
    lines.append("")
    lines.append(f"**Obra:** {obra}")
    lines.append(f"**Ground truth:** {result['total']} amostras (6 imagens + 5 frames)")
    lines.append("")
    lines.append("## Resumo numerico")
    lines.append("")
    lines.append(f"- Amostras encontradas em `visual_analyses`: **{result['found_in_db']}/{result['total']}**")
    lines.append(f"- Amostras com ao menos 1 keyword acertada: **{result['match_any_keyword']}**")
    lines.append(
        f"- `false_off_topic` (Vision disse 'nao identificado' mas conteudo era util): "
        f"**{result['false_off_topic']}**"
    )
    lines.append(f"- Divergencias entre v1/v2 (gpt-4o-mini vs gpt-4o): **{result['divergences']}**")
    lines.append(
        f"- Amostras que deveriam ir por OCR-first (comprovantes financeiros): "
        f"**{result['ocr_samples']}**"
    )
    lines.append("")
    lines.append("## Por amostra")
    lines.append("")
    lines.append(
        "| # | Arquivo | Esperado | Keywords hit | Unhelpful? | Divergencia? | Nota |"
    )
    lines.append("|---|---|---|---:|:---:|:---:|---|")
    for i, s in enumerate(result["samples"], 1):
        unh = "SIM" if s.is_unhelpful else "nao"
        div = "SIM" if s.has_divergence else "nao"
        hits_str = f"{s.best_match_keyword_hits}/{len(s.keywords)}" if s.keywords else "—"
        lines.append(
            f"| {i} | `{s.filename[:40]}` | `{s.expected_category}` | {hits_str} | "
            f"{unh} | {div} | {s.note[:60]} |"
        )
    lines.append("")
    lines.append("## Detalhes por amostra")
    lines.append("")
    for i, s in enumerate(result["samples"], 1):
        lines.append(f"### {i}. `{s.filename}`")
        lines.append("")
        lines.append(f"- **Ground truth:** {s.ground_truth}")
        lines.append(f"- **Expected category:** `{s.expected_category}`")
        if s.should_be_ocr:
            lines.append("- **Deveria ir por OCR-first** (comprovante financeiro)")
        if not s.found_rows:
            lines.append("- **DB:** sem rows em visual_analyses")
        else:
            for r in s.found_rows:
                snippet = r["concat_text"][:200].replace("\n", " | ")
                lines.append(
                    f"- **DB row {r['analysis_id']} ({r['created_at'][:10]}):** "
                    f"conf={r['confidence']} — {snippet}..."
                )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--obra", required=True)
    p.add_argument("--output", default="/tmp/op9_vision_baseline.md")
    p.add_argument("--prompt-label", default="Vision V1 (pre-Op9)")
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
        result = measure(conn, args.obra)
    finally:
        conn.close()

    report = render_report(result, args.obra, args.prompt_label)
    out_path = Path(args.output)
    out_path.write_text(report, encoding="utf-8")
    print(f"[ok] {args.output} ({result['total']} amostras)")
    print(f"[ok] match_any_keyword={result['match_any_keyword']}/{result['total']}")
    print(f"[ok] false_off_topic={result['false_off_topic']}")
    print(f"[ok] divergences={result['divergences']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
