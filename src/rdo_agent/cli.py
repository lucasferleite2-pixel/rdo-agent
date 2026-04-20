"""
CLI principal do rdo-agent.

Uso:
    rdo-agent ingest <zip_path> --obra <codesc>
    rdo-agent status --obra <codesc>
    rdo-agent generate-rdo --obra <codesc> --data <YYYY-MM-DD>
    rdo-agent --version
"""

from __future__ import annotations

import json
import signal
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import click
from rich.console import Console
from rich.progress import (
    BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn,
)
from rich.table import Table

from rdo_agent import __version__
from rdo_agent.ingestor import IngestConflictError, run_ingest
from rdo_agent.utils import config

console = Console()


@click.group()
@click.version_option(__version__, prog_name="rdo-agent")
def main() -> None:
    """Agente Forense de RDO — Vale Nobre."""
    pass


@main.command()
@click.argument("zip_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--obra", required=True, help="Identificador da obra (ex: CODESC_75817)")
@click.option("--vault-root", type=click.Path(path_type=Path), help="Raiz das vaults (sobrescreve .env)")
def ingest(zip_path: Path, obra: str, vault_root: Path | None) -> None:
    """Ingere um zip do WhatsApp na vault da obra especificada."""
    effective_root = vault_root or config.get().vaults_root
    console.print(f"[bold cyan]Ingest:[/bold cyan] {zip_path}")
    console.print(f"[bold cyan]Obra:[/bold cyan]   {obra}")
    console.print(f"[bold cyan]Vault:[/bold cyan]  {effective_root / obra}")
    try:
        manifest = run_ingest(zip_path, obra, effective_root)
    except IngestConflictError as e:
        console.print(f"[red]✗ Conflito de ingest:[/red] {e}")
        sys.exit(2)

    if manifest.was_already_ingested:
        console.print(
            f"[yellow]⚠ Ingest já realizado em {manifest.ingest_timestamp} "
            f"(zip {manifest.zip_sha256[:12]}…). Nada a fazer.[/yellow]"
        )
        return

    console.print(
        f"[green]✓[/green] Ingest concluído: zip "
        f"{manifest.zip_sha256[:12]}… | files: {len(manifest.files)} | "
        f"messages: {manifest.messages_count}"
    )
    if manifest.opentimestamps_pending:
        console.print(
            "[yellow]⚠ OpenTimestamps pendente — "
            "stamp não foi obtido (calendar offline?)[/yellow]"
        )
    if manifest.git_commit_hash is None:
        console.print("[yellow]⚠ git commit falhou — vault não versionada[/yellow]")


@main.command()
@click.option("--obra", required=True, help="Identificador da obra")
def status(obra: str) -> None:
    """Mostra estado de processamento da obra (tabela de tasks por tipo/status)."""
    vault_path = config.get().vault_path(obra)
    db_path = vault_path / "index.sqlite"
    if not db_path.exists():
        console.print(f"[red]✗ banco não encontrado:[/red] {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT task_type, status, COUNT(*) AS n
        FROM tasks WHERE obra = ?
        GROUP BY task_type, status
        ORDER BY task_type, status
        """,
        (obra,),
    ).fetchall()

    STATUSES = ("pending", "running", "done", "failed")
    STATUS_COLORS = ("yellow", "blue", "green", "red")
    by_type: dict[str, dict[str, int]] = {}
    for r in rows:
        by_type.setdefault(r["task_type"], {})[r["status"]] = r["n"]

    table = Table(title=f"Status — obra {obra}")
    table.add_column("task_type", style="cyan")
    for s, color in zip(STATUSES, STATUS_COLORS, strict=True):
        table.add_column(s, style=color, justify="right")

    if not by_type:
        table.add_row("(sem tasks)", "—", "—", "—", "—")
    else:
        for t in sorted(by_type):
            cells = [str(by_type[t][s]) if by_type[t].get(s) else "—" for s in STATUSES]
            table.add_row(t, *cells)

    console.print(table)

    api_row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM api_calls WHERE obra = ?",
        (obra,),
    ).fetchone()
    api_count = api_row[0] or 0
    api_cost = float(api_row[1] or 0.0)
    console.print(
        f"[bold]API calls:[/bold] {api_count} totais, "
        f"custo agregado [green]US$ {api_cost:.4f}[/green]"
    )

    window = conn.execute(
        "SELECT MIN(created_at), MAX(finished_at) FROM tasks "
        "WHERE obra = ? AND status = 'done'",
        (obra,),
    ).fetchone()
    if window and window[0] and window[1]:
        console.print(
            f"[bold]Janela de processamento:[/bold] {window[0]} → {window[1]}"
        )
    else:
        console.print("[dim]Nenhuma task concluída ainda.[/dim]")

    conn.close()


@main.command(name="generate-rdo")
@click.option("--obra", required=True, help="Identificador da obra")
@click.option("--data", required=True, help="Data do RDO (YYYY-MM-DD)")
def generate_rdo(obra: str, data: str) -> None:
    """Gera RDO de um dia específico da obra."""
    console.print(f"[bold cyan]Gerar RDO:[/bold cyan] {obra} — {data}")
    # TODO Sprint 4: chamar rdo_agent.engineer.synthesize(obra, data)
    console.print("[yellow]⚠ Implementação pendente (Sprint 4)[/yellow]")


def _fetch_next_eligible(
    conn: sqlite3.Connection,
    obra: str,
    task_type_filter: str | None,
):
    """
    Variante de next_pending com filtro opcional por task_type.

    Necessária quando --task-type é fornecido: filtrar no consumidor após
    next_pending causaria re-fetch infinito da mesma task (SQLite não tem
    SKIP LOCKED). A query é equivalente à de next_pending; só acrescenta
    AND t.task_type=? quando task_type_filter é verdadeiro.
    """
    from rdo_agent.orchestrator import Task, TaskStatus, TaskType

    sql = (
        "SELECT * FROM tasks t "
        "WHERE t.obra = ? AND t.status = 'pending' "
        "AND NOT EXISTS ("
        "    SELECT 1 FROM json_each(t.depends_on) j "
        "    LEFT JOIN tasks dep ON dep.id = CAST(j.value AS INTEGER) "
        "    WHERE dep.status IS NULL OR dep.status != 'done'"
        ")"
    )
    params: list = [obra]
    if task_type_filter:
        sql += " AND t.task_type = ?"
        params.append(task_type_filter)
    sql += " ORDER BY t.priority DESC, t.created_at ASC, t.id ASC LIMIT 1"

    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return Task(
        id=row["id"],
        task_type=TaskType(row["task_type"]),
        payload=json.loads(row["payload"]),
        status=TaskStatus(row["status"]),
        depends_on=json.loads(row["depends_on"]),
        obra=row["obra"],
        created_at=row["created_at"],
        priority=row["priority"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error_message=row["error_message"],
        result_ref=row["result_ref"],
    )


def _process_with_progress(
    vault_path: Path,
    obra: str,
    handlers: dict,
    task_type_filter: str | None,
    limit: int | None,
    throttle: float,
) -> tuple[int, int, float, float, bool]:
    """
    Wrapper local equivalente a run_worker(stop_when_empty=True) com
    progress bar, filtro por task_type, --limit, --throttle e SIGINT
    graceful. Não modifica run_worker — reimplementa o loop com as
    mesmas invariantes (next_pending → mark_running → handler → mark_done/
    failed) e respeita a ordenação do orchestrator.

    Returns:
        (done, failed, cost_total_usd, avg_latency_ms, interrupted)
    """
    import traceback

    from rdo_agent.orchestrator import (
        init_db, mark_done, mark_failed, mark_running,
    )

    state = {"interrupted": False}

    def _handle_sigint(_signum, _frame):
        state["interrupted"] = True
        console.print(
            "\n[yellow]⚠ Ctrl+C recebido — encerrando após task atual...[/yellow]"
        )

    prev_sigint = signal.signal(signal.SIGINT, _handle_sigint)

    conn = init_db(vault_path)
    done = 0
    failed = 0
    total_latency_ms = 0
    cost_total = 0.0

    pending_sql = "SELECT COUNT(*) FROM tasks WHERE obra = ? AND status = 'pending'"
    pending_params: tuple = (obra,)
    if task_type_filter:
        pending_sql += " AND task_type = ?"
        pending_params = (obra, task_type_filter)
    total_pending = conn.execute(pending_sql, pending_params).fetchone()[0]
    if limit is not None:
        total_pending = min(total_pending, limit)

    def _cost() -> float:
        r = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM api_calls WHERE obra = ?",
            (obra,),
        ).fetchone()
        return float(r[0] or 0.0)

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[cyan]{task.completed}/{task.total}"),
            TextColumn("• custo: [green]${task.fields[cost]:.4f}"),
            TextColumn("• avg: [blue]{task.fields[latency]:.1f}s"),
            TextColumn("• falhas: [red]{task.fields[failed]}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            bar = progress.add_task(
                "[green]Processando tasks...",
                total=max(total_pending, 1),
                cost=0.0, latency=0.0, failed=0,
            )

            while True:
                if state["interrupted"]:
                    break
                if limit is not None and (done + failed) >= limit:
                    break

                task = _fetch_next_eligible(conn, obra, task_type_filter)
                if task is None:
                    break
                assert task.id is not None

                mark_running(conn, task.id)
                handler = handlers.get(task.task_type)
                if handler is None:
                    msg = f"sem handler para task_type={task.task_type.value}"
                    mark_failed(conn, task.id, msg)
                    failed += 1
                    progress.update(
                        bar, advance=1, cost=_cost(), failed=failed,
                    )
                    continue

                try:
                    t0 = time.monotonic()
                    result_ref = handler(task, conn)
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    mark_done(conn, task.id, result_ref=result_ref)
                    done += 1
                    total_latency_ms += latency_ms
                except Exception as exc:
                    mark_failed(conn, task.id, traceback.format_exc())
                    failed += 1
                    console.print(
                        f"[red]✗ task {task.id} "
                        f"({task.task_type.value}) falhou:[/red] "
                        f"{type(exc).__name__}: {str(exc)[:160]}"
                    )

                avg_lat_s = (total_latency_ms / done / 1000.0) if done else 0.0
                progress.update(
                    bar, advance=1, cost=_cost(),
                    latency=avg_lat_s, failed=failed,
                )

                if throttle > 0 and not state["interrupted"]:
                    time.sleep(throttle)

        cost_total = _cost()
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
        conn.close()

    avg_lat_ms = (total_latency_ms / done) if done else 0.0
    return done, failed, cost_total, avg_lat_ms, state["interrupted"]


def _new_task(
    conn: sqlite3.Connection,
    *,
    task_type,
    payload: dict,
    obra: str,
) -> int:
    """
    Helper local que encapsula Task(...) + enqueue() para a CLI.

    O comando detect-quality (Sprint 3 Fase 1) importava new_task do
    orchestrator, mas a funcao nao existe — orchestrator expoe apenas
    enqueue(conn, task). Este wrapper restaura o call-site sem tocar
    em orchestrator/__init__.py (blacklist).
    """
    from rdo_agent.orchestrator import Task, TaskStatus, enqueue

    task = Task(
        id=None,
        task_type=task_type,
        payload=payload,
        status=TaskStatus.PENDING,
        depends_on=[],
        obra=obra,
        created_at="",
    )
    return enqueue(conn, task)


@main.command()
@click.option("--obra", required=True, help="Identificador da obra")
@click.option(
    "--task-type",
    type=click.Choice(
        ["extract_audio", "extract_document", "transcribe", "visual_analysis", "detect_quality"],
        case_sensitive=False,
    ),
    default=None,
    help="Filtrar por tipo de task específico (default: todos)",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Máximo de tasks a processar (default: sem limite)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Mostra o que seria processado sem executar",
)
@click.option(
    "--throttle",
    type=float,
    default=0.3,
    help="Pausa entre tasks em segundos (default: 0.3)",
)
def process(obra, task_type, limit, dry_run, throttle):
    """Processa tasks pendentes da obra (dispatching para handlers registrados)."""
    from rdo_agent.classifier.quality_detector import detect_quality_handler
    from rdo_agent.document_extractor import extract_document_handler
    from rdo_agent.extractor import extract_audio_handler
    from rdo_agent.orchestrator import TaskType
    from rdo_agent.transcriber import transcribe_handler
    from rdo_agent.visual_analyzer import visual_analysis_handler

    HANDLERS = {
        TaskType.EXTRACT_AUDIO: extract_audio_handler,
        TaskType.EXTRACT_DOCUMENT: extract_document_handler,
        TaskType.TRANSCRIBE: transcribe_handler,
        TaskType.VISUAL_ANALYSIS: visual_analysis_handler,
        TaskType.DETECT_QUALITY: detect_quality_handler,
    }

    task_type_norm = task_type.lower() if task_type else None

    vault_path = config.get().vault_path(obra)
    db_path = vault_path / "index.sqlite"
    if not db_path.exists():
        console.print(f"[red]✗ banco não encontrado:[/red] {db_path}")
        sys.exit(1)

    if dry_run:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        sql = (
            "SELECT task_type, COUNT(*) AS n, "
            "SUM(CASE WHEN depends_on != '[]' THEN 1 ELSE 0 END) AS dep "
            "FROM tasks WHERE obra = ? AND status = 'pending'"
        )
        params: tuple = (obra,)
        if task_type_norm:
            sql += " AND task_type = ?"
            params = (obra, task_type_norm)
        sql += " GROUP BY task_type ORDER BY task_type"
        rows = conn.execute(sql, params).fetchall()
        conn.close()

        table = Table(title=f"Dry-run — obra {obra}")
        table.add_column("task_type", style="cyan")
        table.add_column("pending", justify="right", style="yellow")
        table.add_column("com depends_on", justify="right", style="magenta")
        if not rows:
            table.add_row("(nenhuma task pendente)", "0", "0")
        else:
            for r in rows:
                table.add_row(
                    r["task_type"], str(r["n"] or 0), str(r["dep"] or 0),
                )

        console.print(table)
        console.print("[yellow]⚠ dry-run — nenhuma task executada.[/yellow]")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    pending_sql = (
        "SELECT COUNT(*) FROM tasks WHERE obra = ? AND status = 'pending'"
    )
    pending_params: tuple = (obra,)
    if task_type_norm:
        pending_sql += " AND task_type = ?"
        pending_params = (obra, task_type_norm)
    total_pending = conn.execute(pending_sql, pending_params).fetchone()[0]
    conn.close()

    console.print(f"[bold cyan]Process:[/bold cyan] {obra}")
    console.print(f"[bold cyan]Vault:[/bold cyan]   {vault_path}")
    filter_msg = (
        (f" (filtro: {task_type_norm})" if task_type_norm else "")
        + (f" (limit: {limit})" if limit else "")
    )
    console.print(f"[bold cyan]Pending:[/bold cyan] {total_pending}{filter_msg}")

    done, failed, cost_usd, avg_lat_ms, interrupted = _process_with_progress(
        vault_path=vault_path,
        obra=obra,
        handlers=HANDLERS,
        task_type_filter=task_type_norm,
        limit=limit,
        throttle=throttle,
    )

    summary = Table(title="Resumo", show_header=False)
    summary.add_column("métrica", style="cyan", no_wrap=True)
    summary.add_column("valor", style="bold")
    summary.add_row("total processado", str(done + failed))
    summary.add_row("done", f"[green]{done}[/green]")
    summary.add_row(
        "failed", f"[red]{failed}[/red]" if failed else "0",
    )
    summary.add_row("custo total", f"[green]US$ {cost_usd:.4f}[/green]")
    summary.add_row("latência média", f"{avg_lat_ms / 1000.0:.2f}s")
    console.print("")
    console.print(summary)

    if interrupted:
        sys.exit(130)
    if failed > 0:
        sys.exit(1)
    sys.exit(0)


@main.command(name="detect-quality")
@click.option("--obra", required=True, help="Identificador da obra")
@click.option("--limit", type=int, default=None, help="Maximo de transcricoes a analisar")
@click.option("--throttle", type=float, default=0.3, help="Pausa entre tasks (s)")
def detect_quality_cmd(obra: str, limit: int | None, throttle: float) -> None:
    """Enfileira e executa detector de qualidade em transcricoes sem classification."""
    from rdo_agent.classifier.quality_detector import detect_quality_handler
    from rdo_agent.orchestrator import TaskType, init_db

    vault_path = config.get().vault_path(obra)
    db_path = vault_path / "index.sqlite"
    if not db_path.exists():
        console.print(f"[red]x banco nao encontrado:[/red] {db_path}")
        sys.exit(1)

    conn = init_db(vault_path)
    rows = conn.execute(
        """
        SELECT t.file_id FROM transcriptions t
        LEFT JOIN classifications c
          ON c.obra = t.obra AND c.source_file_id = t.file_id
        WHERE t.obra = ? AND c.id IS NULL
        ORDER BY t.id
        """,
        (obra,),
    ).fetchall()
    targets = [r[0] for r in rows]
    if limit:
        targets = targets[:limit]

    console.print(f"[bold cyan]Detect-quality:[/bold cyan] {obra}")
    console.print(f"[bold cyan]Transcricoes sem classification:[/bold cyan] {len(targets)}")
    if not targets:
        console.print("[yellow]Nenhuma transcricao pendente.[/yellow]")
        conn.close()
        return

    existing = conn.execute(
        "SELECT payload FROM tasks WHERE obra = ? AND task_type = 'detect_quality' "
        "AND status IN ('pending', 'running')",
        (obra,),
    ).fetchall()
    already: set[str] = set()
    for r in existing:
        try:
            already.add(json.loads(r[0])["transcription_file_id"])
        except (KeyError, ValueError, TypeError):
            continue

    enqueued = 0
    for fid in targets:
        if fid in already:
            continue
        _new_task(
            conn,
            task_type=TaskType.DETECT_QUALITY,
            payload={"transcription_file_id": fid},
            obra=obra,
        )
        enqueued += 1
    conn.close()
    console.print(f"[green]+[/green] {enqueued} task(s) enfileiradas")

    handlers_map = {TaskType.DETECT_QUALITY: detect_quality_handler}
    done, failed, cost_usd, avg_lat_ms, interrupted = _process_with_progress(
        vault_path=vault_path, obra=obra, handlers=handlers_map,
        task_type_filter="detect_quality", limit=limit, throttle=throttle,
    )

    summary = Table(title="Resumo detect-quality", show_header=False)
    summary.add_column("metrica", style="cyan", no_wrap=True)
    summary.add_column("valor", style="bold")
    summary.add_row("done", f"[green]{done}[/green]")
    summary.add_row("failed", f"[red]{failed}[/red]" if failed else "0")
    summary.add_row("custo", f"[green]US$ {cost_usd:.4f}[/green]")
    summary.add_row("latencia media", f"{avg_lat_ms / 1000.0:.2f}s")
    console.print("")
    console.print(summary)
    if interrupted:
        sys.exit(130)
    if failed > 0:
        sys.exit(1)


@main.command(name="classify")
@click.option("--obra", required=True, help="Identificador da obra")
@click.option("--limit", type=int, default=None, help="Maximo de classifications a processar")
@click.option("--throttle", type=float, default=0.3, help="Pausa entre tasks (s)")
def classify_cmd(obra: str, limit: int | None, throttle: float) -> None:
    """Classificador semantico sobre classifications pending_classify (Sprint 3 Fase 3)."""
    from rdo_agent.classifier.semantic_classifier import classify_handler
    from rdo_agent.orchestrator import TaskType, init_db

    vault_path = config.get().vault_path(obra)
    db_path = vault_path / "index.sqlite"
    if not db_path.exists():
        console.print(f"[red]x banco nao encontrado:[/red] {db_path}")
        sys.exit(1)

    conn = init_db(vault_path)
    rows = conn.execute(
        "SELECT id FROM classifications WHERE obra = ? "
        "AND semantic_status = 'pending_classify' ORDER BY id",
        (obra,),
    ).fetchall()
    targets = [r[0] for r in rows]
    if limit:
        targets = targets[:limit]

    console.print(f"[bold cyan]Classify:[/bold cyan] {obra}")
    console.print(f"[bold cyan]Classifications pending_classify:[/bold cyan] {len(targets)}")
    if not targets:
        console.print("[yellow]Nenhuma classification pendente.[/yellow]")
        conn.close()
        return

    existing = conn.execute(
        "SELECT payload FROM tasks WHERE obra = ? AND task_type = 'classify' "
        "AND status IN ('pending', 'running')",
        (obra,),
    ).fetchall()
    already: set[int] = set()
    for r in existing:
        try:
            already.add(int(json.loads(r[0])["classifications_id"]))
        except (KeyError, ValueError, TypeError):
            continue

    enqueued = 0
    for cid in targets:
        if cid in already:
            continue
        _new_task(
            conn,
            task_type=TaskType.CLASSIFY,
            payload={"classifications_id": cid},
            obra=obra,
        )
        enqueued += 1
    conn.close()
    console.print(f"[green]+[/green] {enqueued} task(s) enfileiradas")

    handlers_map = {TaskType.CLASSIFY: classify_handler}
    done, failed, cost_usd, avg_lat_ms, interrupted = _process_with_progress(
        vault_path=vault_path, obra=obra, handlers=handlers_map,
        task_type_filter="classify", limit=limit, throttle=throttle,
    )

    summary = Table(title="Resumo classify", show_header=False)
    summary.add_column("metrica", style="cyan", no_wrap=True)
    summary.add_column("valor", style="bold")
    summary.add_row("done", f"[green]{done}[/green]")
    summary.add_row("failed", f"[red]{failed}[/red]" if failed else "0")
    summary.add_row("custo", f"[green]US$ {cost_usd:.4f}[/green]")
    summary.add_row("latencia media", f"{avg_lat_ms / 1000.0:.2f}s")
    console.print("")
    console.print(summary)
    if interrupted:
        sys.exit(130)
    if failed > 0:
        sys.exit(1)


@main.command()
@click.option("--obra", required=True, help="Identificador da obra")
def review(obra: str) -> None:
    """Revisao humana de classifications pending_review (Sprint 3 Fase 2)."""
    from rdo_agent.classifier.human_reviewer import review_pending

    vault_path = config.get().vault_path(obra)
    db_path = vault_path / "index.sqlite"
    if not db_path.exists():
        console.print(f"[red]x banco nao encontrado:[/red] {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        stats = review_pending(conn, obra)
    finally:
        conn.close()

    summary = Table(title="Resumo review", show_header=False)
    summary.add_column("metrica", style="cyan", no_wrap=True)
    summary.add_column("valor", style="bold")
    summary.add_row("total pending_review", str(stats["total"]))
    summary.add_row("accepted", f"[green]{stats['accepted']}[/green]")
    summary.add_row("edited", f"[green]{stats['edited']}[/green]")
    summary.add_row("rejected", f"[yellow]{stats['rejected']}[/yellow]")
    summary.add_row("skipped", f"[dim]{stats['skipped']}[/dim]")
    if stats["quit_early"]:
        summary.add_row("saida", "[yellow]Q (quit) pelo operador[/yellow]")
    console.print("")
    console.print(summary)


if __name__ == "__main__":
    main()
