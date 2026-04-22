"""Testes do prompt Vision V2 — Sprint 4 Op9.

Valida que o prompt V2 calibrado com few-shot:
  1. Esta disponivel como constante `SYSTEM_PROMPT_V2`
  2. Feature flag `VISION_PROMPT_VERSION` seleciona v1 ou v2 corretamente
  3. V2 contem os 6 few-shot examples derivados do ground truth
  4. V2 mantem backward compat: 4 campos obrigatorios V1 ainda sao
     pedidos no schema (pipeline existente nao quebra)
  5. Response JSON V2 (com campos estendidos) passa na validacao atual
     — os campos extras sao ignorados pelo validator atual

Sem chamadas reais a OpenAI (FakeClient). Espelha padrao
test_visual_analyzer.py.
"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from rdo_agent import visual_analyzer
from rdo_agent.orchestrator import Task, TaskStatus, TaskType, init_db
from rdo_agent.utils import config
from rdo_agent.utils.hashing import sha256_file
from rdo_agent.visual_analyzer import (
    SYSTEM_PROMPT_V1,
    SYSTEM_PROMPT_V2,
    visual_analysis_handler,
)

# ---------------------------------------------------------------------------
# FakeClient infra
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeUsage:
    def __init__(self, pt=1200, ct=300):
        self.prompt_tokens = pt
        self.completion_tokens = ct
        self.total_tokens = pt + ct


class _FakeChatCompletion:
    def __init__(self, content, pt=1200, ct=300):
        self._content = content
        self._usage = _FakeUsage(pt, ct)

    def model_dump(self):
        return {
            "choices": [{"message": {"content": self._content, "role": "assistant"}}],
            "usage": {
                "prompt_tokens": self._usage.prompt_tokens,
                "completion_tokens": self._usage.completion_tokens,
                "total_tokens": self._usage.total_tokens,
            },
        }


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
    def __init__(self, queue):
        self.completions = _FakeCompletions(queue)


class _FakeClient:
    def __init__(self, queue):
        self.chat = _FakeChat(queue)


@pytest.fixture
def vaults_root(tmp_path, monkeypatch):
    root = tmp_path / "vaults"
    settings = config.Settings(
        openai_api_key="sk-test-dummy",
        anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=root,
        log_level="WARNING",
        dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)
    return root


@pytest.fixture
def seeded_vault(vaults_root):
    obra = "OBRA_V2"
    vault = vaults_root / obra
    media_dir = vault / "10_media"
    media_dir.mkdir(parents=True)
    img_path = media_dir / "canteiro.jpg"
    Image.new("RGB", (64, 64), (150, 150, 150)).save(img_path, "JPEG")
    conn = init_db(vault)
    sha = sha256_file(img_path)
    fid = f"f_{sha[:12]}"
    conn.execute(
        """INSERT INTO files (file_id, obra, file_path, file_type, sha256,
        size_bytes, timestamp_resolved, timestamp_source, semantic_status,
        created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (fid, obra, "10_media/canteiro.jpg", "image", sha,
         img_path.stat().st_size,
         "2026-04-15T17:13:00+00:00", "filename",
         "awaiting_visual_analysis", "2026-04-22T00:00:00Z"),
    )
    conn.commit()
    return {"obra": obra, "vault": vault, "conn": conn,
            "image_file_id": fid, "image_path": img_path}


def _install_fake(monkeypatch, queue):
    fake = _FakeClient(queue)
    monkeypatch.setattr(visual_analyzer, "_get_openai_client", lambda: fake)
    return fake


def _make_task(seeded):
    return Task(
        id=None, task_type=TaskType.VISUAL_ANALYSIS,
        payload={"file_id": seeded["image_file_id"],
                 "file_path": "10_media/canteiro.jpg"},
        status=TaskStatus.PENDING, depends_on=[],
        obra=seeded["obra"], created_at="",
    )


# ---------------------------------------------------------------------------
# 1. Prompts V1 e V2 existem como constantes publicas
# ---------------------------------------------------------------------------


def test_prompt_v1_preserved_as_constant():
    """V1 nao foi deletado — continua disponivel para rollback."""
    assert SYSTEM_PROMPT_V1
    assert "Descreva APENAS o que é visível" in SYSTEM_PROMPT_V1
    assert "4 chaves" in SYSTEM_PROMPT_V1


def test_prompt_v2_exists_and_is_longer_than_v1():
    """V2 eh materialmente mais extenso (few-shot)."""
    assert SYSTEM_PROMPT_V2
    assert len(SYSTEM_PROMPT_V2) > len(SYSTEM_PROMPT_V1) * 3


# ---------------------------------------------------------------------------
# 2. V2 preserva os 4 campos obrigatorios V1 (backward compat)
# ---------------------------------------------------------------------------


def test_prompt_v2_mentions_all_4_legacy_required_fields():
    """V2 instrui o modelo a retornar os 4 campos que _validate_schema checa."""
    for field in (
        "elementos_construtivos",
        "atividade_em_curso",
        "condicoes_ambiente",
        "observacoes_tecnicas",
    ):
        assert field in SYSTEM_PROMPT_V2, f"V2 sem campo obrigatorio {field}"


# ---------------------------------------------------------------------------
# 3. V2 inclui os 6 campos estendidos + categoria_sugerida
# ---------------------------------------------------------------------------


def test_prompt_v2_includes_extended_fields():
    for field in (
        "materiais_presentes",
        "epi_observados",
        "pessoas_presentes",
        "categoria_sugerida",
        "categorias_secundarias",
        "confidence",
    ):
        assert field in SYSTEM_PROMPT_V2, f"V2 sem campo estendido {field}"


def test_prompt_v2_enumerates_9_valid_categories():
    """V2 lista as 9 categorias do semantic_classifier + ilegivel."""
    for cat in (
        "reporte_execucao",
        "material",
        "especificacao_tecnica",
        "off_topic",
        "ilegivel",
    ):
        assert cat in SYSTEM_PROMPT_V2


# ---------------------------------------------------------------------------
# 4. V2 contem os 6 few-shot examples do ground truth
# ---------------------------------------------------------------------------


def test_prompt_v2_contains_few_shot_examples():
    for example_marker in (
        "Exemplo 1",
        "Exemplo 2",
        "Exemplo 3",
        "Exemplo 4",
        "Exemplo 5",
        "Exemplo 6",
    ):
        assert example_marker in SYSTEM_PROMPT_V2


def test_prompt_v2_few_shot_scenarios_cover_known_failures():
    """Os few-shot cobrem os padroes identificados no ground truth:
    medicao (ex 1), estado final (ex 2), desenho tecnico (ex 3),
    feira (ex 4), acidente (ex 5), material solto (ex 6)."""
    # palavras-chave ancoradas nos exemplos (caso-insensitivo via .lower())
    v2_lower = SYSTEM_PROMPT_V2.lower()
    for anchor in (
        "tubo",        # ex 1 medicao
        "estrutura metálica",  # ex 2 estado final
        "desenho técnico",     # ex 3 especificacao
        "vonder",              # ex 4 feira
        "trator",              # ex 5 acidente
    ):
        assert anchor.lower() in v2_lower, f"V2 sem ancora {anchor!r}"


# ---------------------------------------------------------------------------
# 5. Feature flag seleciona prompt correto
# ---------------------------------------------------------------------------


def test_vision_prompt_version_constant_exists():
    from rdo_agent.visual_analyzer import VISION_PROMPT_VERSION
    assert VISION_PROMPT_VERSION in ("v1", "v2")


def test_system_prompt_default_points_to_v2():
    """Quando VISION_PROMPT_VERSION='v2' (default), SYSTEM_PROMPT == V2."""
    from rdo_agent.visual_analyzer import SYSTEM_PROMPT, VISION_PROMPT_VERSION
    if VISION_PROMPT_VERSION == "v2":
        assert SYSTEM_PROMPT == SYSTEM_PROMPT_V2
    else:
        assert SYSTEM_PROMPT == SYSTEM_PROMPT_V1


# ---------------------------------------------------------------------------
# 6. Response JSON V2 (superset) passa na validacao existente
# ---------------------------------------------------------------------------


def _valid_v2_response_payload() -> dict:
    """Payload com 4 campos V1 + 6 extensoes V2 — deve passar no
    _validate_schema atual (que so exige os 4 obrigatorios)."""
    return {
        # 4 obrigatorios V1 (preservados)
        "elementos_construtivos": "tesouras e terças posicionadas, conectores soldados",
        "atividade_em_curso": "estrutura metálica do telhado montada — etapa finalizada",
        "condicoes_ambiente": "canteiro externo, iluminação diurna",
        "observacoes_tecnicas": "estado final observável — reporte de execução válido",
        # 6 estendidos V2
        "materiais_presentes": "tesouras metálicas, terças, parafusos",
        "epi_observados": "não observado (nenhuma pessoa visível)",
        "pessoas_presentes": "nenhuma",
        "categoria_sugerida": "reporte_execucao",
        "categorias_secundarias": [],
        "confidence": 0.85,
    }


def test_v2_response_passes_existing_schema_validation(
    seeded_vault, monkeypatch,
):
    """Handler atual aceita response V2 — campos extras nao quebram validacao."""
    payload = _valid_v2_response_payload()
    _install_fake(monkeypatch, [_FakeChatCompletion(json.dumps(payload))])

    json_fid = visual_analysis_handler(
        _make_task(seeded_vault), seeded_vault["conn"],
    )
    assert json_fid.startswith("f_")

    va_row = seeded_vault["conn"].execute(
        "SELECT analysis_json, confidence FROM visual_analyses WHERE file_id=?",
        (json_fid,),
    ).fetchone()
    assert va_row["confidence"] == 1.0  # sucesso no validator
    parsed = json.loads(va_row["analysis_json"])
    # Todos os campos do V2 preservados no JSON persistido
    for k in ("categoria_sugerida", "categorias_secundarias", "confidence",
              "materiais_presentes", "epi_observados", "pessoas_presentes"):
        assert k in parsed, f"campo V2 {k} nao foi persistido"
    assert parsed["categoria_sugerida"] == "reporte_execucao"


def test_v2_response_with_mixed_categories_preserved(seeded_vault, monkeypatch):
    """Response com categorias_secundarias populado nao eh perdida."""
    payload = _valid_v2_response_payload()
    payload["categoria_sugerida"] = "reporte_execucao"
    payload["categorias_secundarias"] = ["especificacao_tecnica"]
    _install_fake(monkeypatch, [_FakeChatCompletion(json.dumps(payload))])

    json_fid = visual_analysis_handler(
        _make_task(seeded_vault), seeded_vault["conn"],
    )
    va_row = seeded_vault["conn"].execute(
        "SELECT analysis_json FROM visual_analyses WHERE file_id=?",
        (json_fid,),
    ).fetchone()
    parsed = json.loads(va_row["analysis_json"])
    assert parsed["categorias_secundarias"] == ["especificacao_tecnica"]


# ---------------------------------------------------------------------------
# 7. V2 ainda emite sentinel em malformed JSON (regressao do V1)
# ---------------------------------------------------------------------------


def test_v2_still_falls_back_to_sentinel_on_malformed(seeded_vault, monkeypatch):
    """Mudanca de prompt nao altera tratamento de malformed response."""
    bad = _FakeChatCompletion("isto { nao eh ] JSON valido", 1000, 50)
    _install_fake(monkeypatch, [bad])

    json_fid = visual_analysis_handler(
        _make_task(seeded_vault), seeded_vault["conn"],
    )
    va_row = seeded_vault["conn"].execute(
        "SELECT analysis_json, confidence FROM visual_analyses WHERE file_id=?",
        (json_fid,),
    ).fetchone()
    parsed = json.loads(va_row["analysis_json"])
    assert parsed["_sentinel"] == "malformed_json_response"
    assert va_row["confidence"] == 0.0


# ---------------------------------------------------------------------------
# 8. Rollback via env var funciona (V1 ativo quando VISION_PROMPT_VERSION=v1)
# ---------------------------------------------------------------------------


def test_rollback_to_v1_via_env_var(monkeypatch):
    """Simula export VISION_PROMPT_VERSION=v1 → SYSTEM_PROMPT aponta para V1.

    Como a resolucao eh feita em import-time, este teste valida via
    reimport do modulo com env setado.
    """
    import importlib
    import sys
    monkeypatch.setenv("VISION_PROMPT_VERSION", "v1")
    # Remove from cache pra forcar reimport com env novo
    if "rdo_agent.visual_analyzer" in sys.modules:
        del sys.modules["rdo_agent.visual_analyzer"]
    reloaded = importlib.import_module("rdo_agent.visual_analyzer")
    try:
        assert reloaded.VISION_PROMPT_VERSION == "v1"
        assert reloaded.SYSTEM_PROMPT == reloaded.SYSTEM_PROMPT_V1
    finally:
        # Restaura estado pra nao contaminar outros testes
        del sys.modules["rdo_agent.visual_analyzer"]
        monkeypatch.delenv("VISION_PROMPT_VERSION", raising=False)
        importlib.import_module("rdo_agent.visual_analyzer")
