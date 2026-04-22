"""Testes CLI rdo-agent narrate — Sprint 5 Fase A F7."""

from __future__ import annotations

import json
import sqlite3

import pytest
from click.testing import CliRunner

from rdo_agent.cli import main
from rdo_agent.forensic_agent import narrator
from rdo_agent.orchestrator import init_db
from rdo_agent.utils import config

# ---------------------------------------------------------------------------
# FakeAnthropicClient shared com test_narrator.py (duplicado aqui pra
# isolamento; mesmo shape)
# ---------------------------------------------------------------------------


class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeUsage:
    def __init__(self, pt=500, ct=800):
        self.input_tokens = pt
        self.output_tokens = ct


class _FakeMessage:
    def __init__(self, text: str, pt=500, ct=800):
        self.content = [_FakeTextBlock(text)]
        self.usage = _FakeUsage(pt, ct)
        self.stop_reason = "end_turn"


class _FakeMessages:
    def __init__(self, queue):
        self._queue = list(queue)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._queue.pop(0)


class _FakeAnthropicClient:
    def __init__(self, queue):
        self.messages = _FakeMessages(queue)


def _mock_narrative(obra="OBRA_CLI", date="2026-04-06") -> str:
    return (
        f"# Narrativa: {obra} — day {date}\n\n"
        + ("Mensagem às 09:00. transferencia PIX de R$ 3.500,00 "
           "da CONSTRUTORA para Everaldo. " * 15)
        + "\n\n---\n\n```json\n"
        '{"self_assessment": {"confidence": 0.85, "covered_all_events": true}}'
        "\n```"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_vault(tmp_path, monkeypatch, obra: str) -> sqlite3.Connection:
    settings = config.Settings(
        openai_api_key="",
        anthropic_api_key="sk-ant-test-dummy",
        claude_model="claude-sonnet-4-6",
        vaults_root=tmp_path,
        log_level="WARNING",
        dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)
    vault = tmp_path / obra
    vault.mkdir()
    conn = init_db(vault)

    # Seed minimo pra ter 1 classification
    now = "2026-04-22T00:00:00Z"
    ts = "2026-04-06T09:00:00Z"
    conn.execute(
        """INSERT INTO messages (message_id, obra, timestamp_whatsapp,
        sender, content, media_ref, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("m_1", obra, ts, "Lucas", "audio", "A.opus", now),
    )
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, referenced_by_message, timestamp_resolved,
        timestamp_source, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f_aud", obra, "10_media/a.opus", "audio", "a"*64, 100,
         "m_1", ts, "whatsapp_txt", "done", now),
    )
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, derived_from, derivation_method, referenced_by_message,
        timestamp_resolved, timestamp_source, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f_tr", obra, "20_transcriptions/t.txt", "text", "b"*64, 50,
         "f_aud", "whisper-1", "m_1", ts, "whatsapp_txt",
         "awaiting_classification", now),
    )
    conn.execute(
        """INSERT INTO transcriptions (obra, file_id, text, language,
        confidence, low_confidence, api_call_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (obra, "f_tr", "texto", "portuguese", 0.6, 0, None, now),
    )
    conn.execute(
        """INSERT INTO classifications (
            obra, source_file_id, source_type,
            quality_flag, quality_reasoning, human_review_needed,
            human_reviewed, categories, confidence_model, reasoning,
            classifier_api_call_id, classifier_model,
            quality_api_call_id, quality_model,
            source_sha256, semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (obra, "f_tr", "transcription",
         "coerente", "ok", 0, 0, json.dumps(["cronograma"]), 0.85, "x",
         None, "gpt-4o-mini", None, "gpt-4o-mini",
         "c"*64, "classified", now),
    )
    # Add financial_record pra validator testar valores_preservados
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, semantic_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f_pix", obra, "10_media/pix.jpg", "image", "p"*64, 1000,
         "ocr_extracted", now),
    )
    conn.execute(
        """INSERT INTO financial_records (obra, source_file_id, doc_type,
        valor_centavos, moeda, data_transacao, hora_transacao,
        pagador_nome, recebedor_nome, descricao, confidence,
        api_call_id, created_at)
        VALUES (?, ?, 'pix', 350000, 'BRL', '2026-04-06', '11:13:24',
                'CONSTRUTORA', 'Everaldo', 'sinal', 0.95, NULL, ?)""",
        (obra, "f_pix", now),
    )
    conn.commit()
    return conn


def _install_fake_anthropic(monkeypatch, queue):
    c = _FakeAnthropicClient(queue)
    monkeypatch.setattr(narrator, "_get_anthropic_client", lambda: c)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    conn = _seed_vault(tmp_path, monkeypatch, "OBRA_CLI")
    return tmp_path, conn


def test_narrate_day_scope_generates_narrative(seeded, monkeypatch):
    tmp_path, conn = seeded
    conn.close()
    _install_fake_anthropic(monkeypatch, [_FakeMessage(_mock_narrative())])

    runner = CliRunner()
    result = runner.invoke(main, [
        "narrate", "--obra", "OBRA_CLI", "--dia", "2026-04-06",
        "--reports-root", str(tmp_path / "reports"),
    ])
    assert result.exit_code == 0, f"stdout: {result.output}\nexc: {result.exception}"
    assert "Gerando narrativa" in result.output
    assert "day" in result.output
    # Arquivo criado
    assert (tmp_path / "reports" / "OBRA_CLI" / "day_2026-04-06.md").exists()


def test_narrate_both_runs_day_and_overview(seeded, monkeypatch):
    tmp_path, conn = seeded
    conn.close()
    # 2 calls: day + overview
    _install_fake_anthropic(monkeypatch, [
        _FakeMessage(_mock_narrative("OBRA_CLI", "2026-04-06")),
        _FakeMessage(_mock_narrative("OBRA_CLI", "overview")),
    ])

    runner = CliRunner()
    result = runner.invoke(main, [
        "narrate", "--obra", "OBRA_CLI", "--dia", "2026-04-06",
        "--scope", "both",
        "--reports-root", str(tmp_path / "reports"),
    ])
    assert result.exit_code == 0, f"output: {result.output}"
    assert (tmp_path / "reports" / "OBRA_CLI" / "day_2026-04-06.md").exists()
    assert (tmp_path / "reports" / "OBRA_CLI" / "obra_overview.md").exists()


def test_narrate_obra_scope_without_dia(seeded, monkeypatch):
    tmp_path, conn = seeded
    conn.close()
    _install_fake_anthropic(monkeypatch, [_FakeMessage(_mock_narrative())])

    runner = CliRunner()
    result = runner.invoke(main, [
        "narrate", "--obra", "OBRA_CLI", "--scope", "obra",
        "--reports-root", str(tmp_path / "reports"),
    ])
    assert result.exit_code == 0, f"output: {result.output}"
    assert (tmp_path / "reports" / "OBRA_CLI" / "obra_overview.md").exists()


def test_narrate_requires_dia_for_day_scope(seeded, monkeypatch):
    tmp_path, _ = seeded
    runner = CliRunner()
    result = runner.invoke(main, [
        "narrate", "--obra", "OBRA_CLI", "--scope", "day",
        "--reports-root", str(tmp_path / "reports"),
    ])
    assert result.exit_code == 2
    assert "--dia eh obrigatorio" in result.output


def test_narrate_cache_hit_skips_regeneration(seeded, monkeypatch):
    tmp_path, conn = seeded
    conn.close()
    _install_fake_anthropic(monkeypatch, [_FakeMessage(_mock_narrative())])

    runner = CliRunner()
    r1 = runner.invoke(main, [
        "narrate", "--obra", "OBRA_CLI", "--dia", "2026-04-06",
        "--reports-root", str(tmp_path / "reports"),
    ])
    assert r1.exit_code == 0

    # 2a chamada: cache hit, nao chama API
    # Se _FakeMessage queue vazio e narrate for chamado, IndexError
    # (pop vazio) — queremos que NAO seja chamado.
    r2 = runner.invoke(main, [
        "narrate", "--obra", "OBRA_CLI", "--dia", "2026-04-06",
        "--reports-root", str(tmp_path / "reports"),
    ])
    assert r2.exit_code == 0, f"output: {r2.output}"
    assert "cache hit" in r2.output


def test_narrate_skip_cache_regenerates(seeded, monkeypatch):
    tmp_path, conn = seeded
    conn.close()
    # 2 messages — primeira e segunda rodada
    _install_fake_anthropic(monkeypatch, [
        _FakeMessage(_mock_narrative()),
        _FakeMessage(_mock_narrative()),
    ])

    runner = CliRunner()
    r1 = runner.invoke(main, [
        "narrate", "--obra", "OBRA_CLI", "--dia", "2026-04-06",
        "--reports-root", str(tmp_path / "reports"),
    ])
    assert r1.exit_code == 0
    r2 = runner.invoke(main, [
        "narrate", "--obra", "OBRA_CLI", "--dia", "2026-04-06",
        "--skip-cache",
        "--reports-root", str(tmp_path / "reports"),
    ])
    assert r2.exit_code == 0
    assert "cache hit" not in r2.output


def test_narrate_fails_gracefully_without_anthropic_key(
    tmp_path, monkeypatch,
):
    _seed_vault(tmp_path, monkeypatch, "OBRA_NOKEY").close()
    # Override settings SEM anthropic_api_key
    settings = config.Settings(
        openai_api_key="", anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=tmp_path, log_level="WARNING", dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)

    runner = CliRunner()
    result = runner.invoke(main, [
        "narrate", "--obra", "OBRA_NOKEY", "--dia", "2026-04-06",
        "--reports-root", str(tmp_path / "reports"),
    ])
    assert result.exit_code == 3
    assert "ANTHROPIC_API_KEY ausente" in result.output
