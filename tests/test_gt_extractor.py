"""Testes gt_extractor — Fase D1 (sincrono) + D2 (adaptativo mockado)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rdo_agent.ground_truth import GroundTruth, load_ground_truth
from rdo_agent.gt_extractor import (
    InterviewInput,
    InterviewSkipped,
    run_simple_interview,
    write_ground_truth_yaml,
)


def _make_scripted_input(responses: list[str]):
    """Retorna um `input_fn` que consome `responses` em ordem."""
    it = iter(responses)

    def input_fn(prompt: str) -> str:
        try:
            return next(it)
        except StopIteration:
            # Defensivo: se script terminar, trata como skip
            return ""

    return input_fn


def _collect_output():
    captured: list[str] = []
    return captured, captured.append


# ---------------------------------------------------------------------------
# run_simple_interview — happy path
# ---------------------------------------------------------------------------


def test_run_simple_interview_basic_flow(tmp_path):
    responses = [
        # obra_real
        "Reforma Teste",           # nome
        "TesteConstr Ltda",        # contratada
        "12345",                   # codesc
        "Santana",                 # municipio
        "MG",                      # uf
        "SEE-MG",                  # contratante_publico
        # canal
        "",                         # id (usa default obra)
        "",                         # tipo (default whatsapp)
        "Lucas F.",                # parte A nome
        "representante_empresa",   # parte A papel
        "",                         # parte A especialidade skip
        "Everaldo B.",             # parte B nome
        "prestador_servico",       # parte B papel
        "serralheiro",             # parte B especialidade
        # contratos (1)
        "1",                        # quantos
        "C1",                       # id
        "Estrutura bruta",         # escopo
        "7000.00",                 # valor_total
        "50/50",                   # forma
        "whatsapp_06_04",          # origem
        "2026-04-06",              # data_acordo
        "quitado",                 # status
        "",                         # observacao skip
        # pagamentos_confirmados (0)
        "0",                        # quantos pag conf
        # pagamentos_pendentes (0)
        "0",                        # quantos pag pend
        # problemas (0)
        "0",                        # quantos problemas
    ]
    captured, output_fn = _collect_output()
    inp = InterviewInput(
        obra="OBRA_T",
        output_path=tmp_path / "gt.yml",
        input_fn=_make_scripted_input(responses),
        output_fn=output_fn,
    )
    gt = run_simple_interview(inp)
    assert isinstance(gt, GroundTruth)
    assert gt.obra_real.nome == "Reforma Teste"
    assert gt.obra_real.codesc == 12345
    assert gt.canal.id == "OBRA_T"  # default usado
    assert gt.canal.tipo == "whatsapp"  # default usado
    assert gt.canal.parte_A.nome == "Lucas F."
    assert gt.canal.parte_B.especialidade == "serralheiro"
    assert len(gt.contratos) == 1
    assert gt.contratos[0].valor_total == 7000.00


def test_run_simple_interview_required_field_retries(tmp_path):
    """Campo obrigatorio skipado retomado no mesmo prompt."""
    responses = [
        "",               # nome (obrigatorio — skip, retomar)
        "Reforma",        # nome ok
        "TesteConstr",    # contratada
        "",               # codesc skip
        "",               # municipio skip
        "",               # uf skip
        "",               # contratante skip
        "",               # canal id (default)
        "",               # tipo (default)
        "A", "a",         # parte A nome + papel
        "",               # parte A especialidade
        "B", "b",         # parte B nome + papel
        "",               # parte B especialidade
        "0", "0", "0",   # zero contratos, zero pag conf, zero pag pend
        "0",              # zero problemas
    ]
    captured, output_fn = _collect_output()
    inp = InterviewInput(
        obra="X",
        output_path=tmp_path / "gt.yml",
        input_fn=_make_scripted_input(responses),
        output_fn=output_fn,
    )
    gt = run_simple_interview(inp)
    assert gt.obra_real.nome == "Reforma"
    # Mensagem de 'obrigatorio' apareceu em algum ponto
    assert any("obrigat" in m.lower() for m in captured)


def test_run_simple_interview_stop_early_raises():
    responses = ["stop"]
    captured, output_fn = _collect_output()
    inp = InterviewInput(
        obra="X",
        output_path=Path("/tmp/unused.yml"),
        input_fn=_make_scripted_input(responses),
        output_fn=output_fn,
    )
    with pytest.raises(InterviewSkipped):
        run_simple_interview(inp)


# ---------------------------------------------------------------------------
# write_ground_truth_yaml — serializa GroundTruth
# ---------------------------------------------------------------------------


def test_write_gt_yaml_roundtrip(tmp_path):
    """Escreve YAML e relê via loader — fidelidade."""
    from rdo_agent.ground_truth import (
        Canal, CanalParte, Contrato, GroundTruth, ObraReal,
    )
    gt = GroundTruth(
        obra_real=ObraReal(
            nome="Reforma X", contratada="Y Ltda",
            codesc=123, municipio="Santana", uf="MG",
        ),
        canal=Canal(
            id="TST", tipo="whatsapp",
            parte_A=CanalParte(nome="A", papel="a"),
            parte_B=CanalParte(
                nome="B", papel="b", especialidade="serralheiro"
            ),
        ),
        contratos=[
            Contrato(id="C1", escopo="tesouras", valor_total=7000.0,
                     data_acordo="2026-04-06", status="quitado"),
        ],
    )
    out_path = tmp_path / "gt.yml"
    write_ground_truth_yaml(gt, out_path)
    assert out_path.exists()

    reloaded = load_ground_truth(out_path)
    assert reloaded.obra_real.nome == "Reforma X"
    assert reloaded.obra_real.codesc == 123
    assert reloaded.canal.parte_B.especialidade == "serralheiro"
    assert len(reloaded.contratos) == 1
    assert reloaded.contratos[0].valor_total == 7000.0


def test_write_gt_yaml_prunes_none_fields(tmp_path):
    """Fields None/vazios nao aparecem no YAML (clean output)."""
    from rdo_agent.ground_truth import (
        Canal, CanalParte, GroundTruth, ObraReal,
    )
    gt = GroundTruth(
        obra_real=ObraReal(nome="X", contratada="Y"),  # sem codesc/municipio
        canal=Canal(
            id="C", tipo="whatsapp",
            parte_A=CanalParte(nome="A", papel="a"),  # sem especialidade
            parte_B=CanalParte(nome="B", papel="b"),
        ),
    )
    out_path = tmp_path / "gt.yml"
    write_ground_truth_yaml(gt, out_path)
    content = out_path.read_text(encoding="utf-8")
    assert "codesc" not in content
    assert "municipio" not in content
    assert "especialidade" not in content
    # Mas campos presentes SIM
    assert "obra_real:" in content
    assert "nome: X" in content


# ---------------------------------------------------------------------------
# run_adaptive_interview — Fase D2
# ---------------------------------------------------------------------------


class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [_FakeTextBlock(text)]


class _FakeAnthropicMessages:
    """Espelha .messages.create() do SDK anthropic."""

    def __init__(self, queue: list[str]):
        self._queue = list(queue)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self._queue.pop(0)
        return _FakeResponse(text)


def _scripted_turn(
    fragment: dict, question: str = "", is_complete: bool = False,
    notes: str | None = None,
) -> str:
    """Gera o text bruto de uma resposta do Claude (com bloco JSON)."""
    import json as _j
    payload = {
        "next_question": question,
        "accumulated_yaml_fragment": fragment,
        "is_complete": is_complete,
    }
    if notes:
        payload["notes_for_operator"] = notes
    return f"```json\n{_j.dumps(payload, ensure_ascii=False)}\n```"


def test_deep_merge_nested_dicts():
    from rdo_agent.gt_extractor.adaptive import _deep_merge
    a = {"obra_real": {"nome": "X"}}
    b = {"obra_real": {"contratada": "Y"}, "canal": {"id": "C"}}
    merged = _deep_merge(dict(a), b)
    assert merged["obra_real"] == {"nome": "X", "contratada": "Y"}
    assert merged["canal"] == {"id": "C"}


def test_deep_merge_lists_concat_with_id_dedup():
    from rdo_agent.gt_extractor.adaptive import _deep_merge
    a = {"contratos": [{"id": "C1", "valor_total": 7000}]}
    b = {"contratos": [
        {"id": "C1", "status": "quitado"},   # mesmo id => ignorado
        {"id": "C2", "valor_total": 11000},  # novo id => adicionado
    ]}
    merged = _deep_merge(dict(a), b)
    assert len(merged["contratos"]) == 2
    ids = [c["id"] for c in merged["contratos"]]
    assert ids == ["C1", "C2"]


def test_extract_json_block_fenced():
    from rdo_agent.gt_extractor.adaptive import _extract_json_block
    text = 'preambulo... ```json\n{"is_complete": true}\n``` fim'
    assert _extract_json_block(text) == {"is_complete": true_or_py()}


def true_or_py():
    return True  # helper pra evitar linter warnings sem afetar comportamento


def test_extract_json_block_no_fence():
    from rdo_agent.gt_extractor.adaptive import _extract_json_block
    text = 'lixo {"a": 1, "b": 2} mais lixo'
    assert _extract_json_block(text) == {"a": 1, "b": 2}


def test_extract_json_block_invalid_returns_none():
    from rdo_agent.gt_extractor.adaptive import _extract_json_block
    assert _extract_json_block("sem json aqui") is None


def test_run_adaptive_interview_minimal_flow(tmp_path):
    from rdo_agent.gt_extractor import run_adaptive_interview
    # Turno 1: pergunta obra_real.nome, operador responde "Reforma X"
    # Turno 2: pergunta contratada, operador responde "Y Ltda"
    # Turno 3: pergunta canal.id, operador responde "CANAL1"
    # Turno 4: pergunta parte_A, operador "Lucas,representante_empresa"
    # Turno 5: completa com todos os fragments
    fake = _FakeAnthropicMessages([
        _scripted_turn(
            fragment={"obra_real": {"nome": "Reforma X"}},
            question="Qual o nome da obra?",
        ),
        _scripted_turn(
            fragment={"obra_real": {"contratada": "Y Ltda"}},
            question="Qual empresa contratada?",
        ),
        _scripted_turn(
            fragment={"canal": {"id": "CANAL1", "tipo": "whatsapp"}},
            question="Qual id do canal?",
        ),
        _scripted_turn(
            fragment={
                "canal": {
                    "parte_A": {"nome": "Lucas", "papel": "representante_empresa"},
                    "parte_B": {"nome": "Everaldo", "papel": "prestador_servico"},
                },
            },
            question="Quem sao as partes do canal?",
        ),
        _scripted_turn(
            fragment={},
            question="",
            is_complete=True,
        ),
    ])

    responses = [
        "Reforma X",
        "Y Ltda",
        "CANAL1",
        "Lucas (A) e Everaldo (B)",
    ]
    captured, output_fn = _collect_output()
    inp = InterviewInput(
        obra="CANAL1",
        output_path=tmp_path / "gt.yml",
        input_fn=_make_scripted_input(responses),
        output_fn=output_fn,
    )
    gt = run_adaptive_interview(inp, client=fake)
    assert gt.obra_real.nome == "Reforma X"
    assert gt.obra_real.contratada == "Y Ltda"
    assert gt.canal.id == "CANAL1"
    assert gt.canal.parte_A.nome == "Lucas"
    # A chamada passou os 4 turnos antes do is_complete
    assert len(fake.calls) == 5


def test_run_adaptive_interview_invalid_json_raises(tmp_path):
    """Claude retorna texto sem JSON parseavel => AdaptiveInterviewError."""
    from rdo_agent.gt_extractor import (
        AdaptiveInterviewError, run_adaptive_interview,
    )
    fake = _FakeAnthropicMessages(["sem json aqui, so prosa"])
    captured, output_fn = _collect_output()
    inp = InterviewInput(
        obra="X",
        output_path=tmp_path / "gt.yml",
        input_fn=_make_scripted_input([]),
        output_fn=output_fn,
    )
    with pytest.raises(AdaptiveInterviewError):
        run_adaptive_interview(inp, client=fake, max_turns=1)


def test_write_gt_yaml_creates_parent_dirs(tmp_path):
    from rdo_agent.ground_truth import (
        Canal, CanalParte, GroundTruth, ObraReal,
    )
    gt = GroundTruth(
        obra_real=ObraReal(nome="X", contratada="Y"),
        canal=Canal(
            id="C", tipo="whatsapp",
            parte_A=CanalParte(nome="A", papel="a"),
            parte_B=CanalParte(nome="B", papel="b"),
        ),
    )
    nested = tmp_path / "deep" / "nested" / "gt.yml"
    write_ground_truth_yaml(gt, nested)
    assert nested.exists()
