"""
Validator Checklist F3 — Sprint 5 Fase A.

Checklist automatico sobre narrativa gerada + dossier original.
Retorna {passed, checks, warnings}.

Critical checks (invalidam passed):
  - valores_preservados: todos valor_brl de financial_records aparecem em R$ formato
  - horarios_preservados: pelo menos 1 HH:MM do timeline esta presente (se timeline > 0)
  - tem_abertura: comeca com "# Narrativa:"
  - tamanho_razoavel: 300 <= len(body) <= 20000

Soft checks (warnings mas nao invalidam):
  - file_ids_preservados: ao menos 50% dos file_ids aparecem
  - nomes_preservados: pagador/recebedor literais presentes se houver
    financial_records
  - marcadores_inferencia: pelo menos 1 marcador se >=5 eventos
  - tem_fechamento: contem '---' antes do self_assessment
  - self_assessment_presente: dict nao-vazio
"""

from __future__ import annotations

import re
from typing import Any

# Limites para tamanho
MIN_BODY_CHARS = 300
MAX_BODY_CHARS = 20000

# Marcadores de inferencia aceitos (qualquer um conta)
INFERENCE_MARKERS = (
    "sugere que", "sugere ", "possivelmente", "pode indicar",
    "aparenta", "ao que parece", "pode ter", "talvez",
)

# Critical checks — sua falha invalida passed
CRITICAL_CHECKS = {
    "valores_preservados",
    "horarios_preservados",
    "tem_abertura",
    "tamanho_razoavel",
}


def _format_brl_patterns(valor_brl: float) -> list[str]:
    """
    Gera variantes de formatacao BRL aceitas pra uma valor.
    Ex: 3500.00 -> ['R$ 3.500,00', 'R$3.500,00', 'R\\$ 3.500,00']
    """
    reais = int(valor_brl)
    centavos = round((valor_brl - reais) * 100)
    # Com separador de milhar
    with_sep = f"{reais:,}".replace(",", ".")
    formatted = f"{with_sep},{centavos:02d}"
    # Sem separador
    plain = f"{reais},{centavos:02d}"
    return [
        f"R$ {formatted}",
        f"R${formatted}",
        f"R$ {plain}",
        f"R${plain}",
    ]


def _check_valores_preservados(
    narrative: str, dossier: dict,
) -> tuple[bool, list[str]]:
    """True se TODOS financial_records.valor_brl aparecem em formato BRL."""
    financial = dossier.get("financial_records") or []
    warnings = []
    if not financial:
        return True, []
    for rec in financial:
        valor = rec.get("valor_brl")
        if valor is None:
            continue
        patterns = _format_brl_patterns(valor)
        if not any(p in narrative for p in patterns):
            warnings.append(f"valor R$ {valor:.2f} nao encontrado em narrativa")
    return (len(warnings) == 0, warnings)


def _horario_pattern(hhmm: str) -> str:
    """
    Pattern regex que match um HH:MM do dossier em qualquer estilo comum
    de narrativa PT-BR:
      - "11:13" (colon, estilo canonico)
      - "11h13" (h estilo brasileiro usual)
      - "11:13:00" (com segundos)
      - "11h13min" (estilo formal) — opcional suffix "min"
    """
    hh, mm = hhmm.split(":")
    # \b{HH}[:h]{MM}(?::\d{2}|min)?\b
    return rf"\b{re.escape(hh)}[:h]{re.escape(mm)}(?::\d{{2}}|min)?\b"


def _check_horarios_preservados(
    narrative: str, dossier: dict,
) -> tuple[bool, list[str]]:
    """True se ao menos 1 HH:MM do timeline aparece na narrativa."""
    timeline = dossier.get("events_timeline") or []
    if not timeline:
        return True, []  # vacuously true
    horarios = {e.get("hora_brasilia") for e in timeline if e.get("hora_brasilia")}
    horarios.discard("--:--")
    if not horarios:
        return True, []
    found = any(
        re.search(_horario_pattern(h), narrative)
        for h in horarios
        if h and ":" in h
    )
    if not found:
        return False, [
            f"nenhum horario do timeline ({len(horarios)} disponiveis) "
            f"presente em narrativa"
        ]
    return True, []


def _check_nomes_preservados(
    narrative: str, dossier: dict,
) -> tuple[bool, list[str]]:
    """Soft: nomes proprios (pagador/recebedor) devem aparecer literais."""
    financial = dossier.get("financial_records") or []
    warnings = []
    for rec in financial:
        for field in ("pagador", "recebedor"):
            name = rec.get(field)
            if name and len(name) > 4 and name not in narrative:
                # Tenta primeira palavra (ex: 'Everaldo' basta)
                first_word = name.split()[0] if name.split() else ""
                if first_word and first_word not in narrative:
                    warnings.append(
                        f"{field} '{name}' nao literal em narrativa"
                    )
    return (len(warnings) == 0, warnings)


def _check_file_ids_preservados(
    narrative: str, dossier: dict,
) -> tuple[bool, list[str]]:
    """Soft: ao menos 50% dos file_ids devem aparecer."""
    timeline = dossier.get("events_timeline") or []
    fids = [e.get("file_id") for e in timeline if e.get("file_id")]
    if not fids:
        return True, []
    found = sum(1 for f in fids if f in narrative)
    ratio = found / len(fids)
    if ratio < 0.5:
        return False, [
            f"apenas {found}/{len(fids)} file_ids ({ratio*100:.0f}%) "
            "aparecem em narrativa"
        ]
    return True, []


def _check_marcadores_inferencia(
    narrative: str, dossier: dict,
) -> tuple[bool, list[str]]:
    """Soft: pelo menos 1 marcador se timeline tem >=5 eventos."""
    timeline = dossier.get("events_timeline") or []
    if len(timeline) < 5:
        return True, []
    narrative_lower = narrative.lower()
    found = any(m in narrative_lower for m in INFERENCE_MARKERS)
    if not found:
        return False, [
            "nenhum marcador de inferencia (sugere/possivelmente/etc) "
            "em narrativa com >=5 eventos"
        ]
    return True, []


def _check_tamanho_razoavel(body: str) -> tuple[bool, list[str]]:
    n = len(body)
    if n < MIN_BODY_CHARS:
        return False, [f"narrativa muito curta: {n} < {MIN_BODY_CHARS} chars"]
    if n > MAX_BODY_CHARS:
        return False, [f"narrativa muito longa: {n} > {MAX_BODY_CHARS} chars"]
    return True, []


def _check_tem_abertura(narrative: str) -> tuple[bool, list[str]]:
    if not narrative.strip().startswith("# Narrativa:"):
        return False, ["narrativa nao comeca com '# Narrativa:'"]
    return True, []


def _check_tem_fechamento(narrative: str) -> tuple[bool, list[str]]:
    """Soft: contem '---' em algum ponto (separador antes do JSON)."""
    if "---" not in narrative:
        return False, ["narrativa nao contem separador '---'"]
    return True, []


def _check_self_assessment_presente(self_assessment: dict) -> tuple[bool, list[str]]:
    """Soft: self_assessment dict tem ao menos 'confidence'."""
    if not isinstance(self_assessment, dict) or not self_assessment:
        return False, ["self_assessment ausente ou vazio"]
    if "confidence" not in self_assessment:
        return False, ["self_assessment sem campo 'confidence'"]
    return True, []


def validate_narrative(
    narrative_body: str,
    dossier: dict,
    self_assessment: dict | None = None,
    full_narrative: str | None = None,
) -> dict[str, Any]:
    """
    Valida narrativa vs dossier. Retorna dict com:
      - passed: bool (todos critical checks ok)
      - checks: {check_name: bool}
      - warnings: list[str]

    Args:
        narrative_body: markdown sem bloco self_assessment
        dossier: dict original do dossier_builder
        self_assessment: dict parseado do bloco JSON
        full_narrative: markdown completo (body + bloco) — se None,
            usa narrative_body para checks de abertura/fechamento
    """
    full = full_narrative if full_narrative is not None else narrative_body
    self_assessment = self_assessment or {}

    checks: dict[str, bool] = {}
    warnings: list[str] = []

    # Critical
    ok, w = _check_valores_preservados(full, dossier)
    checks["valores_preservados"] = ok
    warnings.extend(w)

    ok, w = _check_horarios_preservados(full, dossier)
    checks["horarios_preservados"] = ok
    warnings.extend(w)

    ok, w = _check_tem_abertura(full)
    checks["tem_abertura"] = ok
    warnings.extend(w)

    ok, w = _check_tamanho_razoavel(narrative_body)
    checks["tamanho_razoavel"] = ok
    warnings.extend(w)

    # Soft
    ok, w = _check_file_ids_preservados(full, dossier)
    checks["file_ids_preservados"] = ok
    warnings.extend(w)

    ok, w = _check_nomes_preservados(full, dossier)
    checks["nomes_preservados"] = ok
    warnings.extend(w)

    ok, w = _check_marcadores_inferencia(full, dossier)
    checks["marcadores_inferencia"] = ok
    warnings.extend(w)

    ok, w = _check_tem_fechamento(full)
    checks["tem_fechamento"] = ok
    warnings.extend(w)

    ok, w = _check_self_assessment_presente(self_assessment)
    checks["self_assessment_presente"] = ok
    warnings.extend(w)

    # passed = todos os critical checks True
    passed = all(checks[c] for c in CRITICAL_CHECKS)

    return {
        "passed": passed,
        "checks": checks,
        "warnings": warnings,
    }


__all__ = [
    "CRITICAL_CHECKS",
    "INFERENCE_MARKERS",
    "MAX_BODY_CHARS",
    "MIN_BODY_CHARS",
    "validate_narrative",
]
