"""Testes do narrate_streaming (Sessao 5, divida #16)."""

from __future__ import annotations

import sqlite3

import pytest

from rdo_agent.forensic_agent import narrator
from rdo_agent.forensic_agent.narrator import (
    NarrationResult,
    narrate,
    narrate_streaming,
)
from rdo_agent.orchestrator import init_db
from rdo_agent.utils import config


# ---------------------------------------------------------------------------
# Fakes para streaming
# ---------------------------------------------------------------------------


class _FakeFinalMessage:
    def __init__(self, full_text: str, input_tokens: int, output_tokens: int):
        self._full_text = full_text
        self.usage = type("Usage", (), {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })()
        self.stop_reason = "end_turn"


class _FakeStream:
    """Context manager retornando um stream com text_stream + get_final_message."""

    def __init__(self, chunks: list[str], pt: int = 100, ct: int = 200):
        self._chunks = chunks
        self._pt = pt
        self._ct = ct

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    @property
    def text_stream(self):
        for c in self._chunks:
            yield c

    def get_final_message(self) -> _FakeFinalMessage:
        return _FakeFinalMessage("".join(self._chunks), self._pt, self._ct)


class _FakeStreamingMessages:
    def __init__(self, chunks: list[str], pt: int = 100, ct: int = 200):
        self._chunks = chunks
        self._pt = pt
        self._ct = ct
        self.stream_calls: list[dict] = []

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        return _FakeStream(self._chunks, self._pt, self._ct)

    # narrate sync ainda pode ser chamado se o teste rodar caminho duplo;
    # implementamos no-op pra detectar uso indevido.
    def create(self, **kwargs):
        raise AssertionError(
            "create() chamado em teste de streaming — caller deve "
            "usar stream() em vez de create()"
        )


class _FakeStreamingClient:
    def __init__(self, chunks: list[str], pt: int = 100, ct: int = 200):
        self.messages = _FakeStreamingMessages(chunks, pt, ct)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_with_key(tmp_path, monkeypatch) -> sqlite3.Connection:
    settings = config.Settings(
        openai_api_key="",
        anthropic_api_key="sk-ant-test-dummy",
        claude_model="claude-sonnet-4-6",
        vaults_root=tmp_path,
        log_level="WARNING",
        dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)
    return init_db(tmp_path)


def _sample_dossier() -> dict:
    return {
        "obra": "OBRA_T",
        "scope": "day",
        "scope_ref": "2026-04-06",
        "statistics": {"events_total": 1},
        "financial_records": [],
        "events_timeline": [],
        "context_hints": {},
    }


def _valid_markdown(confidence: float = 0.85) -> str:
    return f"""# Narrativa: OBRA_T — day 2026-04-06

O dia apresentou eventos.

---

```json
{{
  "self_assessment": {{
    "confidence": {confidence},
    "covered_all_events": true
  }}
}}
```"""


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------


def test_narrate_streaming_yields_chunks(db_with_key, monkeypatch):
    """Cada chunk do stream deve passar pelo callback on_chunk."""
    full = _valid_markdown(0.9)
    # Quebra em 5 chunks pra simular streaming real
    quarter = max(1, len(full) // 5)
    chunks = [full[i : i + quarter] for i in range(0, len(full), quarter)]

    fake = _FakeStreamingClient(chunks, pt=120, ct=240)
    monkeypatch.setattr(narrator, "_get_anthropic_client", lambda: fake)

    received: list[str] = []
    result = narrate_streaming(
        _sample_dossier(), db_with_key, on_chunk=received.append,
    )

    assert isinstance(result, NarrationResult)
    # Recebemos chunks separados (nao a string inteira de uma vez)
    assert len(received) == len(chunks)
    # Concatenar reconstroi o texto inteiro
    assert "".join(received) == full


def test_narrate_streaming_complete_text_matches_sync(db_with_key, monkeypatch):
    """
    Stream e sync devem produzir o mesmo NarrationResult.markdown_text
    quando alimentados com a mesma resposta.
    """
    full = _valid_markdown(0.9)

    # Sync run
    from tests.test_narrator import _FakeAnthropicClient

    sync_response = type("Resp", (), {
        "content": [type("Block", (), {"type": "text", "text": full})()],
        "usage": type("U", (), {"input_tokens": 100, "output_tokens": 200})(),
        "stop_reason": "end_turn",
    })()
    sync_fake = _FakeAnthropicClient([sync_response])
    monkeypatch.setattr(narrator, "_get_anthropic_client", lambda: sync_fake)
    sync_result = narrate(_sample_dossier(), db_with_key)

    # Streaming run
    chunks = [full[i : i + 30] for i in range(0, len(full), 30)]
    stream_fake = _FakeStreamingClient(chunks, pt=100, ct=200)
    monkeypatch.setattr(narrator, "_get_anthropic_client", lambda: stream_fake)
    received: list[str] = []
    stream_result = narrate_streaming(
        _sample_dossier(), db_with_key, on_chunk=received.append,
    )

    # Ambos chegam ao mesmo conteudo
    assert sync_result.markdown_text == stream_result.markdown_text
    assert sync_result.markdown_body == stream_result.markdown_body
    assert sync_result.is_malformed == stream_result.is_malformed
    assert sync_result.self_assessment == stream_result.self_assessment


def test_narrate_streaming_persists_only_after_complete(db_with_key, monkeypatch):
    """
    A funcao em si nao chama save_narrative. Verifica que
    forensic_narratives continua vazia apos narrate_streaming
    (caller eh responsavel pela persistencia).
    """
    full = _valid_markdown(0.85)
    chunks = [full[i : i + 50] for i in range(0, len(full), 50)]
    fake = _FakeStreamingClient(chunks, pt=80, ct=160)
    monkeypatch.setattr(narrator, "_get_anthropic_client", lambda: fake)

    # Sanity: tabela vazia antes
    cur = db_with_key.cursor()
    cur.execute("SELECT COUNT(*) FROM forensic_narratives")
    assert cur.fetchone()[0] == 0

    received: list[str] = []
    result = narrate_streaming(
        _sample_dossier(), db_with_key, on_chunk=received.append,
    )

    # Persistencia continua nao acontecendo (caller eh quem grava)
    cur.execute("SELECT COUNT(*) FROM forensic_narratives")
    assert cur.fetchone()[0] == 0

    # Mas api_calls SIM deve ter 1 row (logging do call streaming)
    cur.execute("SELECT COUNT(*) FROM api_calls")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT response_json FROM api_calls")
    response_row = cur.fetchone()
    assert response_row is not None
    assert "\"streaming\": true" in response_row[0]

    # Assert que de fato recebeu chunks (sanity) e o result eh valido
    assert len(received) > 1
    assert result.markdown_text == full


def test_narrate_streaming_propagates_on_error(db_with_key, monkeypatch):
    """Erro durante o stream propaga pro caller, sem retry mascarando."""

    class _BrokenStream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        @property
        def text_stream(self):
            yield "inicio do texto..."
            raise RuntimeError("conexao caiu no meio do stream")

        def get_final_message(self):  # pragma: no cover
            raise AssertionError("nao deveria ser chamado")

    class _BrokenMessages:
        def stream(self, **kwargs):
            return _BrokenStream()

    class _BrokenClient:
        def __init__(self):
            self.messages = _BrokenMessages()

    monkeypatch.setattr(narrator, "_get_anthropic_client", lambda: _BrokenClient())

    received: list[str] = []
    with pytest.raises(RuntimeError, match="conexao caiu"):
        narrate_streaming(
            _sample_dossier(), db_with_key, on_chunk=received.append,
        )

    # Mesmo com erro, deve ter recebido o chunk anterior ao erro
    assert received == ["inicio do texto..."]

    # api_calls deve ter logado o erro
    cur = db_with_key.cursor()
    cur.execute("SELECT error_type FROM api_calls")
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] is not None  # tem error_type registrado
