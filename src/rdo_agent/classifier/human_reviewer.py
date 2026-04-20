"""
Revisor humano de classificacoes — Sprint 3 Camada 2.

CLI interativa que processa linhas em `classifications` com
`semantic_status='pending_review'`. Para cada linha exibe
quality_flag, reasoning, path do audio e transcricao, e pede
acao do usuario: [E]ditar, [A]ceitar, [R]ejeitar, [S]kip, [Q]uit.

Regras de transicao (state machine do ADR-002):
  pending_review --(accept)-->  pending_classify  (human_reviewed=1)
  pending_review --(edit)  -->  pending_classify  (human_reviewed=1,
                                                    human_corrected_text)
  pending_review --(reject)-->  rejected          (human_reviewed=1)
  pending_review --(skip)  -->  pending_review    (no-op)

NAO chama API externa. NAO reproduz audio — o operador abre
manualmente o path exibido se quiser ouvir. (Player CLI e overbuild.)

Custo esperado: ZERO.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)

VALID_ACTIONS: tuple[str, ...] = ("E", "A", "R", "S", "Q")


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _default_edit_fn(initial_text: str) -> str:
    """
    Abre $EDITOR (fallback nano) em tempfile pre-populado com initial_text.
    Retorna texto editado com whitespace final removido. Raise se $EDITOR
    retornar nao-zero; upstream pode tratar.
    """
    editor = os.environ.get("EDITOR", "nano")
    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".txt", delete=False, encoding="utf-8",
    ) as tmp:
        tmp.write(initial_text)
        tmp_path = tmp.name
    try:
        subprocess.run([editor, tmp_path], check=True)
        with open(tmp_path, encoding="utf-8") as f:
            return f.read().rstrip()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _fetch_pending_review(conn: sqlite3.Connection, obra: str) -> list[sqlite3.Row]:
    """
    Retorna rows de classifications.pending_review com transcription e
    audio path. JOIN com transcriptions (para texto) e com files duas
    vezes — uma para o .txt derivado, outra (via derived_from) para o
    .opus original.
    """
    rows = conn.execute(
        """
        SELECT
            c.id AS classification_id,
            c.source_file_id,
            c.quality_flag,
            c.quality_reasoning,
            c.human_corrected_text,
            t.text AS transcription_text,
            f_trans.file_path AS transcription_path,
            f_audio.file_path AS audio_path
        FROM classifications c
        LEFT JOIN transcriptions t
            ON t.obra = c.obra AND t.file_id = c.source_file_id
        LEFT JOIN files f_trans
            ON f_trans.file_id = c.source_file_id
        LEFT JOIN files f_audio
            ON f_audio.file_id = f_trans.derived_from
        WHERE c.obra = ? AND c.semantic_status = 'pending_review'
        ORDER BY c.id ASC
        """,
        (obra,),
    ).fetchall()
    return list(rows)


def _display_row(row: sqlite3.Row, print_fn: Callable[[str], None]) -> None:
    """Imprime cabecalho informativo de uma classification pendente."""
    print_fn("")
    print_fn("=" * 72)
    print_fn(f"classification_id: {row['classification_id']}")
    print_fn(f"flag: {row['quality_flag']}")
    print_fn(f"reasoning: {row['quality_reasoning']}")
    print_fn(f"audio_path: {row['audio_path'] or '(nao encontrado)'}")
    print_fn(f"transcription_path: {row['transcription_path'] or '(nao encontrado)'}")
    print_fn("-" * 72)
    print_fn("Texto original (transcricao):")
    print_fn(row["transcription_text"] or "(vazio)")
    if row["human_corrected_text"]:
        print_fn("-" * 72)
        print_fn("Texto corrigido previo:")
        print_fn(row["human_corrected_text"])
    print_fn("-" * 72)


def _apply_accept(conn: sqlite3.Connection, classification_id: int) -> None:
    now = _now_iso_utc()
    conn.execute(
        "UPDATE classifications SET human_reviewed=1, human_reviewed_at=?, "
        "semantic_status='pending_classify', updated_at=? WHERE id=?",
        (now, now, classification_id),
    )
    conn.commit()


def _apply_edit(
    conn: sqlite3.Connection, classification_id: int, corrected_text: str,
) -> None:
    now = _now_iso_utc()
    conn.execute(
        "UPDATE classifications SET human_reviewed=1, human_reviewed_at=?, "
        "human_corrected_text=?, semantic_status='pending_classify', "
        "updated_at=? WHERE id=?",
        (now, corrected_text, now, classification_id),
    )
    conn.commit()


def _apply_reject(conn: sqlite3.Connection, classification_id: int) -> None:
    now = _now_iso_utc()
    conn.execute(
        "UPDATE classifications SET human_reviewed=1, human_reviewed_at=?, "
        "semantic_status='rejected', updated_at=? WHERE id=?",
        (now, now, classification_id),
    )
    conn.commit()


def review_pending(
    conn: sqlite3.Connection,
    obra: str,
    *,
    prompt_fn: Callable[[str], str] | None = None,
    edit_fn: Callable[[str], str] | None = None,
    print_fn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """
    Loop interativo sobre linhas `pending_review` da obra.

    Args:
        prompt_fn: callable que recebe prompt e retorna string de acao
                   (default: builtins.input). Injetavel para testes.
        edit_fn: callable que recebe texto inicial e retorna texto
                 editado (default: abre $EDITOR em tempfile). Injetavel.
        print_fn: callable de saida (default: builtins.print). Injetavel.

    Returns:
        dict com contadores: total, accepted, edited, rejected,
        skipped, quit_early.
    """
    p_prompt = prompt_fn if prompt_fn is not None else input
    p_edit = edit_fn if edit_fn is not None else _default_edit_fn
    p_print = print_fn if print_fn is not None else print

    rows = _fetch_pending_review(conn, obra)
    stats: dict[str, Any] = {
        "total": len(rows),
        "accepted": 0,
        "edited": 0,
        "rejected": 0,
        "skipped": 0,
        "quit_early": False,
    }

    if not rows:
        p_print(f"Nenhuma classification 'pending_review' para obra={obra}.")
        return stats

    p_print(f"[info] {len(rows)} pending_review para obra={obra}.")

    for i, row in enumerate(rows, start=1):
        p_print(f"\n[{i}/{len(rows)}]")
        _display_row(row, p_print)

        action: str | None = None
        while action not in VALID_ACTIONS:
            raw = p_prompt("[E]ditar  [A]ceitar  [R]ejeitar  [S]kip  [Q]uit > ")
            if not raw:
                continue
            action = raw.strip().upper()[:1]
            if action not in VALID_ACTIONS:
                p_print(f"Acao invalida: {raw!r}. Use E/A/R/S/Q.")
                action = None

        cid = row["classification_id"]
        if action == "A":
            _apply_accept(conn, cid)
            stats["accepted"] += 1
            p_print("[+] aceita (pending_classify)")
        elif action == "E":
            initial = row["human_corrected_text"] or row["transcription_text"] or ""
            corrected = p_edit(initial)
            _apply_edit(conn, cid, corrected)
            stats["edited"] += 1
            p_print("[+] editada (pending_classify)")
        elif action == "R":
            _apply_reject(conn, cid)
            stats["rejected"] += 1
            p_print("[+] rejeitada (rejected)")
        elif action == "S":
            stats["skipped"] += 1
            p_print("[ ] pulada (permanece pending_review)")
        elif action == "Q":
            stats["quit_early"] = True
            p_print("[!] saida — estado salvo.")
            break

    return stats
