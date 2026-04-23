"""Testes loader Ground Truth — Sprint 5 Fase C."""

from __future__ import annotations

from pathlib import Path

import pytest

from rdo_agent.ground_truth import (
    GroundTruth,
    GroundTruthValidationError,
    load_ground_truth,
)


# Fixture YAML completo (usa textwrap.dedent pra limpeza)
_FULL_YAML = """
obra_real:
  nome: Reforma Teste
  codesc: 12345
  municipio: Santana
  uf: MG
  contratante_publico: SEE-MG
  contratada: Empresa Teste Ltda

canal:
  id: TESTE_OBRA
  tipo: whatsapp
  parte_A:
    nome: Lucas Fernandes
    papel: representante_empresa
  parte_B:
    nome: Everaldo Baia
    papel: prestador_servico
    especialidade: serralheiro

contratos:
  - id: C1
    escopo: Estrutura bruta
    valor_total: 7000.00
    forma_pagamento: 50/50
    origem: whatsapp_06_04
    data_acordo: "2026-04-06"
    status: quitado
  - id: C2
    escopo: Acabamento completo
    valor_total: 11000.00
    status: em_execucao_50pct_pago

pagamentos_confirmados:
  - valor: 3500.00
    data: "2026-04-06"
    hora: "11:13"
    contrato_ref: C1
    parcela: sinal_50pct
    descricao_pix: "sinal telhado"
  - valor: 30.00
    data: "2026-04-14"
    hora: "13:43"
    tipo: reembolso_operacional
    descricao_pix: Gasolina tinta

pagamentos_pendentes:
  - valor: 5500.00
    contrato_ref: C2
    parcela: saldo_50pct
    gatilho_pagamento: conclusao_do_servico

totais:
  valor_negociado_total: 18000.00
  valor_pago_total: 12530.00
  valor_pendente: 5500.00
  pct_concluido_financeiramente: 69.4

estado_atual:
  data_snapshot: "2026-04-23"
  obra_em_execucao: true
  c1_status: quitado
  c2_status: em_execucao_50pct_pago
  problemas_conhecidos:
    - descricao: Medidas erradas no alambrado
      detectado_em: "2026-04-15"
      responsabilidade: terceiros_anteriores

aspectos_nao_registrados_em_evidencia:
  - Negociacoes fora do WhatsApp
  - Sem contrato escrito formal
"""


@pytest.fixture
def full_yaml(tmp_path) -> Path:
    p = tmp_path / "gt.yml"
    p.write_text(_FULL_YAML, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_load_full_yaml_returns_ground_truth(full_yaml):
    gt = load_ground_truth(full_yaml)
    assert isinstance(gt, GroundTruth)
    assert gt.obra_real.nome == "Reforma Teste"
    assert gt.obra_real.codesc == 12345
    assert gt.canal.id == "TESTE_OBRA"
    assert gt.canal.parte_A.nome == "Lucas Fernandes"
    assert gt.canal.parte_B.especialidade == "serralheiro"


def test_load_preserves_contratos(full_yaml):
    gt = load_ground_truth(full_yaml)
    assert len(gt.contratos) == 2
    assert gt.contratos[0].id == "C1"
    assert gt.contratos[0].valor_total == 7000.00
    assert gt.contratos[1].id == "C2"
    assert gt.contratos[1].status == "em_execucao_50pct_pago"


def test_load_preserves_pagamentos_confirmados(full_yaml):
    gt = load_ground_truth(full_yaml)
    assert len(gt.pagamentos_confirmados) == 2
    p1 = gt.pagamentos_confirmados[0]
    assert p1.valor == 3500.00
    assert p1.data == "2026-04-06"
    assert p1.hora == "11:13"
    assert p1.contrato_ref == "C1"
    # segundo pagamento (reembolso) nao tem contrato_ref
    p2 = gt.pagamentos_confirmados[1]
    assert p2.tipo == "reembolso_operacional"
    assert p2.contrato_ref is None


def test_load_preserves_pagamentos_pendentes(full_yaml):
    gt = load_ground_truth(full_yaml)
    assert len(gt.pagamentos_pendentes) == 1
    pp = gt.pagamentos_pendentes[0]
    assert pp.valor == 5500.00
    assert pp.contrato_ref == "C2"


def test_load_preserves_totais(full_yaml):
    gt = load_ground_truth(full_yaml)
    assert gt.totais is not None
    assert gt.totais.valor_negociado_total == 18000.00


def test_load_preserves_estado_e_problemas(full_yaml):
    gt = load_ground_truth(full_yaml)
    assert gt.estado_atual is not None
    assert gt.estado_atual.obra_em_execucao is True
    assert len(gt.estado_atual.problemas_conhecidos) == 1
    assert "alambrado" in gt.estado_atual.problemas_conhecidos[0].descricao.lower()


def test_load_preserves_raw_yaml(full_yaml):
    gt = load_ground_truth(full_yaml)
    # raw preserva forward-compat; deve conter as chaves de topo
    assert "obra_real" in gt.raw
    assert "contratos" in gt.raw


def test_load_aspectos_list(full_yaml):
    gt = load_ground_truth(full_yaml)
    assert len(gt.aspectos_nao_registrados_em_evidencia) == 2
    assert "WhatsApp" in gt.aspectos_nao_registrados_em_evidencia[0]


# ---------------------------------------------------------------------------
# Validacao — erros
# ---------------------------------------------------------------------------


def test_load_missing_obra_real_raises(tmp_path):
    p = tmp_path / "bad.yml"
    p.write_text("canal:\n  id: X\n  tipo: whatsapp\n  "
                 "parte_A: {nome: A, papel: a}\n  "
                 "parte_B: {nome: B, papel: b}\n", encoding="utf-8")
    with pytest.raises(GroundTruthValidationError, match="obra_real"):
        load_ground_truth(p)


def test_load_missing_obra_nome_raises(tmp_path):
    p = tmp_path / "bad.yml"
    p.write_text(
        "obra_real:\n  contratada: X\ncanal:\n  id: C\n  tipo: whatsapp\n"
        "  parte_A: {nome: A, papel: a}\n  parte_B: {nome: B, papel: b}\n",
        encoding="utf-8",
    )
    with pytest.raises(GroundTruthValidationError, match="nome"):
        load_ground_truth(p)


def test_load_missing_contrato_valor_raises(tmp_path):
    p = tmp_path / "bad.yml"
    p.write_text(
        _FULL_YAML.replace("valor_total: 7000.00", "escopo_dup: x"),
        encoding="utf-8",
    )
    with pytest.raises(GroundTruthValidationError, match="valor_total"):
        load_ground_truth(p)


def test_load_nonexistent_file_raises():
    with pytest.raises(FileNotFoundError):
        load_ground_truth("/nao/existe/arquivo.yml")


def test_load_malformed_yaml_raises(tmp_path):
    p = tmp_path / "bad.yml"
    # YAML invalido: indentacao inconsistente + tab
    p.write_text("obra_real:\n\tnome: X\n  contratada: Y\n", encoding="utf-8")
    with pytest.raises(GroundTruthValidationError):
        load_ground_truth(p)


def test_load_root_not_mapping_raises(tmp_path):
    p = tmp_path / "bad.yml"
    p.write_text("- isso eh uma lista\n- nao mapping\n", encoding="utf-8")
    with pytest.raises(GroundTruthValidationError, match="dict"):
        load_ground_truth(p)


# ---------------------------------------------------------------------------
# YAML real do piloto (sanity — arquivo existe no repo)
# ---------------------------------------------------------------------------


def test_load_real_everaldo_piloto_yaml():
    real_path = Path(
        "docs/ground_truth/EVERALDO_SANTAQUITERIA.yml"
    )
    if not real_path.exists():
        pytest.skip("YAML do piloto nao disponivel neste checkout")
    gt = load_ground_truth(real_path)
    assert gt.canal.id == "EVERALDO_SANTAQUITERIA"
    assert len(gt.contratos) == 2
    # c1 + c2 = 18k negociado
    assert gt.totais.valor_negociado_total == 18000.00
