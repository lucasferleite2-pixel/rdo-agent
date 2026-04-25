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
from enum import Enum
from typing import Any

# Limites para tamanho
MIN_BODY_CHARS = 300
# Sessao 2: overview com GT + adversarial + correlations pode atingir
# 28-32k chars legitimamente. Bumpado de 20000 -> 40000. Acima disso
# o narrador esta perdendo foco ou a obra eh grande demais (sinal pra
# segmentar em canais).
MAX_BODY_CHARS = 40000

# Marcadores de inferencia aceitos (qualquer um conta)
INFERENCE_MARKERS = (
    "sugere que", "sugere ", "possivelmente", "pode indicar",
    "aparenta", "ao que parece", "pode ter", "talvez",
)


class ValidationSeverity(str, Enum):
    """
    Tier de severidade de cada check do validator F3 (Sessao 5, #31).

    - CRITICAL: falha invalida ``passed``. Pipeline em qualquer modo
      bloqueia.
    - WARNING: falha gera warning. ``passed`` pode continuar True.
      No modo ``strict=True`` tambem bloqueia.
    - INFO: observacao nao-bloqueante. Nunca bloqueia. So aparece em
      ``warnings`` se for util pra debug.
    """

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


# Tier por check name. CRITICAL_CHECKS (legacy) preservado para
# compat — espelha o set abaixo.
CHECK_SEVERITY: dict[str, ValidationSeverity] = {
    # Critical: cobertura semantica e estrutura minima
    "valores_preservados":      ValidationSeverity.CRITICAL,
    "horarios_preservados":     ValidationSeverity.CRITICAL,
    "tem_abertura":             ValidationSeverity.CRITICAL,
    "tamanho_razoavel":         ValidationSeverity.CRITICAL,
    # Warning: cobertura desejada mas nao bloqueante
    "file_ids_preservados":     ValidationSeverity.WARNING,
    "nomes_preservados":        ValidationSeverity.WARNING,
    "marcadores_inferencia":    ValidationSeverity.WARNING,
    "self_assessment_presente": ValidationSeverity.WARNING,
    # Info: cosmetico (separador antes do bloco JSON)
    "tem_fechamento":           ValidationSeverity.INFO,
}

# Critical checks — sua falha invalida passed (mantido para compat
# externa; derivado de CHECK_SEVERITY).
CRITICAL_CHECKS = frozenset(
    name for name, sev in CHECK_SEVERITY.items()
    if sev is ValidationSeverity.CRITICAL
)


def has_critical_failure(result: dict) -> bool:
    """
    True se algum check CRITICAL falhou. Equivalente a
    ``not result['passed']`` no modo padrao, mas explicito para
    callers que querem testar severidade.
    """
    checks = result.get("checks", {})
    return any(
        not ok and CHECK_SEVERITY.get(name) is ValidationSeverity.CRITICAL
        for name, ok in checks.items()
    )


def has_warning_failure(result: dict) -> bool:
    """True se algum check WARNING falhou."""
    checks = result.get("checks", {})
    return any(
        not ok and CHECK_SEVERITY.get(name) is ValidationSeverity.WARNING
        for name, ok in checks.items()
    )


def has_info_failure(result: dict) -> bool:
    """True se algum check INFO falhou."""
    checks = result.get("checks", {})
    return any(
        not ok and CHECK_SEVERITY.get(name) is ValidationSeverity.INFO
        for name, ok in checks.items()
    )


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
    narrative: str, dossier: dict, prompt_version: str | None = None,
) -> tuple[bool, list[str]]:
    """
    Soft: ao menos N% dos file_ids devem aparecer.

    Threshold é 50% no modo padrão. Em modo **adversarial** o
    threshold cai para 30%, porque a seção "Contestações Hipotéticas"
    da narrativa V4 cita evidências limitadas por construção (cada
    contestação aponta a uma ou duas peças), o que naturalmente reduz
    a cobertura de file_ids sem indicar problema de qualidade
    (#33 — falso warning evitado).
    """
    timeline = dossier.get("events_timeline") or []
    fids = [e.get("file_id") for e in timeline if e.get("file_id")]
    if not fids:
        return True, []
    found = sum(1 for f in fids if f in narrative)
    ratio = found / len(fids)
    is_adversarial = bool(prompt_version) and "adversarial" in prompt_version
    threshold = 0.3 if is_adversarial else 0.5
    if ratio < threshold:
        return False, [
            f"apenas {found}/{len(fids)} file_ids ({ratio*100:.0f}%) "
            f"aparecem em narrativa (esperado >={threshold*100:.0f}%"
            + (", adversarial)" if is_adversarial else ")")
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
    prompt_version: str | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """
    Valida narrativa vs dossier. Retorna dict com:
      - passed: bool — modo padrão: ``True`` se todos os critical checks
        ok. Modo ``strict=True``: ``True`` apenas se críticos E warnings
        ok (info segue não-bloqueante).
      - checks: {check_name: bool}
      - warnings: list[str]
      - severities: {check_name: ValidationSeverity}
      - has_critical: bool
      - has_warning: bool

    Args:
        narrative_body: markdown sem bloco self_assessment
        dossier: dict original do dossier_builder
        self_assessment: dict parseado do bloco JSON
        full_narrative: markdown completo (body + bloco) — se None,
            usa narrative_body para checks de abertura/fechamento
        prompt_version: versão do prompt usada (ex: 'narrator_v4_adversarial').
            Quando 'adversarial' aparece no nome, o threshold de
            file_ids_preservados é relaxado (#33).
        strict: se True, falha em WARNING também invalida ``passed``.
            Default False mantém comportamento histórico (só CRITICAL
            bloqueia). Sessão 5 / #31.
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
    ok, w = _check_file_ids_preservados(full, dossier, prompt_version)
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

    # Severities resolvidas para cada check executado.
    severities = {
        name: CHECK_SEVERITY.get(name, ValidationSeverity.INFO)
        for name in checks
    }

    # passed (default) = todos os critical checks True
    # passed (strict)  = critical AND warning todos True (info ainda
    # nao-bloqueante)
    crit_ok = all(
        ok for name, ok in checks.items()
        if severities[name] is ValidationSeverity.CRITICAL
    )
    warn_ok = all(
        ok for name, ok in checks.items()
        if severities[name] is ValidationSeverity.WARNING
    )
    passed = (crit_ok and warn_ok) if strict else crit_ok

    return {
        "passed": passed,
        "checks": checks,
        "warnings": warnings,
        "severities": severities,
        "has_critical": not crit_ok,
        "has_warning": not warn_ok,
    }


__all__ = [
    "ValidationSeverity",
    "CHECK_SEVERITY",
    "has_critical_failure",
    "has_warning_failure",
    "has_info_failure",
    "CRITICAL_CHECKS",
    "INFERENCE_MARKERS",
    "MAX_BODY_CHARS",
    "MIN_BODY_CHARS",
    "validate_narrative",
]
