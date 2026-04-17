"""
Ingestor — Camada 1, ponto de entrada da cadeia de custódia.

Pipeline determinístico (síncrono — ingest é atômico do ponto de vista
probatório; se parse/temporal falham, o ingest inteiro falha):

     1. validate_whatsapp_zip(zip_path)
     2. zip_sha256 = sha256_file(zip_path)              ← HASH ANTES de tudo
     3. create_vault_structure(vault_path)
     4. init_db(vault_path)
     5. detecta re-ingest:
            mesmo hash → retorna manifesto existente com was_already_ingested=True
            hash diferente → IngestConflictError
     6. shutil.copy2(zip → 00_raw/)                     ← cópia imutável
     7. zipfile.extractall(zip → 10_media/)
     8. hash + metadata de cada arquivo extraído
     9. parse_chat_file(_chat.txt)
    10. resolve_temporal por mídia (síncrono, com whatsapp_timestamp da msg)
    11. INSERT messages, files; ENQUEUE downstream tasks (PENDING)
    12. write evidence_manifest.json (versão pré-stamp)
    13. ots stamp                          ← pode falhar (não-bloqueante)
    14. git init + add + commit            ← pode falhar (não-bloqueante)
    15. re-write evidence_manifest.json com git_commit_hash + ots flags
    16. chmod 0o444 nos arquivos + 0o555 no dir 00_raw/   ← LAST

Importante sobre ordem 12-15:
    O .ots prova evidence_manifest.json no momento do stamp (versão pré-audit).
    Re-escrever depois do commit ADICIONA campos auditáveis (git hash, status
    do stamp) mas invalida tecnicamente o .ots em relação ao bytes-on-disk.
    Isto é aceito conscientemente: o auditor cruza .ots ↔ git log do commit
    apontado por git_commit_hash, que contém a versão originalmente stampada.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from rdo_agent.orchestrator import (
    DB_FILENAME,
    Task,
    TaskStatus,
    TaskType,
    enqueue,
    init_db,
)
from rdo_agent.parser import MessageType, ParsedMessage, parse_chat_file
from rdo_agent.temporal import resolve_temporal
from rdo_agent.utils.hashing import sha256_file
from rdo_agent.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

VAULT_NUMBERED_DIRS: tuple[str, ...] = (
    "00_raw",
    "10_media",
    "20_transcriptions",
    "30_visual",
    "40_events",
    "50_daily",
    "60_rdo",
)
LOG_SUBDIRS: tuple[str, ...] = ("openai_api", "anthropic_api", "execution")

MANIFEST_FILENAME = "evidence_manifest.json"
OTS_TIMEOUT_SECONDS = 30

EXT_TO_FILE_TYPE: dict[str, str] = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image",
    ".heic": "image", ".tiff": "image", ".tif": "image",
    ".mp4": "video", ".mov": "video", ".3gp": "video", ".mkv": "video",
    ".opus": "audio", ".m4a": "audio", ".mp3": "audio", ".wav": "audio",
    ".txt": "text",
    # Documentos (memoriais, cronogramas, ofícios, plantas) — Sprint 2
    # adiciona handler EXTRACT_DOCUMENT via pdfplumber (ver SPRINT2_BACKLOG.md).
    ".pdf": "document", ".doc": "document", ".docx": "document",
    ".xls": "document", ".xlsx": "document", ".odt": "document",
}

FILE_TYPE_TO_TASK: dict[str, TaskType] = {
    "audio": TaskType.TRANSCRIBE,
    "video": TaskType.EXTRACT_AUDIO,
    "image": TaskType.VISUAL_ANALYSIS,
}

FILE_TYPE_TO_SEMANTIC_STATUS: dict[str, str] = {
    "audio": "awaiting_transcription",
    "video": "awaiting_audio_extraction",
    "image": "awaiting_vision",
    "document": "awaiting_document_processing",
}


# ---------------------------------------------------------------------------
# Dataclass + exceções
# ---------------------------------------------------------------------------


class IngestConflictError(Exception):
    """A vault da obra já contém evidência de um zip diferente do fornecido."""


@dataclass
class IngestManifest:
    """
    Resultado da ingestão — serializado em 00_raw/evidence_manifest.json.

    Campos pré-stamp (presentes no .ots):
        obra, zip_path, zip_sha256, ingest_timestamp, files, messages_count
    Campos pós-stamp (acrescentados após o commit, NÃO cobertos pelo .ots):
        opentimestamps_proof, opentimestamps_pending, git_commit_hash
    Campo runtime (não persistido como True na primeira ingestão):
        was_already_ingested
    """

    obra: str
    zip_path: str
    zip_sha256: str
    ingest_timestamp: str  # ISO 8601 UTC
    files: list[dict]      # cada item: {path, sha256, size_bytes, file_type}
    messages_count: int
    opentimestamps_proof: str | None = None
    opentimestamps_pending: bool = False
    git_commit_hash: str | None = None
    was_already_ingested: bool = False

    def to_dict(self) -> dict:
        """Serialização canônica — sort_keys=True na escrita garante diff estável."""
        return asdict(self)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def run_ingest(
    zip_path: Path,
    obra: str,
    vault_root: Path,
) -> IngestManifest:
    """
    Executa o pipeline de ingestão completo.

    Raises:
        FileNotFoundError: zip não existe.
        ValueError: zip inválido (ver validate_whatsapp_zip).
        IngestConflictError: vault da obra já tem zip diferente do fornecido.
    """
    if not zip_path.exists():
        raise FileNotFoundError(f"zip não encontrado: {zip_path}")
    if not validate_whatsapp_zip(zip_path):
        raise ValueError(f"zip inválido ou sem _chat.txt: {zip_path}")

    zip_sha256 = sha256_file(zip_path)

    vault_path = vault_root / obra
    create_vault_structure(vault_path)
    raw_dir = vault_path / "00_raw"
    media_dir = vault_path / "10_media"

    # Idempotência
    existing = _load_existing_manifest(raw_dir)
    if existing is not None:
        if existing.zip_sha256 == zip_sha256:
            existing.was_already_ingested = True
            log.info(
                "ingest já realizado para %s em %s (zip %s)",
                obra, existing.ingest_timestamp, zip_sha256[:8],
            )
            return existing
        raise IngestConflictError(
            f"Vault {obra} já contém {existing.zip_sha256[:8]}, "
            f"zip fornecido é {zip_sha256[:8]}."
        )

    _ensure_writable(raw_dir)
    shutil.copy2(zip_path, raw_dir / zip_path.name)

    with zipfile.ZipFile(zip_path) as z:
        z.extractall(media_dir)

    files_meta = _collect_files_meta(media_dir)

    chat_path = _find_chat_txt(media_dir)
    messages = parse_chat_file(chat_path) if chat_path else []
    media_to_message_id = _build_media_to_message_id(messages, obra)

    conn = init_db(vault_path)
    try:
        _write_messages_to_db(conn, messages, obra)
        _write_files_to_db(conn, files_meta, media_dir, messages, media_to_message_id, obra)
        _enqueue_downstream_tasks(conn, files_meta, obra)
        conn.commit()
    finally:
        conn.close()

    manifest = IngestManifest(
        obra=obra,
        zip_path=str(zip_path),
        zip_sha256=zip_sha256,
        ingest_timestamp=_now_iso_utc(),
        files=files_meta,
        messages_count=len(messages),
    )

    manifest_path = raw_dir / MANIFEST_FILENAME
    _write_manifest(manifest, manifest_path)

    proof, pending = _ots_stamp(manifest_path)
    manifest.opentimestamps_proof = proof
    manifest.opentimestamps_pending = pending

    manifest.git_commit_hash = _setup_git(
        vault_path,
        f"ingest {obra} {zip_path.name} ({zip_sha256[:8]})",
    )

    # Reescreve manifest com campos pós-audit. Trade-off documentado no
    # docstring do módulo: invalida o .ots em relação aos bytes-on-disk,
    # mas auditor reconcilia via git_commit_hash → git log → versão original.
    _write_manifest(manifest, manifest_path)

    _make_readonly(raw_dir)
    return manifest


def validate_whatsapp_zip(zip_path: Path) -> bool:
    """
    Verifica que o zip é válido e contém ao menos um arquivo .txt no nível raiz
    (o _chat.txt do WhatsApp). Não exige nome exato — alguns exports usam
    nomes localizados como "Conversa do WhatsApp com X.txt".
    """
    if not zipfile.is_zipfile(zip_path):
        return False
    try:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                if "/" not in name and name.lower().endswith(".txt"):
                    return True
        return False
    except zipfile.BadZipFile:
        return False


def create_vault_structure(vault_path: Path) -> None:
    """
    Cria a estrutura padrão de pastas da vault (Blueprint §7.2). Idempotente.

    Cria:
        00_raw/, 10_media/, 20_transcriptions/, 30_visual/,
        40_events/, 50_daily/, 60_rdo/,
        99_logs/{openai_api,anthropic_api,execution}/

    NÃO cria:
        .obsidian/      — gerada pelo próprio Obsidian quando o usuário abre
                          a vault; criar aqui obrigaria embutir versão do app.
        .git/           — inicializada em run_ingest após o primeiro commit;
                          assim o ingest pode rodar em vault não-versionada
                          (ex: testes) sem deixar repos vazios.
        index.sqlite    — responsabilidade do orchestrator (init_db); o
                          ingestor invoca init_db separadamente para preservar
                          o princípio "schema mora com seu dono".
    """
    vault_path.mkdir(parents=True, exist_ok=True)
    for d in VAULT_NUMBERED_DIRS:
        (vault_path / d).mkdir(exist_ok=True)
    logs_dir = vault_path / "99_logs"
    logs_dir.mkdir(exist_ok=True)
    for sub in LOG_SUBDIRS:
        (logs_dir / sub).mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Subfunções (privadas)
# ---------------------------------------------------------------------------


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _find_chat_txt(media_dir: Path) -> Path | None:
    """Primeiro .txt no nível raiz (compatível com nomes localizados)."""
    candidates = sorted(p for p in media_dir.iterdir() if p.is_file() and p.suffix.lower() == ".txt")
    return candidates[0] if candidates else None


def _collect_files_meta(media_dir: Path) -> list[dict]:
    """Hash + size + tipo de cada arquivo extraído. Ordem determinística por nome."""
    out: list[dict] = []
    for f in sorted(media_dir.iterdir()):
        if not f.is_file():
            continue
        out.append({
            "path": f"10_media/{f.name}",
            "sha256": sha256_file(f),
            "size_bytes": f.stat().st_size,
            "file_type": EXT_TO_FILE_TYPE.get(f.suffix.lower(), "other"),
        })
    return out


def _message_id(obra: str, line_number: int) -> str:
    return f"msg_{obra}_L{line_number:04d}"


def _file_id(sha256: str) -> str:
    return f"f_{sha256[:12]}"


def _build_media_to_message_id(messages: list[ParsedMessage], obra: str) -> dict[str, str]:
    """media_filename → message_id. Primeira ocorrência vence."""
    out: dict[str, str] = {}
    for m in messages:
        if m.message_type == MessageType.MEDIA_REF and m.media_filename:
            out.setdefault(m.media_filename, _message_id(obra, m.line_number))
    return out


def _write_messages_to_db(conn, messages: list[ParsedMessage], obra: str) -> None:
    rows = []
    now = _now_iso_utc()
    for m in messages:
        rows.append((
            _message_id(obra, m.line_number),
            obra,
            m.timestamp.isoformat(),
            m.sender,
            m.content,
            m.media_filename,
            int(m.message_type == MessageType.DELETED),
            int(m.edited),
            int(m.message_type == MessageType.STICKER),
            m.timestamp_raw,
            now,
        ))
    conn.executemany(
        """
        INSERT INTO messages (
            message_id, obra, timestamp_whatsapp, sender, content, media_ref,
            is_deleted, is_edited, is_sticker, raw_line, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _write_files_to_db(
    conn,
    files_meta: list[dict],
    media_dir: Path,
    messages: list[ParsedMessage],
    media_to_message_id: dict[str, str],
    obra: str,
) -> None:
    """
    INSERT de cada arquivo extraído. Para cada arquivo referenciado por mensagem,
    chama resolve_temporal passando o whatsapp_timestamp da mensagem que o cita.
    """
    msg_by_filename: dict[str, ParsedMessage] = {
        m.media_filename: m
        for m in messages
        if m.message_type == MessageType.MEDIA_REF and m.media_filename
    }
    rows = []
    now = _now_iso_utc()
    for fm in files_meta:
        filename = Path(fm["path"]).name
        ref_msg = msg_by_filename.get(filename)
        wa_ts = ref_msg.timestamp if ref_msg is not None else None
        full_path = media_dir / filename
        resolution = resolve_temporal(full_path, whatsapp_timestamp=wa_ts)
        ftype = fm["file_type"]
        rows.append((
            _file_id(fm["sha256"]),
            obra,
            fm["path"],
            ftype,
            fm["sha256"],
            fm["size_bytes"],
            None,                                       # derived_from
            None,                                       # derivation_method
            media_to_message_id.get(filename),          # referenced_by_message
            resolution.timestamp_resolved.isoformat(),
            resolution.source_used.value,
            FILE_TYPE_TO_SEMANTIC_STATUS.get(ftype),
            now,
        ))
    conn.executemany(
        """
        INSERT OR IGNORE INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            derived_from, derivation_method, referenced_by_message,
            timestamp_resolved, timestamp_source, semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _enqueue_downstream_tasks(conn, files_meta: list[dict], obra: str) -> None:
    """Enfileira tasks PENDING por arquivo conforme tipo. Sprint 1 não tem handlers."""
    for fm in files_meta:
        task_type = FILE_TYPE_TO_TASK.get(fm["file_type"])
        if task_type is None:
            continue
        task = Task(
            id=None,
            task_type=task_type,
            payload={"file_id": _file_id(fm["sha256"]), "file_path": fm["path"]},
            status=TaskStatus.PENDING,
            depends_on=[],
            obra=obra,
            created_at="",  # preenchido por enqueue
        )
        enqueue(conn, task)


def _write_manifest(manifest: IngestManifest, path: Path) -> None:
    path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_existing_manifest(raw_dir: Path) -> IngestManifest | None:
    p = raw_dir / MANIFEST_FILENAME
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return IngestManifest(**data)
    except (json.JSONDecodeError, TypeError) as e:
        log.warning("manifesto existente em %s ilegível: %s", p, e)
        return None


def _ots_stamp(manifest_path: Path) -> tuple[str | None, bool]:
    """
    Invoca `ots stamp` via subprocess. Retorna (proof_filename, pending).
    pending=True quando OTS indisponível: ingest continua e auditor sabe
    que o stamp precisa ser refeito posteriormente.
    """
    try:
        result = subprocess.run(
            ["ots", "stamp", str(manifest_path)],
            timeout=OTS_TIMEOUT_SECONDS,
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        log.warning("binário 'ots' não encontrado no PATH")
        return None, True
    except subprocess.TimeoutExpired:
        log.warning("ots stamp expirou após %ds (calendar offline?)", OTS_TIMEOUT_SECONDS)
        return None, True

    ots_path = manifest_path.with_suffix(manifest_path.suffix + ".ots")
    if result.returncode == 0 and ots_path.exists():
        return ots_path.name, False
    log.warning(
        "ots stamp falhou (rc=%d): %s",
        result.returncode, result.stderr.decode(errors="replace")[:200],
    )
    return None, True


def _setup_git(vault_path: Path, commit_message: str) -> str | None:
    """
    git init (se necessário) + add + commit. Retorna hash do commit ou None
    em qualquer falha (best-effort: ingest não invalida por falta de git).
    """
    try:
        if not (vault_path / ".git").exists():
            subprocess.run(
                ["git", "init", "-q"], cwd=vault_path,
                check=True, capture_output=True,
            )
            # Identidade de commit (necessária para `git commit`); local à vault.
            subprocess.run(
                ["git", "config", "user.email", "rdo-agent@valenobre.com.br"],
                cwd=vault_path, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "RDO Agent"],
                cwd=vault_path, check=True, capture_output=True,
            )
        subprocess.run(["git", "add", "."], cwd=vault_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", commit_message],
            cwd=vault_path, check=True, capture_output=True,
        )
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=vault_path, check=True, capture_output=True,
        )
        return result.stdout.decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        log.warning("git setup falhou (%s); ingest continua sem versionamento", e)
        return None


def _ensure_writable(raw_dir: Path) -> None:
    """
    Restaura permissão de escrita em 00_raw/ — necessário se ingest anterior
    deixou tudo 0o444 e estamos em uma vault sendo recriada (cenário raro,
    mas a chmod inicial precisa não falhar).
    """
    if not raw_dir.exists():
        return
    try:
        raw_dir.chmod(0o755)
        for f in raw_dir.iterdir():
            f.chmod(0o644)
    except OSError as e:
        log.warning("não consegui restaurar escrita em %s: %s", raw_dir, e)


def _make_readonly(raw_dir: Path) -> None:
    """
    chmod 0o444 em arquivos + 0o555 no dir. ÚLTIMO passo do pipeline:
    deve vir DEPOIS do git commit, senão o git pode interpretar mal as
    permissões em filesystems não-POSIX (WSL sobre NTFS).
    """
    try:
        for f in raw_dir.iterdir():
            f.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)  # 0o444
        raw_dir.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)  # 0o555
    except OSError as e:
        log.warning("chmod read-only falhou em %s: %s", raw_dir, e)


# Re-exporta DB_FILENAME para conveniência de quem só importa o ingestor.
__all__ = [
    "DB_FILENAME",
    "IngestConflictError",
    "IngestManifest",
    "create_vault_structure",
    "run_ingest",
    "validate_whatsapp_zip",
]
