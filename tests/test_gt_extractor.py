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
