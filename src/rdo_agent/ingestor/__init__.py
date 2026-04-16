"""
Módulo de Ingestão — Camada 1.

Responsável por:
1. Receber um .zip exportado do WhatsApp
2. Calcular SHA-256 do zip antes de qualquer operação
3. Descompactar para pasta de trabalho preservando original
4. Calcular SHA-256 de cada arquivo interno
5. Gerar evidence_manifest.json
6. Carimbar hash do manifesto via OpenTimestamps
7. Marcar /00_raw/ como read-only

IMPORTANTE: esta é a primeira e mais crítica peça da cadeia de custódia.
Todo hash aqui calculado será a referência imutável para auditoria futura.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class IngestManifest:
    """Resultado da ingestão — estrutura do evidence_manifest.json."""

    obra: str
    zip_path: str
    zip_sha256: str
    ingest_timestamp: str  # ISO 8601 com timezone
    files: list[dict]  # [{"path": "...", "sha256": "...", "size_bytes": N}]
    opentimestamps_proof: str | None  # path para .ots, se carimbado

    def to_dict(self) -> dict:
        # TODO Sprint 1: implementar serialização canônica
        raise NotImplementedError


def run_ingest(
    zip_path: Path,
    obra: str,
    vault_root: Path,
) -> IngestManifest:
    """
    Executa o pipeline de ingestão completo.

    Args:
        zip_path: caminho do .zip exportado do WhatsApp
        obra: identificador da obra (ex: "CODESC_75817")
        vault_root: raiz onde criar a vault da obra

    Returns:
        IngestManifest com todos os hashes e referências

    Raises:
        FileNotFoundError: se o zip não existir
        ValueError: se o zip não for válido ou não contiver _chat.txt
        PermissionError: se não conseguir escrever na vault_root

    Pipeline:
        1. Validar zip
        2. Calcular hash do zip
        3. Criar estrutura de pastas da vault (00_raw/, 10_media/, etc.)
        4. Copiar zip para 00_raw/ (cópia imutável)
        5. Descompactar para 10_media/ (área de trabalho)
        6. Hash de cada arquivo extraído
        7. Gerar evidence_manifest.json em 00_raw/
        8. Chamar ots_stamp(manifest) → evidence_manifest.json.ots
        9. chmod 444 em 00_raw/
        10. Inicializar git repo na vault (se ainda não existe)
        11. Commit inicial: "ingest {obra} {zip_name}"
    """
    # TODO Sprint 1: implementar
    raise NotImplementedError(
        "Implementação pendente — ver docstring para pipeline completo"
    )


def validate_whatsapp_zip(zip_path: Path) -> bool:
    """
    Verifica se o zip é um export válido do WhatsApp.

    Critérios:
    - É um arquivo .zip válido
    - Contém pelo menos um _chat.txt ou arquivo .txt no nível raiz
    - Tamanho razoável (não está corrompido)
    """
    # TODO Sprint 1
    raise NotImplementedError


def create_vault_structure(vault_path: Path) -> None:
    """
    Cria a estrutura padrão de pastas de uma vault de obra.

    Estrutura criada:
        vault_path/
        ├── 00_raw/           (read-only após ingestão)
        ├── 10_media/         (mídias extraídas)
        ├── 20_transcriptions/
        ├── 30_visual/
        ├── 40_events/
        ├── 50_daily/
        ├── 60_rdo/
        ├── 99_logs/
        │   ├── openai_api/
        │   ├── anthropic_api/
        │   └── execution/
        └── index.sqlite (criado pelo orchestrator)
    """
    # TODO Sprint 1
    raise NotImplementedError
