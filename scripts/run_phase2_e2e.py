"""
E2E Fase 2 — TRANSCRIBE contra vault real.

Processa todas as tasks 'transcribe' com status 'pending' da obra informada,
chamando o transcribe_handler (que já tem retry, sentinel, logging em api_calls).
Atualiza tasks para 'running' → 'done'/'failed' e persiste no SQLite.

Uso:
    python scripts/run_phase2_e2e.py --obra EVERALDO_SANTAQUITERIA

Interruptível com Ctrl+C — estado transacional, pode retomar.
"""
from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn,
)
from rich.table import Table

from rdo_agent.orchestrator import Task, TaskStatus, TaskType
from rdo_agent.transcriber import transcribe_handler
from rdo_agent.utils import config

console = Console()
_interrupted = False


def _handle_sigint(signum, frame):
    global _interrupted
    _interrupted = True
    console.print("\n[yellow]⚠ Ctrl+C recebido — terminando task atual e saindo...[/yellow]")


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class RunStats:
    total: int = 0
    done: int = 0
    failed: int = 0
    sentinel: int = 0
    cost_usd: float = 0.0
    total_latency_ms: int = 0

    @property
    def avg_latency_ms(self) -> float:
        processed = self.done + self.failed
        return self.total_latency_ms / processed if processed else 0.0


def fetch_pending_transcribe_tasks(conn: sqlite3.Connection, obra: str):
    cur = conn.execute(
        """
        SELECT id, task_type, payload, obra, created_at, depends_on
        FROM tasks
        WHERE task_type='transcribe' AND status='pending' AND obra=?
        ORDER BY priority DESC, created_at ASC, id ASC
        """,
        (obra,),
    )
    return cur.fetchall()


def run(obra: str, throttle_sec: float = 0.3) -> RunStats:
    vault_path = config.get().vault_path(obra)
    db_path = vault_path / "index.sqlite"

    if not db_path.exists():
        console.print(f"[red]ERRO:[/red] banco não encontrado em {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    rows = fetch_pending_transcribe_tasks(conn, obra)
    stats = RunStats(total=len(rows))

    if stats.total == 0:
        console.print(f"[yellow]Nenhuma task 'transcribe' pendente para obra={obra}.[/yellow]")
        return stats

    console.print(f"[bold cyan]E2E Fase 2 — TRANSCRIBE[/bold cyan]")
    console.print(f"obra: {obra}")
    console.print(f"vault: {vault_path}")
    console.print(f"total pending: {stats.total}")
    console.print("")

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
            "[green]Transcrevendo...", total=stats.total,
            cost=0.0, latency=0.0, failed=0,
        )

        for row in rows:
            if _interrupted:
                break

            task_id = row["id"]
            started = _now_iso()
            conn.execute(
                "UPDATE tasks SET status='running', started_at=? WHERE id=?",
                (started, task_id),
            )
            conn.commit()

            task = Task(
                id=row["id"],
                task_type=TaskType.TRANSCRIBE,
                payload=json.loads(row["payload"]),
                status=TaskStatus.RUNNING,
                depends_on=json.loads(row["depends_on"]),
                obra=obra,
                created_at=row["created_at"],
            )

            try:
                t0 = time.monotonic()
                result_ref = transcribe_handler(task, conn)
                latency_ms = int((time.monotonic() - t0) * 1000)

                finished = _now_iso()
                conn.execute(
                    "UPDATE tasks SET status='done', result_ref=?, finished_at=? WHERE id=?",
                    (result_ref, finished, task_id),
                )
                conn.commit()

                stats.done += 1
                stats.total_latency_ms += latency_ms

                cost_row = conn.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM api_calls WHERE obra=?",
                    (obra,),
                ).fetchone()
                stats.cost_usd = float(cost_row[0] or 0.0)

                progress.update(
                    bar, advance=1,
                    cost=stats.cost_usd,
                    latency=stats.avg_latency_ms / 1000.0,
                    failed=stats.failed,
                )

                if throttle_sec > 0:
                    time.sleep(throttle_sec)

            except Exception as exc:
                finished = _now_iso()
                err = str(exc)[:500]
                conn.execute(
                    "UPDATE tasks SET status='failed', error_message=?, finished_at=? WHERE id=?",
                    (err, finished, task_id),
                )
                conn.commit()

                stats.failed += 1
                progress.update(
                    bar, advance=1,
                    cost=stats.cost_usd,
                    latency=stats.avg_latency_ms / 1000.0,
                    failed=stats.failed,
                )
                console.print(f"[red]✗ task {task_id} falhou:[/red] {type(exc).__name__}: {err[:160]}")

    sentinel_row = conn.execute(
        """SELECT COUNT(*) FROM transcriptions t
           JOIN files f ON f.file_id = t.file_id
           WHERE t.obra=? AND t.low_confidence=1 AND t.text=''""",
        (obra,),
    ).fetchone()
    stats.sentinel = sentinel_row[0] or 0
    conn.close()
    return stats


def print_summary(stats: RunStats) -> None:
    table = Table(title="E2E Fase 2 — TRANSCRIBE", show_header=False, show_lines=False)
    table.add_column("métrica", style="cyan", no_wrap=True)
    table.add_column("valor", style="bold")

    table.add_row("total", str(stats.total))
    table.add_row("done", f"[green]{stats.done}[/green]")
    table.add_row("sentinel (sem fala)", f"[yellow]{stats.sentinel}[/yellow]")
    table.add_row("failed", f"[red]{stats.failed}[/red]" if stats.failed else "0")
    table.add_row("custo total", f"[green]${stats.cost_usd:.4f}[/green]")
    table.add_row("latência média", f"{stats.avg_latency_ms / 1000.0:.2f}s")

    console.print("")
    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E Fase 2 — TRANSCRIBE")
    parser.add_argument("--obra", required=True, help="Identificador da obra")
    parser.add_argument(
        "--throttle", type=float, default=0.3,
        help="Pausa entre tasks em segundos (default: 0.3)",
    )
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_sigint)

    stats = run(args.obra, args.throttle)
    print_summary(stats)

    if _interrupted:
        sys.exit(130)
    if stats.failed > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
