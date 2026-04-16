"""
CLI principal do rdo-agent.

Uso:
    rdo-agent ingest <zip_path> --obra <codesc>
    rdo-agent status --obra <codesc>
    rdo-agent generate-rdo --obra <codesc> --data <YYYY-MM-DD>
    rdo-agent --version
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from rdo_agent import __version__

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
    console.print(f"[bold cyan]Ingest:[/bold cyan] {zip_path}")
    console.print(f"[bold cyan]Obra:[/bold cyan]   {obra}")
    # TODO Sprint 1: chamar rdo_agent.ingestor.run_ingest(zip_path, obra, vault_root)
    console.print("[yellow]⚠ Implementação pendente (Sprint 1)[/yellow]")
    sys.exit(0)


@main.command()
@click.option("--obra", required=True, help="Identificador da obra")
def status(obra: str) -> None:
    """Mostra status de processamento da obra."""
    console.print(f"[bold cyan]Status:[/bold cyan] {obra}")
    # TODO Sprint 1: consultar orchestrator e mostrar tabela de tarefas
    console.print("[yellow]⚠ Implementação pendente (Sprint 1)[/yellow]")


@main.command(name="generate-rdo")
@click.option("--obra", required=True, help="Identificador da obra")
@click.option("--data", required=True, help="Data do RDO (YYYY-MM-DD)")
def generate_rdo(obra: str, data: str) -> None:
    """Gera RDO de um dia específico da obra."""
    console.print(f"[bold cyan]Gerar RDO:[/bold cyan] {obra} — {data}")
    # TODO Sprint 4: chamar rdo_agent.engineer.synthesize(obra, data)
    console.print("[yellow]⚠ Implementação pendente (Sprint 4)[/yellow]")


if __name__ == "__main__":
    main()
