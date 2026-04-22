"""Testes do ingestor de mensagens de texto puro — Sprint 4 Op1.

Cobrem:
  - happy path: mensagens texto viram classifications com source_type=
    'text_message', semantic_status='pending_classify', quality_flag=
    'coerente' (sem passar pelo detector);
  - skip de mensagens com media_ref populado (anexos);
  - skip de metadados WhatsApp (protecao, ligacao de voz);
  - skip de content vazio ou so espaco;
  - idempotencia: 2a chamada nao duplica;
  - synthetic files row criada com file_type='message';
  - classificador semantico extendido le messages.content via
    source_message_id quando source_type='text_message'.

Todos os casos sao offline — nenhum mock de OpenAI necessario no ingestor
(nao chama API). O classificador sim usa _FakeClient.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

import pytest

from rdo_agent.classifier import semantic_classifier
from rdo_agent.classifier.semantic_classifier import classify_handler
from rdo_agent.classifier.text_message_ingestor import (
    DERIVATION_METHOD,
    QUALITY_FLAG,
    SEMANTIC_STATUS,
    SOURCE_TYPE,
    SYNTHETIC_FILE_TYPE,
    _synthetic_file_id,
    ingest_text_messages,
)
from rdo_agent.orchestrator import Task, TaskStatus, TaskType, init_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_message(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    content: str | None,
    media_ref: str | None = None,
    is_deleted: int = 0,
    sender: str = "Lucas",
    ts: str = "2026-04-08T10:00:00Z",
    obra: str = "EVERALDO",
) -> None:
    conn.execute(
        """INSERT INTO messages (
            message_id, obra, timestamp_whatsapp, sender, content,
            media_ref, is_deleted, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (message_id, obra, ts, sender, content, media_ref, is_deleted,
         "2026-04-22T00:00:00Z"),
    )
    conn.commit()


@pytest.fixture
def prepared_db(tmp_path, monkeypatch) -> sqlite3.Connection:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-key")
    from rdo_agent.utils import config as config_mod
    if hasattr(config_mod, "_cached"):
        config_mod._cached = None
    return init_db(tmp_path)


# ---------------------------------------------------------------------------
# FakeClient (para testar classificador extendido)
# ---------------------------------------------------------------------------


class _FakeUsage:
    def __init__(self, pt=100, ct=20):
        self.prompt_tokens = pt
        self.completion_tokens = ct


class _FakeChoice:
    def __init__(self, content: str):
        self.message = MagicMock()
        self.message.content = content


class _FakeCompletion:
    def __init__(self, content: str, pt=100, ct=20):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage(pt, ct)


class _FakeChatCompletions:
    def __init__(self, queue: list):
        self._queue = list(queue)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeChat:
    def __init__(self, queue: list):
        self.completions = _FakeChatCompletions(queue)


class _FakeClient:
    def __init__(self, queue: list):
        self.chat = _FakeChat(queue)


# ---------------------------------------------------------------------------
# synthetic_file_id helper
# ---------------------------------------------------------------------------


def test_synthetic_file_id_deterministic_and_prefixed():
    a = _synthetic_file_id("msg_001")
    b = _synthetic_file_id("msg_001")
    c = _synthetic_file_id("msg_002")
    assert a == b
    assert a.startswith("m_")
    assert len(a) == 14  # "m_" + 12 hex
    assert a != c


# ---------------------------------------------------------------------------
# ingest_text_messages — happy paths e filtros
# ---------------------------------------------------------------------------


def test_ingest_happy_path_text_only(prepared_db):
    _insert_message(
        prepared_db, message_id="m1",
        content="O Lucas, daqui a duas horas eu libero o caminhao",
    )
    _insert_message(
        prepared_db, message_id="m2",
        content="Manda a chave pix por favor",
    )
    stats = ingest_text_messages(prepared_db, "EVERALDO")
    assert stats == {
        "candidates": 2,
        "inserted": 2,
        "skipped_existing": 0,
        "skipped_empty": 0,
    }

    rows = prepared_db.execute(
        "SELECT source_type, source_message_id, quality_flag, "
        "semantic_status, human_review_needed, source_file_id "
        "FROM classifications ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    for r in rows:
        assert r["source_type"] == SOURCE_TYPE
        assert r["quality_flag"] == QUALITY_FLAG
        assert r["semantic_status"] == SEMANTIC_STATUS
        assert r["human_review_needed"] == 0
        assert r["source_file_id"].startswith("m_")

    # Files row sintetica
    files = prepared_db.execute(
        "SELECT file_id, file_type, file_path, derivation_method, "
        "referenced_by_message, semantic_status FROM files"
    ).fetchall()
    assert len(files) == 2
    for f in files:
        assert f["file_type"] == SYNTHETIC_FILE_TYPE
        assert f["file_path"] == ""
        assert f["derivation_method"] == DERIVATION_METHOD
        assert f["referenced_by_message"] in ("m1", "m2")
        assert f["semantic_status"] == "awaiting_classification"


def test_ingest_skips_messages_with_media_ref(prepared_db):
    _insert_message(
        prepared_db, message_id="m1", content="texto puro",
    )
    _insert_message(
        prepared_db, message_id="m2", content="foto",
        media_ref="IMG-20260408-WA0001.jpg",
    )
    _insert_message(
        prepared_db, message_id="m3", content="audio",
        media_ref="PTT-20260408-WA0001.opus",
    )
    stats = ingest_text_messages(prepared_db, "EVERALDO")
    assert stats["candidates"] == 1
    assert stats["inserted"] == 1


def test_ingest_skips_whatsapp_noise_patterns(prepared_db):
    _insert_message(
        prepared_db, message_id="m1",
        content="As mensagens e ligações são protegidas com a criptografia...",
    )
    _insert_message(
        prepared_db, message_id="m2", content="Ligação de voz perdida",
    )
    _insert_message(
        prepared_db, message_id="m3", content="Esta mensagem foi apagada",
    )
    _insert_message(
        prepared_db, message_id="m4", content="Conteudo real aproveitavel",
    )
    stats = ingest_text_messages(prepared_db, "EVERALDO")
    assert stats["candidates"] == 1
    assert stats["inserted"] == 1
    rows = prepared_db.execute(
        "SELECT source_message_id FROM classifications"
    ).fetchall()
    assert [r[0] for r in rows] == ["m4"]


def test_ingest_skips_empty_content(prepared_db):
    _insert_message(prepared_db, message_id="m1", content="")
    _insert_message(prepared_db, message_id="m2", content="   ")
    _insert_message(prepared_db, message_id="m3", content="valid")
    stats = ingest_text_messages(prepared_db, "EVERALDO")
    assert stats["candidates"] == 3
    assert stats["skipped_empty"] == 2
    assert stats["inserted"] == 1


def test_ingest_skips_deleted_messages(prepared_db):
    _insert_message(
        prepared_db, message_id="m1", content="keep me", is_deleted=0,
    )
    _insert_message(
        prepared_db, message_id="m2", content="deleted", is_deleted=1,
    )
    stats = ingest_text_messages(prepared_db, "EVERALDO")
    assert stats["candidates"] == 1


def test_ingest_isolates_by_obra(prepared_db):
    _insert_message(
        prepared_db, message_id="m_a", content="obra A msg", obra="OBRA_A",
    )
    _insert_message(
        prepared_db, message_id="m_b", content="obra B msg", obra="OBRA_B",
    )
    stats_a = ingest_text_messages(prepared_db, "OBRA_A")
    assert stats_a["inserted"] == 1
    stats_b = ingest_text_messages(prepared_db, "OBRA_B")
    assert stats_b["inserted"] == 1

    rows_a = prepared_db.execute(
        "SELECT source_message_id FROM classifications WHERE obra='OBRA_A'"
    ).fetchall()
    assert [r[0] for r in rows_a] == ["m_a"]


def test_ingest_idempotent_no_duplicates(prepared_db):
    _insert_message(
        prepared_db, message_id="m1", content="primeiro texto",
    )
    _insert_message(
        prepared_db, message_id="m2", content="segundo texto",
    )
    stats1 = ingest_text_messages(prepared_db, "EVERALDO")
    assert stats1["inserted"] == 2

    stats2 = ingest_text_messages(prepared_db, "EVERALDO")
    assert stats2["inserted"] == 0
    assert stats2["skipped_existing"] == 2

    total = prepared_db.execute(
        "SELECT COUNT(*) FROM classifications"
    ).fetchone()[0]
    assert total == 2


def test_ingest_preserves_timestamp_in_files_row(prepared_db):
    _insert_message(
        prepared_db, message_id="m1",
        content="mensagem com timestamp especifico",
        ts="2026-04-15T14:30:00Z",
    )
    ingest_text_messages(prepared_db, "EVERALDO")
    row = prepared_db.execute(
        "SELECT timestamp_resolved, timestamp_source FROM files"
    ).fetchone()
    assert row["timestamp_resolved"] == "2026-04-15T14:30:00Z"
    assert row["timestamp_source"] == "whatsapp_txt"


# ---------------------------------------------------------------------------
# classify_handler extendido — deve ler messages.content quando
# source_type='text_message'
# ---------------------------------------------------------------------------


def test_classifier_reads_message_content_for_text_message(
    prepared_db, monkeypatch,
):
    _insert_message(
        prepared_db, message_id="m1",
        content="Manda a chave pix ai pfv",
    )
    ingest_text_messages(prepared_db, "EVERALDO")

    cls_id = prepared_db.execute(
        "SELECT id FROM classifications WHERE source_message_id='m1'"
    ).fetchone()[0]

    fake = _FakeClient([
        _FakeCompletion(json.dumps({
            "categories": ["pagamento"],
            "confidence": 0.9, "reasoning": "pedido de chave pix",
        })),
    ])
    monkeypatch.setattr(
        semantic_classifier, "_get_openai_client", lambda: fake,
    )

    task = Task(
        id=1, task_type=TaskType.CLASSIFY,
        payload={"classifications_id": cls_id},
        status=TaskStatus.RUNNING, depends_on=[], obra="EVERALDO",
        created_at="2026-04-22T00:00:00Z",
    )
    classify_handler(task, prepared_db)

    # classificador deve ter recebido o content da message, nao da
    # transcriptions table
    call = fake.chat.completions.calls[0]
    user_msg = [m for m in call["messages"] if m["role"] == "user"][0]
    assert user_msg["content"] == "Manda a chave pix ai pfv"

    row = prepared_db.execute(
        "SELECT categories, semantic_status FROM classifications WHERE id=?",
        (cls_id,),
    ).fetchone()
    assert json.loads(row[0]) == ["pagamento"]
    assert row[1] == "classified"


def test_classifier_falls_back_to_transcription_when_source_type_null(
    tmp_path, monkeypatch,
):
    """
    Compatibilidade backward: linhas existentes pre-Sprint-4 podem ter
    source_type='transcription' (o unico valor antes). Extensao nao deve
    quebrar esse fluxo.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-key")
    from rdo_agent.utils import config as config_mod
    if hasattr(config_mod, "_cached"):
        config_mod._cached = None

    conn = init_db(tmp_path)
    # Seed arquivo + transcricao + classification tipo Sprint 3
    now = "2026-04-20T00:00:00Z"
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, semantic_status, created_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        ("f_aud_01", "EV", "10_media/a.opus", "audio",
         "a" * 64, 100, "done", now),
    )
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, derived_from, derivation_method, semantic_status,
        created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("f_trans_01", "EV", "20_transcriptions/a.txt", "text",
         "b" * 64, 50, "f_aud_01", "whisper-1",
         "awaiting_classification", now),
    )
    conn.execute(
        """INSERT INTO transcriptions (obra, file_id, text, language,
        confidence, low_confidence, api_call_id, created_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        ("EV", "f_trans_01", "ja coloquei a tesoura de volta",
         "portuguese", 0.6, 0, None, now),
    )
    conn.execute(
        """INSERT INTO classifications (
            obra, source_file_id, source_type,
            quality_flag, quality_reasoning, human_review_needed,
            source_sha256, semantic_status, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?)""",
        ("EV", "f_trans_01", "transcription",
         "coerente", "ok", 0, "c" * 64, "pending_classify", now),
    )
    conn.commit()

    cls_id = conn.execute(
        "SELECT id FROM classifications WHERE source_file_id='f_trans_01'"
    ).fetchone()[0]

    fake = _FakeClient([
        _FakeCompletion(json.dumps({
            "categories": ["reporte_execucao"],
            "confidence": 0.7, "reasoning": "relato de execucao",
        })),
    ])
    monkeypatch.setattr(
        semantic_classifier, "_get_openai_client", lambda: fake,
    )

    task = Task(
        id=1, task_type=TaskType.CLASSIFY,
        payload={"classifications_id": cls_id},
        status=TaskStatus.RUNNING, depends_on=[], obra="EV",
        created_at="2026-04-22T00:00:00Z",
    )
    classify_handler(task, conn)

    call = fake.chat.completions.calls[0]
    user_msg = [m for m in call["messages"] if m["role"] == "user"][0]
    assert user_msg["content"] == "ja coloquei a tesoura de volta"


def test_get_classification_text_raises_for_missing_message(prepared_db):
    from rdo_agent.classifier.semantic_classifier import (
        _get_classification_text,
    )
    with pytest.raises(RuntimeError, match="message nao encontrada"):
        _get_classification_text(
            prepared_db, obra="EVERALDO",
            source_type="text_message",
            source_file_id=None, source_message_id="mX_inexistente",
        )


def test_get_classification_text_raises_when_message_id_absent(prepared_db):
    from rdo_agent.classifier.semantic_classifier import (
        _get_classification_text,
    )
    with pytest.raises(RuntimeError, match="sem source_message_id"):
        _get_classification_text(
            prepared_db, obra="EVERALDO",
            source_type="text_message",
            source_file_id="x", source_message_id=None,
        )
