"""Testes validator checklist F3 — Sprint 5 Fase A F4."""

from __future__ import annotations

import pytest

from rdo_agent.forensic_agent.validator import (
    CRITICAL_CHECKS,
    _format_brl_patterns,
    validate_narrative,
)


# ---------------------------------------------------------------------------
# _format_brl_patterns (puro)
# ---------------------------------------------------------------------------


def test_format_brl_patterns_standard():
    patterns = _format_brl_patterns(3500.00)
    assert "R$ 3.500,00" in patterns
    assert "R$3.500,00" in patterns


def test_format_brl_patterns_small_value():
    patterns = _format_brl_patterns(30.00)
    assert "R$ 30,00" in patterns


def test_format_brl_patterns_cents():
    patterns = _format_brl_patterns(0.99)
    assert "R$ 0,99" in patterns


# ---------------------------------------------------------------------------
# validate_narrative — critical checks
# ---------------------------------------------------------------------------


def _sample_dossier_with_pix() -> dict:
    return {
        "obra": "OBRA_T",
        "scope": "day",
        "scope_ref": "2026-04-06",
        "events_timeline": [
            {
                "id": "c_1", "timestamp": "2026-04-06T09:00:00Z",
                "hora_brasilia": "09:00", "source_type": "text_message",
                "primary_category": "cronograma", "secondary_categories": [],
                "content_preview": "Bom dia", "content_full": "Bom dia",
                "file_id": "m_abc123",
            },
            {
                "id": "c_2", "timestamp": "2026-04-06T11:13:00Z",
                "hora_brasilia": "11:13", "source_type": "visual_analysis",
                "primary_category": "pagamento", "secondary_categories": [],
                "content_preview": "pix", "content_full": "pix",
                "file_id": "f_img_pix",
            },
        ],
        "financial_records": [
            {
                "hora": "11:13", "valor_brl": 3500.00, "doc_type": "pix",
                "pagador": "CONSTRUTORA E IMOBILIARIA VALE NOBRE LTDA",
                "recebedor": "Everaldo Caitano Baia",
                "descricao": "50% sinal serralheria",
            },
        ],
        "context_hints": {"day_has_payment": True},
    }


def _good_narrative() -> str:
    return (
        "# Narrativa: OBRA_T — day 2026-04-06\n\n"
        "O dia de 2026-04-06 apresentou atividade desde 09:00. "
        "Lucas enviou mensagem às 09:00 (m_abc123). Às 11:13, transferência "
        "PIX de R$ 3.500,00 da CONSTRUTORA E IMOBILIARIA VALE NOBRE LTDA "
        "para Everaldo Caitano Baia (f_img_pix) referente a 50% sinal.\n\n"
        "## Destaques financeiros\n\n"
        "- 11:13 — R$ 3.500,00 PIX\n\n"
        "---\n"
    ) * 2  # duplica pra passar tamanho minimo


def _good_self_assessment() -> dict:
    return {
        "confidence": 0.85,
        "covered_all_events": True,
        "preserved_exact_values": True,
    }


def test_validate_passed_when_all_critical_ok():
    result = validate_narrative(
        _good_narrative(), _sample_dossier_with_pix(),
        _good_self_assessment(), _good_narrative(),
    )
    assert result["passed"] is True
    for check in CRITICAL_CHECKS:
        assert result["checks"][check] is True


def test_validate_fails_when_valor_ausente():
    """Narrativa sem o R$ 3.500,00 invalida valores_preservados (critical)."""
    narrative = (
        "# Narrativa: x\n\n" + "conteudo longo. " * 40 + "\n\n---"
    )
    result = validate_narrative(
        narrative, _sample_dossier_with_pix(),
        _good_self_assessment(), narrative,
    )
    assert result["checks"]["valores_preservados"] is False
    assert result["passed"] is False


def test_validate_fails_when_no_horario():
    """Sem HH:MM do timeline → critical fail."""
    narrative = (
        "# Narrativa: x\n\n" +
        ("texto sem horario. R$ 3.500,00 mencionado. " * 10) +
        "\n\n---"
    )
    result = validate_narrative(
        narrative, _sample_dossier_with_pix(),
        _good_self_assessment(), narrative,
    )
    assert result["checks"]["horarios_preservados"] is False
    assert result["passed"] is False


def test_validate_accepts_hhmm_with_h_separator():
    """Regex aceita estilo PT-BR '11h13' alem de '11:13'."""
    narrative = (
        "# Narrativa: x\n\n"
        "As 11h13 ocorreu o evento. " * 15
        + "R$ 3.500,00 pago. " * 10
        + "\n\n---"
    )
    result = validate_narrative(
        narrative, _sample_dossier_with_pix(),
        _good_self_assessment(), narrative,
    )
    assert result["checks"]["horarios_preservados"] is True


def test_validate_accepts_hhmm_with_seconds():
    """Regex aceita '11:13:00'."""
    narrative = (
        "# Narrativa: x\n\n"
        "No timestamp 11:13:00 registrou-se o PIX. " * 15
        + "R$ 3.500,00 pago. " * 10
        + "\n\n---"
    )
    result = validate_narrative(
        narrative, _sample_dossier_with_pix(),
        _good_self_assessment(), narrative,
    )
    assert result["checks"]["horarios_preservados"] is True


def test_validate_accepts_hhmmin_brazilian_style():
    """Regex aceita '11h13min' (estilo formal)."""
    narrative = (
        "# Narrativa: x\n\n"
        "As 11h13min aconteceu o evento. " * 15
        + "R$ 3.500,00 pago. " * 10
        + "\n\n---"
    )
    result = validate_narrative(
        narrative, _sample_dossier_with_pix(),
        _good_self_assessment(), narrative,
    )
    assert result["checks"]["horarios_preservados"] is True


def test_validate_fails_when_no_abertura():
    narrative = "Texto sem header.\n\n" + ("09:00 R$ 3.500,00 " * 30)
    result = validate_narrative(
        narrative, _sample_dossier_with_pix(),
        _good_self_assessment(), narrative,
    )
    assert result["checks"]["tem_abertura"] is False
    assert result["passed"] is False


def test_validate_fails_when_muito_curto():
    narrative = "# Narrativa: x\n\nCurto. 09:00 R$ 3.500,00"
    result = validate_narrative(
        narrative, _sample_dossier_with_pix(),
        _good_self_assessment(), narrative,
    )
    assert result["checks"]["tamanho_razoavel"] is False
    assert result["passed"] is False


# ---------------------------------------------------------------------------
# Soft checks — warnings nao invalidam passed
# ---------------------------------------------------------------------------


def test_validate_soft_file_ids_missing_does_not_fail(
):
    """Narrativa sem file_ids gera warning mas NAO invalida passed
    (soft check)."""
    narrative = (
        "# Narrativa: OBRA_T — day 2026-04-06\n\n"
        + ("Mensagem às 09:00 às 11:13 R$ 3.500,00 "
           "CONSTRUTORA E IMOBILIARIA VALE NOBRE LTDA "
           "Everaldo Caitano Baia. " * 10)
        + "\n\n---\n"
    )
    result = validate_narrative(
        narrative, _sample_dossier_with_pix(),
        _good_self_assessment(), narrative,
    )
    # Critical verdes
    for check in CRITICAL_CHECKS:
        assert result["checks"][check] is True
    assert result["passed"] is True
    # file_ids soft fail
    assert result["checks"]["file_ids_preservados"] is False
    assert any("file_id" in w for w in result["warnings"])


def test_validate_soft_nomes_ausentes_warning():
    narrative = (
        "# Narrativa: x\n\n"
        + ("09:00 R$ 3.500,00 transferencia. " * 30)
        + "\n\n---\n"
    )
    result = validate_narrative(
        narrative, _sample_dossier_with_pix(),
        _good_self_assessment(), narrative,
    )
    assert result["checks"]["nomes_preservados"] is False


def test_validate_marcadores_inferencia_skipped_if_few_events():
    """Se timeline tem <5 eventos, check passa vacuously."""
    d = _sample_dossier_with_pix()  # 2 eventos
    narrative = (
        "# Narrativa: x\n\n"
        + ("09:00 R$ 3.500,00. " * 30)
        + "\n\n---\n"
    )
    result = validate_narrative(
        narrative, d, _good_self_assessment(), narrative,
    )
    assert result["checks"]["marcadores_inferencia"] is True


def test_validate_marcadores_required_if_many_events():
    d = _sample_dossier_with_pix()
    # Adiciona 5 mais pra passar threshold
    for i in range(5):
        d["events_timeline"].append({
            "id": f"c_{10+i}", "hora_brasilia": f"1{i}:00",
            "file_id": f"f_{i}", "source_type": "text_message",
            "primary_category": "cronograma", "secondary_categories": [],
            "content_preview": "", "content_full": "",
        })
    narrative_sem_marcador = (
        "# Narrativa: x\n\n"
        + ("09:00 11:13 R$ 3.500,00 . " * 20)
        + "\n\n---\n"
    )
    result = validate_narrative(
        narrative_sem_marcador, d, _good_self_assessment(),
        narrative_sem_marcador,
    )
    assert result["checks"]["marcadores_inferencia"] is False


def test_validate_self_assessment_empty_soft_fail():
    narrative = (
        "# Narrativa: x\n\n" + ("09:00 R$ 3.500,00 CONSTRUTORA "
        "Everaldo Caitano. " * 30) + "\n\n---\n"
    )
    result = validate_narrative(
        narrative, _sample_dossier_with_pix(), {}, narrative,
    )
    assert result["checks"]["self_assessment_presente"] is False
    # Mas critical ainda verdes → passed=True
    assert result["passed"] is True


# ---------------------------------------------------------------------------
# Dossier sem financial_records — sem falsos-positivos
# ---------------------------------------------------------------------------


def test_validate_no_financial_records_valores_passes_vacuously():
    d = {
        "obra": "O", "scope": "day", "scope_ref": "2026-04-06",
        "events_timeline": [{
            "id": "c_1", "hora_brasilia": "09:00",
            "file_id": "m_abc", "source_type": "text_message",
            "primary_category": "cronograma", "secondary_categories": [],
            "content_preview": "x", "content_full": "x",
        }],
        "financial_records": [],
        "context_hints": {},
    }
    narrative = (
        "# Narrativa: x\n\n09:00 mensagem curta. " + ("x " * 150) + "\n\n---\n"
    )
    result = validate_narrative(
        narrative, d, {"confidence": 0.8}, narrative,
    )
    assert result["checks"]["valores_preservados"] is True
    assert result["checks"]["nomes_preservados"] is True
    assert result["passed"] is True


# ---------------------------------------------------------------------------
# file_ids threshold por modo (Sessão 4 · dívida #33)
# ---------------------------------------------------------------------------


def _dossier_5_file_ids() -> dict:
    """Dossier com 5 file_ids, sem PIX (sem critical sobre BRL/horario)."""
    return {
        "obra": "O", "scope": "day", "scope_ref": "2026-04-06",
        "events_timeline": [
            {
                "id": f"c_{i}", "hora_brasilia": "09:00",
                "file_id": f"m_evid_{i:02d}",
                "source_type": "text_message",
                "primary_category": "cronograma",
                "secondary_categories": [],
                "content_preview": "x", "content_full": "x",
            }
            for i in range(1, 6)
        ],
        "financial_records": [],
        "context_hints": {},
    }


def test_validator_file_ids_threshold_normal_mode_50pct():
    """
    Modo padrão (sem prompt_version): threshold 50%. Narrativa que cita
    apenas 40% (2 de 5) deve falhar o soft check.
    """
    narrative = (
        "# Narrativa: x\n\n"
        "Às 09:00 evidência m_evid_01 registrada e m_evid_02 também. "
        + ("contexto adicional. " * 30)
        + "\n\n---\n"
    )
    result = validate_narrative(
        narrative, _dossier_5_file_ids(), {"confidence": 0.8}, narrative,
    )
    assert result["checks"]["file_ids_preservados"] is False
    assert any("40%" in w or "esperado >=50%" in w for w in result["warnings"])


def test_validator_file_ids_threshold_adversarial_mode_30pct():
    """
    Modo adversarial: threshold 30%. Narrativa com mesmos 40% que falhava
    no padrão agora passa, porque contestações citam evidência limitada
    por construção (#33).
    """
    narrative = (
        "# Narrativa: x\n\n"
        "Às 09:00 evidência m_evid_01 registrada e m_evid_02 também. "
        + ("contexto adicional. " * 30)
        + "\n\n---\n"
    )
    result = validate_narrative(
        narrative, _dossier_5_file_ids(), {"confidence": 0.8}, narrative,
        prompt_version="narrator_v4_adversarial",
    )
    assert result["checks"]["file_ids_preservados"] is True


def test_validator_file_ids_threshold_adversarial_still_fails_below_30pct():
    """
    Threshold relaxado em adversarial não é "qualquer coisa passa": 0%
    de cobertura ainda falha (warning é informação útil).
    """
    narrative = (
        "# Narrativa: x\n\n"
        "Texto sem citar nenhum file_id. "
        + ("contexto adicional. " * 30)
        + "\n\n---\n"
    )
    result = validate_narrative(
        narrative, _dossier_5_file_ids(), {"confidence": 0.8}, narrative,
        prompt_version="narrator_v4_adversarial",
    )
    assert result["checks"]["file_ids_preservados"] is False
    assert any("0/5" in w or "0%" in w for w in result["warnings"])
