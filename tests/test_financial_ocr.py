"""Testes do financial_ocr — Sprint 4 Op8 Fase 4.

Cobre:
  - _parse_currency_to_cents (puro, sem IO) com 12+ casos
  - _coerce_record: saneamento de dict LLM
  - extract_financial_fields via FakeClient
  - save_financial_record: insert idempotente via UNIQUE

FakeClient espelha padrao do ocr_extractor / visual_analyzer.
"""

from __future__ import annotations

import json
import sqlite3

import httpx
import openai
import pytest

from rdo_agent import financial_ocr
from rdo_agent.financial_ocr import (
    FinancialRecord,
    _coerce_record,
    _parse_currency_to_cents,
    extract_financial_fields,
    save_financial_record,
)
from rdo_agent.orchestrator import init_db
from rdo_agent.utils import config


# ---------------------------------------------------------------------------
# _parse_currency_to_cents — cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("s,expected", [
    ("R$ 3.500,00", 350000),
    ("R$3.500,00", 350000),
    ("3500.00", 350000),
    ("3500,00", 350000),
    ("R$ 30", 3000),
    ("3500", 350000),
    ("3500,5", 350050),
    ("R$ 1.234.567,89", 123456789),
    ("  R$  42,30 ", 4230),
    ("1,5", 150),
    ("0,99", 99),
    ("R$ 0,01", 1),
    ("-50,00", -5000),
    ("R$3,500.00", 350000),  # en-US mix
    ("", None),
    (None, None),
    ("invalido", None),
    ("R$", None),
])
def test_parse_currency_to_cents(s, expected):
    assert _parse_currency_to_cents(s) == expected


def test_parse_currency_preserves_precision_with_3_decimals():
    """Ex: 3.500,123 -> truncate para 2 casas = 350012."""
    assert _parse_currency_to_cents("3500,123") == 350012


# ---------------------------------------------------------------------------
# _coerce_record — saneamento
# ---------------------------------------------------------------------------


def test_coerce_record_happy_path():
    parsed = {
        "doc_type": "pix",
        "valor_centavos": 350000,
        "moeda": "BRL",
        "data_transacao": "2026-04-06",
        "hora_transacao": "11:13:24",
        "pagador_nome": "Lucas",
        "pagador_doc": "***.393.776-**",
        "recebedor_nome": "Everaldo",
        "recebedor_doc": "***.456.789-**",
        "chave_pix": "everaldo@example.com",
        "descricao": "50% de sinal serralheria",
        "instituicao_origem": "BB",
        "instituicao_destino": "Itau",
        "confidence": 0.95,
    }
    r = _coerce_record(parsed)
    assert r.doc_type == "pix"
    assert r.valor_centavos == 350000
    assert r.moeda == "BRL"
    assert r.confidence == pytest.approx(0.95)
    assert r.descricao == "50% de sinal serralheria"


def test_coerce_record_doc_type_invalid_becomes_outro():
    parsed = {"doc_type": "invalido_xyz", "confidence": 0.5}
    r = _coerce_record(parsed)
    assert r.doc_type == "outro"


def test_coerce_record_valor_as_string_passes_through_currency_parser():
    parsed = {"doc_type": "pix", "valor_centavos": "R$ 3.500,00", "confidence": 0.9}
    r = _coerce_record(parsed)
    assert r.valor_centavos == 350000


def test_coerce_record_missing_fields_nullified():
    parsed = {"doc_type": "recibo", "confidence": 0.6}
    r = _coerce_record(parsed)
    assert r.valor_centavos is None
    assert r.data_transacao is None
    assert r.pagador_nome is None
    assert r.chave_pix is None


def test_coerce_record_confidence_clamped():
    parsed = {"doc_type": "pix", "confidence": 2.5}
    r = _coerce_record(parsed)
    assert r.confidence == 1.0
    parsed_neg = {"doc_type": "pix", "confidence": -0.2}
    assert _coerce_record(parsed_neg).confidence == 0.0


def test_coerce_record_moeda_defaults_brl():
    r = _coerce_record({"doc_type": "pix", "confidence": 0.5, "moeda": None})
    assert r.moeda == "BRL"


def test_coerce_record_empty_string_fields_become_none():
    parsed = {
        "doc_type": "pix", "confidence": 0.5,
        "pagador_nome": "  ", "chave_pix": "",
    }
    r = _coerce_record(parsed)
    assert r.pagador_nome is None
    assert r.chave_pix is None


def test_coerce_record_valor_as_float_rounds_to_int():
    r = _coerce_record({"doc_type": "pix", "valor_centavos": 350000.4, "confidence": 0.5})
    assert r.valor_centavos == 350000


# ---------------------------------------------------------------------------
# FakeClient para testar extract_financial_fields
# ---------------------------------------------------------------------------


def _req() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _resp(code: int) -> httpx.Response:
    return httpx.Response(status_code=code, request=_req())


def make_connection_error():
    return openai.APIConnectionError(request=_req())


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeUsage:
    def __init__(self, pt=400, ct=80):
        self.prompt_tokens = pt
        self.completion_tokens = ct
        self.total_tokens = pt + ct


class _FakeCompletion:
    def __init__(self, content, pt=400, ct=80):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage(pt, ct)


class _FakeCompletions:
    def __init__(self, queue):
        self._queue = list(queue)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, queue):
        self.chat = _FakeChat(_FakeCompletions(queue))


@pytest.fixture
def db(tmp_path, monkeypatch) -> sqlite3.Connection:
    settings = config.Settings(
        openai_api_key="sk-test-dummy",
        anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=tmp_path,
        log_level="WARNING",
        dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)
    return init_db(tmp_path)


def _seed_image(conn: sqlite3.Connection, file_id="f_image_01") -> str:
    conn.execute(
        """INSERT INTO files (
            file_id, obra, file_path, file_type, sha256, size_bytes,
            semantic_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (file_id, "OBRA_FIN", "10_media/x.jpg", "image",
         "a"*64, 100, "awaiting_classification", "2026-04-22T00:00:00Z"),
    )
    conn.commit()
    return file_id


# ---------------------------------------------------------------------------
# extract_financial_fields via FakeClient
# ---------------------------------------------------------------------------


def test_extract_financial_fields_happy_path(db, monkeypatch):
    payload = {
        "doc_type": "pix",
        "valor_centavos": 350000,
        "moeda": "BRL",
        "data_transacao": "2026-04-06",
        "hora_transacao": "11:13:24",
        "pagador_nome": "Lucas Ferreira",
        "pagador_doc": "***.393.776-**",
        "recebedor_nome": "Everaldo Santos",
        "recebedor_doc": "***.456.789-**",
        "chave_pix": "everaldo@example.com",
        "descricao": "50% sinal serralheria",
        "instituicao_origem": "BB",
        "instituicao_destino": "Itau",
        "confidence": 0.92,
    }
    fake = _FakeClient([_FakeCompletion(json.dumps(payload))])
    monkeypatch.setattr(financial_ocr, "_get_openai_client", lambda: fake)

    record, api_call_id = extract_financial_fields(
        "COMPROVANTE DE PIX\nValor: R$ 3.500,00\n...",
        obra="OBRA_FIN", conn=db,
    )
    assert isinstance(record, FinancialRecord)
    assert record.doc_type == "pix"
    assert record.valor_centavos == 350000
    assert record.descricao == "50% sinal serralheria"
    assert record.confidence == pytest.approx(0.92)

    # api_calls registered
    row = db.execute(
        "SELECT endpoint, model, error_type, cost_usd FROM api_calls WHERE id=?",
        (api_call_id,),
    ).fetchone()
    assert row["endpoint"] == "chat.completions.create"
    assert row["model"] == financial_ocr.MODEL
    assert row["error_type"] is None
    assert row["cost_usd"] > 0


def test_extract_financial_fields_non_financial_text_returns_outro(db, monkeypatch):
    """Prompt instruido a retornar doc_type=outro quando texto nao eh comprovante."""
    payload = {
        "doc_type": "outro",
        "valor_centavos": None,
        "moeda": "BRL",
        "data_transacao": None, "hora_transacao": None,
        "pagador_nome": None, "pagador_doc": None,
        "recebedor_nome": None, "recebedor_doc": None,
        "chave_pix": None, "descricao": None,
        "instituicao_origem": None, "instituicao_destino": None,
        "confidence": 0.0,
    }
    fake = _FakeClient([_FakeCompletion(json.dumps(payload))])
    monkeypatch.setattr(financial_ocr, "_get_openai_client", lambda: fake)

    record, _ = extract_financial_fields(
        "Texto sem nada financeiro aqui",
        obra="OBRA_FIN", conn=db,
    )
    assert record.doc_type == "outro"
    assert record.valor_centavos is None
    assert record.confidence == 0.0


def test_extract_financial_fields_retries_on_connection_error(db, monkeypatch):
    monkeypatch.setattr(financial_ocr, "RETRY_DELAYS_SEC", (0.0, 0.0))
    good = {
        "doc_type": "pix", "valor_centavos": 100, "moeda": "BRL",
        "data_transacao": None, "hora_transacao": None,
        "pagador_nome": None, "pagador_doc": None,
        "recebedor_nome": None, "recebedor_doc": None,
        "chave_pix": None, "descricao": None,
        "instituicao_origem": None, "instituicao_destino": None,
        "confidence": 0.8,
    }
    fake = _FakeClient([
        make_connection_error(),
        _FakeCompletion(json.dumps(good)),
    ])
    monkeypatch.setattr(financial_ocr, "_get_openai_client", lambda: fake)

    record, _ = extract_financial_fields("x", obra="OBRA_FIN", conn=db)
    assert record.doc_type == "pix"
    rows = db.execute("SELECT error_type FROM api_calls ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0]["error_type"] == "connection"
    assert rows[1]["error_type"] is None


# ---------------------------------------------------------------------------
# save_financial_record — insert + idempotencia
# ---------------------------------------------------------------------------


def test_save_financial_record_insert(db):
    _seed_image(db, "f_img_01")
    record = FinancialRecord(
        doc_type="pix", valor_centavos=350000, moeda="BRL",
        data_transacao="2026-04-06", hora_transacao="11:13:24",
        pagador_nome="Lucas", pagador_doc="***.393.776-**",
        recebedor_nome="Everaldo", recebedor_doc="***.456.789-**",
        chave_pix="everaldo@example.com",
        descricao="sinal serralheria",
        instituicao_origem="BB", instituicao_destino="Itau",
        confidence=0.9,
    )
    row_id = save_financial_record(
        db, obra="OBRA_FIN", source_file_id="f_img_01",
        raw_ocr_text="raw OCR text aqui", record=record, api_call_id=None,
    )
    assert row_id is not None

    saved = db.execute(
        "SELECT * FROM financial_records WHERE id=?", (row_id,)
    ).fetchone()
    assert saved["doc_type"] == "pix"
    assert saved["valor_centavos"] == 350000
    assert saved["raw_ocr_text"] == "raw OCR text aqui"
    assert saved["descricao"] == "sinal serralheria"


def test_save_financial_record_idempotent_via_unique(db):
    _seed_image(db, "f_img_01")
    record = FinancialRecord(
        doc_type="pix", valor_centavos=100, moeda="BRL",
        data_transacao=None, hora_transacao=None,
        pagador_nome=None, pagador_doc=None,
        recebedor_nome=None, recebedor_doc=None,
        chave_pix=None, descricao=None,
        instituicao_origem=None, instituicao_destino=None,
        confidence=0.5,
    )
    id_1 = save_financial_record(
        db, obra="OBRA_FIN", source_file_id="f_img_01",
        raw_ocr_text="v1", record=record, api_call_id=None,
    )
    id_2 = save_financial_record(
        db, obra="OBRA_FIN", source_file_id="f_img_01",
        raw_ocr_text="v2", record=record, api_call_id=None,
    )
    assert id_1 is not None
    assert id_2 is None  # 2a tentativa skippa via IntegrityError
    # so 1 row
    assert db.execute(
        "SELECT COUNT(*) FROM financial_records WHERE source_file_id=?",
        ("f_img_01",),
    ).fetchone()[0] == 1
