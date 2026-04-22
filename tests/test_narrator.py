"""Testes narrator — Sprint 5 Fase A F3.

FakeAnthropicClient espelha padrao de ocr_extractor/financial_ocr.
Nenhuma chamada real a API.
"""

from __future__ import annotations

import sqlite3

import anthropic
import httpx
import pytest

from rdo_agent.forensic_agent import narrator
from rdo_agent.forensic_agent.narrator import (
    MODEL,
    PRICING_USD_PER_TOKEN,
    PROMPT_VERSION,
    NarrationResult,
    _extract_self_assessment,
    narrate,
)
from rdo_agent.orchestrator import init_db
from rdo_agent.utils import config

# ---------------------------------------------------------------------------
# FakeAnthropicClient
# ---------------------------------------------------------------------------


def _req() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _resp(code: int) -> httpx.Response:
    return httpx.Response(status_code=code, request=_req())


def make_connection_error():
    return anthropic.APIConnectionError(request=_req())


def make_rate_limit_error():
    return anthropic.RateLimitError("rate", response=_resp(429), body=None)


def make_auth_error():
    return anthropic.AuthenticationError(
        "bad key", response=_resp(401), body=None,
    )


class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens: int = 500, output_tokens: int = 800):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeMessage:
    def __init__(
        self, text: str, input_tokens: int = 500, output_tokens: int = 800,
    ):
        self.content = [_FakeTextBlock(text)]
        self.usage = _FakeUsage(input_tokens, output_tokens)
        self.stop_reason = "end_turn"


class _FakeMessages:
    def __init__(self, queue: list):
        self._queue = list(queue)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeAnthropicClient:
    def __init__(self, queue: list):
        self.messages = _FakeMessages(queue)


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


def _install_fake(monkeypatch, queue):
    c = _FakeAnthropicClient(queue)
    monkeypatch.setattr(narrator, "_get_anthropic_client", lambda: c)
    return c


def _sample_dossier() -> dict:
    return {
        "obra": "OBRA_T",
        "scope": "day",
        "scope_ref": "2026-04-06",
        "statistics": {"events_total": 3},
        "financial_records": [],
        "events_timeline": [],
        "context_hints": {},
    }


def _valid_markdown(confidence: float = 0.85) -> str:
    return f"""# Narrativa: OBRA_T — day 2026-04-06

O dia apresentou 3 eventos registrados no pipeline.

## Destaques financeiros

Nenhum comprovante PIX registrado nessa data.

---

```json
{{
  "self_assessment": {{
    "confidence": {confidence},
    "covered_all_events": true,
    "preserved_exact_values": true,
    "marked_inferences": true,
    "chronological_integrity": true,
    "concerns": []
  }}
}}
```"""


# ---------------------------------------------------------------------------
# _extract_self_assessment (puro)
# ---------------------------------------------------------------------------


def test_extract_self_assessment_happy():
    md = _valid_markdown(0.9)
    sa, body = _extract_self_assessment(md)
    assert sa["confidence"] == 0.9
    assert sa["covered_all_events"] is True
    assert "# Narrativa:" in body
    assert "self_assessment" not in body
    assert "```json" not in body


def test_extract_self_assessment_missing_returns_empty():
    sa, body = _extract_self_assessment("# Narrativa: x\n\ntexto sem bloco")
    assert sa == {}
    assert body == "# Narrativa: x\n\ntexto sem bloco"


def test_extract_self_assessment_malformed_json_returns_empty():
    md = """# Narrativa: x

texto.

```json
{ not valid JSON here
```"""
    sa, body = _extract_self_assessment(md)
    assert sa == {}


# ---------------------------------------------------------------------------
# narrate — happy path
# ---------------------------------------------------------------------------


def test_narrate_happy_returns_result(db_with_key, monkeypatch):
    _install_fake(monkeypatch, [_FakeMessage(_valid_markdown(), 500, 800)])
    result = narrate(_sample_dossier(), db_with_key)

    assert isinstance(result, NarrationResult)
    assert result.model == MODEL
    assert result.prompt_version == PROMPT_VERSION
    assert result.prompt_tokens == 500
    assert result.completion_tokens == 800
    assert result.self_assessment["confidence"] == 0.85
    assert result.is_malformed is False
    assert "# Narrativa:" in result.markdown_body
    assert "```json" not in result.markdown_body
    # cost: 500 * 3/1M + 800 * 15/1M
    expected_cost = 500 * (3.0 / 1_000_000) + 800 * (15.0 / 1_000_000)
    assert result.cost_usd == pytest.approx(expected_cost, rel=1e-6)


def test_narrate_logs_api_call(db_with_key, monkeypatch):
    _install_fake(monkeypatch, [_FakeMessage(_valid_markdown())])
    result = narrate(_sample_dossier(), db_with_key)
    row = db_with_key.execute(
        "SELECT provider, endpoint, model, error_type, cost_usd, "
        "tokens_input, tokens_output FROM api_calls WHERE id=?",
        (result.api_call_id,),
    ).fetchone()
    assert row["provider"] == "anthropic"
    assert row["endpoint"] == "messages"
    assert row["model"] == MODEL
    assert row["error_type"] is None
    assert row["tokens_input"] == 500
    assert row["tokens_output"] == 800


def test_pricing_table_matches_constants():
    p = PRICING_USD_PER_TOKEN[MODEL]
    assert p["input"] == 3.0 / 1_000_000
    assert p["output"] == 15.0 / 1_000_000


# ---------------------------------------------------------------------------
# Malformed response
# ---------------------------------------------------------------------------


def test_narrate_malformed_marks_flag(db_with_key, monkeypatch):
    """Response sem bloco self_assessment: is_malformed=True."""
    raw = "# Narrativa: x\n\ntexto curto sem self_assessment."
    _install_fake(monkeypatch, [_FakeMessage(raw, 100, 50)])
    result = narrate(_sample_dossier(), db_with_key)
    assert result.is_malformed is True
    assert result.malformed_reason == "missing_self_assessment_block"
    assert result.self_assessment == {}
    assert result.markdown_body == raw  # body intacto


# ---------------------------------------------------------------------------
# Retry comportamento
# ---------------------------------------------------------------------------


def test_narrate_retry_on_connection_error(db_with_key, monkeypatch):
    monkeypatch.setattr(narrator, "RETRY_DELAYS_SEC", (0.0, 0.0))
    _install_fake(monkeypatch, [
        make_connection_error(),
        make_connection_error(),
        _FakeMessage(_valid_markdown()),
    ])
    result = narrate(_sample_dossier(), db_with_key)
    assert result.is_malformed is False

    rows = db_with_key.execute(
        "SELECT error_type FROM api_calls ORDER BY id"
    ).fetchall()
    assert len(rows) == 3
    assert rows[0]["error_type"] == "connection"
    assert rows[1]["error_type"] == "connection"
    assert rows[2]["error_type"] is None


def test_narrate_auth_error_propagates(db_with_key, monkeypatch):
    monkeypatch.setattr(narrator, "RETRY_DELAYS_SEC", (0.0, 0.0))
    _install_fake(monkeypatch, [make_auth_error()])
    with pytest.raises(anthropic.AuthenticationError):
        narrate(_sample_dossier(), db_with_key)
    rows = db_with_key.execute(
        "SELECT error_type FROM api_calls"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["error_type"] == "auth_error"


# ---------------------------------------------------------------------------
# ANTHROPIC_API_KEY ausente
# ---------------------------------------------------------------------------


def test_get_anthropic_client_raises_if_no_key(tmp_path, monkeypatch):
    settings = config.Settings(
        openai_api_key="", anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=tmp_path, log_level="WARNING", dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY ausente"):
        narrator._get_anthropic_client()
