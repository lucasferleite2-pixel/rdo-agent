"""Testes CLI rdo-agent export-laudo (Sessao 3 Fase 3.3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from rdo_agent.cli import main
from rdo_agent.laudo.vestigio_laudo import LaudoData


def _stub_data() -> LaudoData:
    return LaudoData(
        caso_id="VST-2026-TEST",
        titulo="Análise Forense · TEST",
        periodo_inicio="01/04/2026",
        periodo_fim="15/04/2026",
        operador="Lucas Fernandes Leite",
        corpus_hash="deadbeef1234",
        total_mensagens=42,
        total_documentos=3,
        total_audios=10,
        total_correlacoes=5,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_cli_export_laudo_basic(tmp_path, monkeypatch):
    """Corpus valido: adapter + generator chamados; exit 0; PDF mock escrito."""
    out = tmp_path / "laudo.pdf"

    def fake_generate(self, data, output):
        Path(output).write_bytes(b"%PDF-fake\n")
        return Path(output)

    with patch(
        "rdo_agent.laudo.rdo_to_vestigio_data",
        return_value=_stub_data(),
    ) as mock_adapter, patch(
        "rdo_agent.laudo.LaudoGenerator.generate",
        autospec=True, side_effect=fake_generate,
    ) as mock_gen:
        runner = CliRunner()
        r = runner.invoke(main, [
            "export-laudo",
            "--corpus", "EVERALDO_SANTAQUITERIA",
            "--output", str(out),
        ])
    assert r.exit_code == 0, r.output
    assert out.exists()
    assert "VST-2026-TEST" in r.output
    assert mock_adapter.called
    args, kwargs = mock_adapter.call_args
    # adversarial default False
    assert kwargs.get("adversarial") is False
    assert kwargs.get("include_ground_truth") is False


def test_cli_export_laudo_certified_flag_propagates(tmp_path):
    """--certified deve ativar incluir_marca_dagua_certificacao na LaudoData."""
    out = tmp_path / "laudo.pdf"
    captured: dict = {}

    def fake_generate(self, data, output):
        captured["data"] = data
        Path(output).write_bytes(b"%PDF-fake\n")
        return Path(output)

    with patch(
        "rdo_agent.laudo.rdo_to_vestigio_data",
        return_value=_stub_data(),
    ), patch(
        "rdo_agent.laudo.LaudoGenerator.generate",
        autospec=True, side_effect=fake_generate,
    ):
        runner = CliRunner()
        r = runner.invoke(main, [
            "export-laudo",
            "--corpus", "EVERALDO_SANTAQUITERIA",
            "--output", str(out),
            "--certified",
        ])
    assert r.exit_code == 0
    assert captured["data"].incluir_marca_dagua_certificacao is True


def test_cli_export_laudo_adversarial_flag_propagates(tmp_path):
    """--adversarial deve chamar rdo_to_vestigio_data com adversarial=True."""
    out = tmp_path / "laudo.pdf"

    def fake_generate(self, data, output):
        Path(output).write_bytes(b"%PDF-fake\n")
        return Path(output)

    with patch(
        "rdo_agent.laudo.rdo_to_vestigio_data",
        return_value=_stub_data(),
    ) as mock_adapter, patch(
        "rdo_agent.laudo.LaudoGenerator.generate",
        autospec=True, side_effect=fake_generate,
    ):
        runner = CliRunner()
        r = runner.invoke(main, [
            "export-laudo",
            "--corpus", "EVERALDO_SANTAQUITERIA",
            "--output", str(out),
            "--adversarial",
        ])
    assert r.exit_code == 0
    _, kwargs = mock_adapter.call_args
    assert kwargs["adversarial"] is True


# ---------------------------------------------------------------------------
# Erros
# ---------------------------------------------------------------------------


def test_cli_export_laudo_missing_corpus(tmp_path):
    """Corpus nao encontrado => exit 2 + mensagem clara."""
    from rdo_agent.laudo import CorpusNotFoundError

    out = tmp_path / "laudo.pdf"
    with patch(
        "rdo_agent.laudo.rdo_to_vestigio_data",
        side_effect=CorpusNotFoundError("Corpus 'NAO_EXISTE' nao tem msgs"),
    ):
        runner = CliRunner()
        r = runner.invoke(main, [
            "export-laudo",
            "--corpus", "NAO_EXISTE",
            "--output", str(out),
        ])
    assert r.exit_code == 2
    assert "NAO_EXISTE" in r.output


def test_cli_export_laudo_required_flags_errors():
    """Faltando --corpus ou --output => Click erro."""
    runner = CliRunner()
    r = runner.invoke(main, ["export-laudo"])
    assert r.exit_code != 0
    assert "Missing option" in r.output or "required" in r.output.lower()


# ---------------------------------------------------------------------------
# --context e --config (opcionais)
# ---------------------------------------------------------------------------


def test_cli_export_laudo_with_context_marks_gt(tmp_path):
    """--context marca include_ground_truth=True no adapter call."""
    out = tmp_path / "laudo.pdf"
    gt = tmp_path / "gt.yml"
    gt.write_text("obra_real: {}\ncanal: {}\n", encoding="utf-8")

    def fake_generate(self, data, output):
        Path(output).write_bytes(b"%PDF-fake\n")
        return Path(output)

    with patch(
        "rdo_agent.laudo.rdo_to_vestigio_data",
        return_value=_stub_data(),
    ) as mock_adapter, patch(
        "rdo_agent.laudo.LaudoGenerator.generate",
        autospec=True, side_effect=fake_generate,
    ):
        runner = CliRunner()
        r = runner.invoke(main, [
            "export-laudo",
            "--corpus", "EVERALDO_SANTAQUITERIA",
            "--output", str(out),
            "--context", str(gt),
        ])
    assert r.exit_code == 0, r.output
    _, kwargs = mock_adapter.call_args
    assert kwargs["include_ground_truth"] is True


def test_cli_export_laudo_with_config_overrides(tmp_path):
    """--config YAML deve enviar overrides ao adapter."""
    out = tmp_path / "laudo.pdf"
    cfg = tmp_path / "cfg.yml"
    cfg.write_text(
        "cliente: Dr. Fulano OAB-MG\n"
        "processo: 12345-67.2026\n"
        "objeto: Teste\n",
        encoding="utf-8",
    )

    def fake_generate(self, data, output):
        Path(output).write_bytes(b"%PDF-fake\n")
        return Path(output)

    with patch(
        "rdo_agent.laudo.rdo_to_vestigio_data",
        return_value=_stub_data(),
    ) as mock_adapter, patch(
        "rdo_agent.laudo.LaudoGenerator.generate",
        autospec=True, side_effect=fake_generate,
    ):
        runner = CliRunner()
        r = runner.invoke(main, [
            "export-laudo",
            "--corpus", "EVERALDO_SANTAQUITERIA",
            "--output", str(out),
            "--config", str(cfg),
        ])
    assert r.exit_code == 0, r.output
    _, kwargs = mock_adapter.call_args
    overrides = kwargs["config_overrides"]
    assert overrides["cliente"] == "Dr. Fulano OAB-MG"
    assert overrides["processo"] == "12345-67.2026"
    assert overrides["objeto"] == "Teste"
