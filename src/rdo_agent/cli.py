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
from pathlib import Path

import click
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
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
        init_db,
        mark_done,
        mark_failed,
        mark_running,
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
        ["extract_audio", "extract_document", "transcribe", "visual_analysis", "detect_quality", "ocr_first"],
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
    from rdo_agent.ocr_extractor import ocr_first_handler
    from rdo_agent.orchestrator import TaskType
    from rdo_agent.transcriber import transcribe_handler
    from rdo_agent.visual_analyzer import visual_analysis_handler

    HANDLERS = {
        TaskType.EXTRACT_AUDIO: extract_audio_handler,
        TaskType.EXTRACT_DOCUMENT: extract_document_handler,
        TaskType.TRANSCRIBE: transcribe_handler,
        TaskType.VISUAL_ANALYSIS: visual_analysis_handler,
        TaskType.DETECT_QUALITY: detect_quality_handler,
        TaskType.OCR_FIRST: ocr_first_handler,
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


@main.command(name="ocr-images")
@click.option("--obra", required=True, help="Identificador da obra")
@click.option("--limit", type=int, default=None, help="Maximo de imagens a enfileirar")
@click.option("--throttle", type=float, default=0.3, help="Pausa entre tasks (s)")
def ocr_images_cmd(obra: str, limit: int | None, throttle: float) -> None:
    """Enfileira e executa pipeline OCR-first em imagens originais (Sprint 4 Op8).

    Lista imagens com file_type='image' e derived_from IS NULL (imagens
    originais, nao frames extraidos de video) que ainda nao tenham sido
    processadas por OCR_FIRST. Para cada, enfileira task OCR_FIRST e
    roda worker com handler apropriado.
    """
    from rdo_agent.ocr_extractor import ocr_first_handler
    from rdo_agent.orchestrator import TaskType, init_db

    vault_path = config.get().vault_path(obra)
    db_path = vault_path / "index.sqlite"
    if not db_path.exists():
        console.print(f"[red]x banco nao encontrado:[/red] {db_path}")
        sys.exit(1)

    conn = init_db(vault_path)
    # Imagens originais (nao derived — exclui frames de video) sem
    # task OCR_FIRST done/running/pending. Usa LEFT JOIN em tasks
    # via json_extract no payload.
    rows = conn.execute(
        """
        SELECT f.file_id, f.file_path FROM files f
        WHERE f.obra = ?
          AND f.file_type = 'image'
          AND f.derived_from IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM tasks t
              WHERE t.obra = f.obra
                AND t.task_type = 'ocr_first'
                AND t.status IN ('pending', 'running', 'done')
                AND json_extract(t.payload, '$.file_id') = f.file_id
          )
        ORDER BY f.timestamp_resolved
        """,
        (obra,),
    ).fetchall()
    targets = list(rows)
    if limit:
        targets = targets[:limit]

    console.print(f"[bold cyan]OCR-images:[/bold cyan] {obra}")
    console.print(
        f"[bold cyan]Imagens originais sem OCR_FIRST:[/bold cyan] {len(targets)}"
    )
    if not targets:
        console.print("[yellow]Nenhuma imagem pendente.[/yellow]")
        conn.close()
        return

    enqueued = 0
    for r in targets:
        _new_task(
            conn, task_type=TaskType.OCR_FIRST,
            payload={"file_id": r["file_id"], "file_path": r["file_path"]},
            obra=obra,
        )
        enqueued += 1
    conn.close()
    console.print(f"[green]+[/green] {enqueued} task(s) enfileiradas")

    handlers_map = {TaskType.OCR_FIRST: ocr_first_handler}
    done, failed, cost_usd, avg_lat_ms, interrupted = _process_with_progress(
        vault_path=vault_path, obra=obra, handlers=handlers_map,
        task_type_filter="ocr_first", limit=limit, throttle=throttle,
    )

    summary = Table(title="Resumo ocr-images", show_header=False)
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


@main.command(name="narrate")
@click.option("--obra", required=True, help="Identificador da obra")
@click.option(
    "--dia", default=None,
    help="Data YYYY-MM-DD; se fornecido, gera narrativa do dia",
)
@click.option(
    "--scope",
    type=click.Choice(["day", "obra", "both"], case_sensitive=False),
    default=None,
    help=(
        "Escopo de geracao: 'day' (so o dia), 'obra' (so overview), "
        "'both' (ambos). Default: 'day' se --dia, senao 'obra'."
    ),
)
@click.option(
    "--skip-cache", is_flag=True, default=False,
    help="Ignora cache UNIQUE e regenera mesmo se dossier_hash ja existe",
)
@click.option(
    "--reports-root", default="reports/narratives",
    help="Diretorio raiz para arquivos markdown (default reports/narratives)",
)
@click.option(
    "--context",
    "context_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Ground Truth YAML (Sprint 5 Fase C). Se fornecido, narrator "
        "verifica corpus contra GT e marca CONFORME/DIVERGENTE/"
        "NAO VERIFICAVEL. Invalida cache automaticamente (hash muda)."
    ),
)
@click.option(
    "--min-correlation-conf",
    "min_correlation_conf",
    type=click.FloatRange(0.0, 1.0),
    default=0.70,
    show_default=True,
    help=(
        "Threshold de confidence minimo para correlacoes injetadas no "
        "dossier (divida #25). Ex: 0.80 remove correlacoes de atencao, "
        "deixando so alta confianca. 0.0 inclui tudo."
    ),
)
@click.option(
    "--adversarial", is_flag=True, default=False,
    help=(
        "Modo adversarial (Fase E): adiciona secao 'Contestacoes "
        "Hipoteticas' com argumentos que a outra parte do canal poderia "
        "levantar. Prompt version = narrator_v4_adversarial. Combinavel "
        "com --context."
    ),
)
def narrate_cmd(
    obra: str, dia: str | None, scope: str | None,
    skip_cache: bool, reports_root: str,
    context_path: Path | None,
    min_correlation_conf: float,
    adversarial: bool,
) -> None:
    """Gera narrativa forense (Sprint 5 Fase A+C) via agente Sonnet 4.6."""
    from rdo_agent.forensic_agent import (
        build_day_dossier,
        build_obra_overview_dossier,
        compute_dossier_hash,
        narrate,
        save_narrative,
        validate_narrative,
    )
    from rdo_agent.orchestrator import init_db

    # Fase C: carrega Ground Truth se --context fornecido
    gt = None
    if context_path is not None:
        if not context_path.exists():
            console.print(
                f"[red]x arquivo --context nao encontrado: {context_path}[/red]"
            )
            sys.exit(2)
        from rdo_agent.ground_truth import (
            GroundTruthValidationError, load_ground_truth,
        )
        try:
            gt = load_ground_truth(context_path)
        except GroundTruthValidationError as exc:
            console.print(
                f"[red]x Ground Truth invalido:[/red] {exc}"
            )
            sys.exit(2)
        except ImportError as exc:
            console.print(f"[red]x {exc}[/red]")
            sys.exit(3)
        console.print(
            f"[cyan]+ Ground Truth carregado:[/cyan] {context_path.name} "
            f"({len(gt.contratos)} contratos, "
            f"{len(gt.pagamentos_confirmados)} pagamentos confirmados)"
        )

    # Resolve scope default
    if scope is None:
        scope_resolved = "day" if dia else "obra"
    else:
        scope_resolved = scope.lower()

    if scope_resolved in ("day", "both") and not dia:
        console.print(
            "[red]x --dia eh obrigatorio para scope 'day' ou 'both'[/red]"
        )
        sys.exit(2)

    vault_path = config.get().vault_path(obra)
    db_path = vault_path / "index.sqlite"
    if not db_path.exists():
        console.print(f"[red]x banco nao encontrado:[/red] {db_path}")
        sys.exit(1)

    # Validate anthropic key early
    if not config.get().anthropic_api_key:
        console.print(
            "[red]x ANTHROPIC_API_KEY ausente.[/red] Configure em .env "
            "para gerar narrativas."
        )
        sys.exit(3)

    conn = init_db(vault_path)
    reports_path = Path(reports_root)
    results_summary: list[dict] = []
    total_cost = 0.0

    scopes_to_run: list[tuple[str, str | None]] = []
    if scope_resolved in ("day", "both"):
        scopes_to_run.append(("day", dia))
    if scope_resolved in ("obra", "both"):
        scopes_to_run.append(("obra_overview", None))

    for sc, ref in scopes_to_run:
        console.print(
            f"\n[bold cyan]Gerando narrativa[/bold cyan] "
            f"obra={obra} scope={sc} ref={ref or '(overview)'}"
        )
        if sc == "day":
            dossier = build_day_dossier(
                conn, obra, ref, gt=gt,
                min_correlation_confidence=min_correlation_conf,
            )
        else:
            dossier = build_obra_overview_dossier(
                conn, obra, gt=gt,
                min_correlation_confidence=min_correlation_conf,
            )
        # Fase E: injeta flag no dossier (muda hash -> invalida cache)
        if adversarial:
            dossier["adversarial"] = True

        events_count = dossier["statistics"]["events_total"]
        if events_count == 0:
            console.print(
                f"[yellow]- nenhum evento classificado para {sc} {ref}; "
                "pulando[/yellow]"
            )
            continue

        dossier_hash = compute_dossier_hash(dossier)

        # Cache check (unless skip_cache)
        if not skip_cache:
            from rdo_agent.forensic_agent.persistence import (
                _find_existing_narrative,
            )
            existing = _find_existing_narrative(
                conn, obra, sc, ref, dossier_hash,
            )
            if existing:
                console.print(
                    f"[dim]- cache hit (id={existing}); use --skip-cache "
                    "para regenerar[/dim]"
                )
                results_summary.append({
                    "scope": sc, "ref": ref, "cached": True,
                    "narrative_id": existing, "passed": None,
                })
                continue

        try:
            narration = narrate(dossier, conn)
        except Exception as exc:
            console.print(
                f"[red]x falha na geracao ({type(exc).__name__}): "
                f"{str(exc)[:200]}[/red]"
            )
            results_summary.append({
                "scope": sc, "ref": ref, "cached": False,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        validation = validate_narrative(
            narration.markdown_body, dossier,
            narration.self_assessment, narration.markdown_text,
        )

        narrative_id, path, was_cached = save_narrative(
            conn, obra=obra, scope=sc, scope_ref=ref,
            dossier_hash=dossier_hash, narration=narration,
            validation=validation, events_count=events_count,
            reports_root=reports_path,
            force=skip_cache,
        )

        # Divida #20: so conta cost se a narrativa foi efetivamente
        # persistida. Se o save retornou was_cached=True (API call
        # descartada), reporta cost 0 pra nao inflar acumulado.
        effective_cost = 0.0 if was_cached else narration.cost_usd
        total_cost += effective_cost

        results_summary.append({
            "scope": sc, "ref": ref, "cached": was_cached,
            "narrative_id": narrative_id, "path": str(path),
            "passed": validation["passed"],
            "warnings_count": len(validation["warnings"]),
            "cost_usd": effective_cost,
            "malformed": narration.is_malformed,
        })
        status = "[green]PASSED[/green]" if validation["passed"] else "[yellow]WARNINGS[/yellow]"
        console.print(
            f"[green]+[/green] id={narrative_id} path={path} "
            f"{status} cost=US$ {effective_cost:.4f}"
            + (" [dim](API call descartada — cache hit)[/dim]"
               if was_cached and narration.cost_usd > 0 else "")
        )

    conn.close()

    # Resumo final
    summary_table = Table(title="Resumo narrate", show_header=True)
    summary_table.add_column("scope", style="cyan")
    summary_table.add_column("ref")
    summary_table.add_column("status")
    summary_table.add_column("passed")
    summary_table.add_column("warnings", justify="right")
    summary_table.add_column("cost", justify="right")
    for r in results_summary:
        scope_str = r["scope"]
        ref_str = r["ref"] or "(overview)"
        if r.get("error"):
            summary_table.add_row(scope_str, ref_str, "[red]ERROR[/red]",
                                  "—", "—", "—")
        elif r["cached"]:
            summary_table.add_row(scope_str, ref_str, "[dim]cached[/dim]",
                                  "—", "—", "—")
        else:
            passed_str = "[green]YES[/green]" if r["passed"] else "[yellow]NO[/yellow]"
            summary_table.add_row(
                scope_str, ref_str, "new", passed_str,
                str(r.get("warnings_count", 0)),
                f"US$ {r.get('cost_usd', 0.0):.4f}",
            )
    console.print("")
    console.print(summary_table)
    console.print(f"\n[bold]Custo total:[/bold] US$ {total_cost:.4f}")


@main.command(name="extract-gt")
@click.option("--obra", required=True, help="Identificador da obra/canal")
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Path do YAML de saida. Default: docs/ground_truth/<OBRA>.yml"
    ),
)
@click.option(
    "--force", is_flag=True, default=False,
    help="Sobrescreve YAML existente sem confirmar.",
)
@click.option(
    "--mode",
    type=click.Choice(["simple", "adaptive"], case_sensitive=False),
    default=None,
    help=(
        "Modo de entrevista: 'simple' (questionario fixo, zero API) "
        "ou 'adaptive' (Claude Sonnet 4.6 conduz). Default: adaptive "
        "se ANTHROPIC_API_KEY configurada, senao simple."
    ),
)
def extract_gt_cmd(
    obra: str, output_path: Path | None, force: bool, mode: str | None,
) -> None:
    """Extrai Ground Truth da obra via entrevista interativa (Fase D)."""
    from rdo_agent.gt_extractor import (
        InterviewInput,
        run_adaptive_interview,
        run_simple_interview,
        write_ground_truth_yaml,
    )

    # Resolve output path default
    if output_path is None:
        output_path = Path("docs/ground_truth") / f"{obra}.yml"

    if output_path.exists() and not force:
        console.print(
            f"[yellow]- Arquivo ja existe: {output_path}[/yellow]"
        )
        confirm = click.confirm(
            "Sobrescrever?", default=False,
        )
        if not confirm:
            console.print("[dim]Abortado. Use --force para pular confirmacao.[/dim]")
            sys.exit(0)

    # Resolve mode default
    if mode is None:
        has_key = bool(config.get().anthropic_api_key)
        mode = "adaptive" if has_key else "simple"
    mode = mode.lower()

    inp = InterviewInput(
        obra=obra,
        output_path=output_path,
    )
    console.print(
        f"[bold cyan]Extracting GT[/bold cyan] obra={obra} "
        f"mode={mode} output={output_path}"
    )

    try:
        if mode == "adaptive":
            if not config.get().anthropic_api_key:
                console.print(
                    "[red]x ANTHROPIC_API_KEY ausente — "
                    "use --mode simple ou configure .env[/red]"
                )
                sys.exit(3)
            gt = run_adaptive_interview(inp)
        else:
            gt = run_simple_interview(inp)
    except KeyboardInterrupt:
        console.print("\n[yellow]- Interrompido (Ctrl+C)[/yellow]")
        sys.exit(1)
    except Exception as exc:
        console.print(
            f"[red]x Falha ({type(exc).__name__}):[/red] {exc}"
        )
        sys.exit(2)

    write_ground_truth_yaml(gt, output_path)
    console.print(
        f"\n[green]+[/green] Ground Truth salvo: {output_path}"
    )
    console.print(
        f"  obra_real: {gt.obra_real.nome}\n"
        f"  canal: {gt.canal.id} ({gt.canal.tipo})\n"
        f"  contratos: {len(gt.contratos)}\n"
        f"  pagamentos: {len(gt.pagamentos_confirmados)} conf / "
        f"{len(gt.pagamentos_pendentes)} pendentes"
    )


@main.command(name="correlate")
@click.option("--obra", required=True, help="Identificador da obra")
@click.option(
    "--rebuild", is_flag=True, default=False,
    help="Apaga correlations existentes antes de detectar (regenera do zero).",
)
@click.option(
    "--sample", "sample_n", type=int, default=5,
    help="Quantidade de correlacoes exibidas como amostra (default 5).",
)
def correlate_cmd(obra: str, rebuild: bool, sample_n: int) -> None:
    """
    Roda detectores rule-based (temporal/semantic/math) e persiste
    correlacoes. Zero chamadas a API externa.
    """
    from collections import Counter

    from rdo_agent.forensic_agent.correlator import (
        delete_correlations_for_obra,
        detect_correlations,
    )
    from rdo_agent.orchestrator import init_db

    vault_path = config.get().vault_path(obra)
    db_path = vault_path / "index.sqlite"
    if not db_path.exists():
        console.print(f"[red]x banco nao encontrado:[/red] {db_path}")
        sys.exit(1)

    conn = init_db(vault_path)
    try:
        if rebuild:
            removed = delete_correlations_for_obra(conn, obra)
            console.print(
                f"[yellow]- rebuild: {removed} correlations antigas removidas[/yellow]"
            )

        t0 = time.monotonic()
        correlations = detect_correlations(conn, obra, persist=True)
        elapsed = time.monotonic() - t0

        if not correlations:
            console.print(
                "[yellow]- nenhuma correlacao detectada (sem "
                "financial_records ou classifications)[/yellow]"
            )
            return

        # Count by type
        counts = Counter(c.correlation_type for c in correlations)
        table = Table(
            title=f"Correlacoes detectadas ({len(correlations)} total, "
                  f"{elapsed:.2f}s)",
            show_header=True,
        )
        table.add_column("tipo", style="cyan")
        table.add_column("count", justify="right")
        table.add_column("conf media", justify="right")
        for ctype, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            avg_conf = sum(
                c.confidence for c in correlations
                if c.correlation_type == ctype
            ) / n
            table.add_row(ctype, str(n), f"{avg_conf:.2f}")
        console.print("")
        console.print(table)

        # Sample (highest confidence first)
        if sample_n > 0:
            sample = sorted(
                correlations, key=lambda c: -c.confidence
            )[:sample_n]
            console.print(
                f"\n[bold]Amostra (top {len(sample)} por confidence):[/bold]"
            )
            for c in sample:
                gap = (
                    f"{c.time_gap_seconds:+d}s"
                    if c.time_gap_seconds is not None else "n/a"
                )
                console.print(
                    f"  [dim]{c.correlation_type}[/dim] "
                    f"{c.primary_event_ref} -> {c.related_event_ref} "
                    f"conf={c.confidence:.2f} gap={gap}\n"
                    f"    {c.rationale}"
                )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
