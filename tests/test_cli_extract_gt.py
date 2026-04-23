"""Testes CLI rdo-agent extract-gt — Fase D3."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from rdo_agent.cli import main
from rdo_agent.ground_truth.schema import (
    Canal,
    CanalParte,
    GroundTruth,
    ObraReal,
)
from rdo_agent.utils import config


def _gt_minimal() -> GroundTruth:
    return GroundTruth(
        obra_real=ObraReal(nome="Teste", contratada="Y"),
        canal=Canal(
            id="TST", tipo="whatsapp",
            parte_A=CanalParte(nome="A", papel="a"),
            parte_B=CanalParte(nome="B", papel="b"),
        ),
    )


def test_extract_gt_simple_mode_writes_yaml(tmp_path, monkeypatch):
    """Modo simple nao precisa de API key."""
    # Sem API key explicitamente
    settings = config.Settings(
        openai_api_key="", anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=tmp_path, log_level="WARNING", dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)

    out = tmp_path / "gt.yml"
    runner = CliRunner()

    # Patch a funcao de entrevista pra retornar um GT fixo sem
    # exigir stdin interativo
    with patch(
        "rdo_agent.gt_extractor.run_simple_interview",
        return_value=_gt_minimal(),
    ):
        r = runner.invoke(main, [
            "extract-gt", "--obra", "TST",
            "--mode", "simple",
            "--output", str(out), "--force",
        ])

    assert r.exit_code == 0, r.output
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "obra_real" in content
    assert "Teste" in content


def test_extract_gt_adaptive_requires_api_key(tmp_path, monkeypatch):
    """Modo adaptive sem ANTHROPIC_API_KEY => exit 3."""
    settings = config.Settings(
        openai_api_key="", anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=tmp_path, log_level="WARNING", dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)

    out = tmp_path / "gt.yml"
    runner = CliRunner()
    r = runner.invoke(main, [
        "extract-gt", "--obra", "TST",
        "--mode", "adaptive",
        "--output", str(out), "--force",
    ])
    assert r.exit_code == 3
    assert "ANTHROPIC_API_KEY ausente" in r.output


def test_extract_gt_default_mode_picks_simple_without_key(
    tmp_path, monkeypatch,
):
    """Sem API key, default = simple (fallback graceful)."""
    settings = config.Settings(
        openai_api_key="", anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=tmp_path, log_level="WARNING", dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)

    out = tmp_path / "gt.yml"
    runner = CliRunner()
    with patch(
        "rdo_agent.gt_extractor.run_simple_interview",
        return_value=_gt_minimal(),
    ) as mock_simple, patch(
        "rdo_agent.gt_extractor.run_adaptive_interview",
    ) as mock_adaptive:
        r = runner.invoke(main, [
            "extract-gt", "--obra", "TST",
            "--output", str(out), "--force",
        ])
    assert r.exit_code == 0, r.output
    assert mock_simple.called
    assert not mock_adaptive.called


def test_extract_gt_existing_file_without_force_aborts(
    tmp_path, monkeypatch,
):
    """Arquivo existe + sem --force + resposta 'n' aborta."""
    settings = config.Settings(
        openai_api_key="", anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=tmp_path, log_level="WARNING", dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)

    out = tmp_path / "gt.yml"
    out.write_text("pre-existente", encoding="utf-8")
    runner = CliRunner()
    r = runner.invoke(main, [
        "extract-gt", "--obra", "TST",
        "--mode", "simple",
        "--output", str(out),
    ], input="n\n")
    assert r.exit_code == 0
    assert "Abortado" in r.output
    # Arquivo preservado
    assert out.read_text(encoding="utf-8") == "pre-existente"


def test_extract_gt_default_output_path(tmp_path, monkeypatch):
    """Sem --output, default = docs/ground_truth/<obra>.yml (relativo)."""
    settings = config.Settings(
        openai_api_key="", anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=tmp_path, log_level="WARNING", dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    with patch(
        "rdo_agent.gt_extractor.run_simple_interview",
        return_value=_gt_minimal(),
    ):
        r = runner.invoke(main, [
            "extract-gt", "--obra", "NOVO_CANAL",
            "--mode", "simple", "--force",
        ])
    assert r.exit_code == 0, r.output
    expected = tmp_path / "docs" / "ground_truth" / "NOVO_CANAL.yml"
    assert expected.exists()


def test_extract_gt_failure_propagates_exit_2(tmp_path, monkeypatch):
    """Exception na entrevista => exit 2."""
    settings = config.Settings(
        openai_api_key="", anthropic_api_key="",
        claude_model="claude-sonnet-4-6",
        vaults_root=tmp_path, log_level="WARNING", dry_run=False,
    )
    monkeypatch.setattr(config, "_settings", settings)

    out = tmp_path / "gt.yml"
    runner = CliRunner()
    with patch(
        "rdo_agent.gt_extractor.run_simple_interview",
        side_effect=ValueError("schema missing required"),
    ):
        r = runner.invoke(main, [
            "extract-gt", "--obra", "TST",
            "--mode", "simple",
            "--output", str(out), "--force",
        ])
    assert r.exit_code == 2
    assert "ValueError" in r.output or "schema missing" in r.output
