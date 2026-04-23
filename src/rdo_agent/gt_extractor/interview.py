"""
interview.py — orquestrador sincrono do questionario (Fase D1).

Classes:
  - InterviewInput: parametros de entrada (obra, io functions)
  - InterviewSkipped: levantada quando operador digita 'stop' ANTES
    de preencher obra_real ou canal (secoes obrigatorias)

Funcao principal:
  - run_simple_interview(inp) -> GroundTruth

O extractor eh TESTAVEL: `input_fn` e `output_fn` sao injectaveis pra
mockar sessao interativa nos testes. Em producao, defaults sao
builtins `input()` e `print()`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rdo_agent.ground_truth.schema import (
    Canal,
    CanalParte,
    Contrato,
    EstadoAtual,
    GroundTruth,
    ObraReal,
    PagamentoConfirmado,
    PagamentoPendente,
    ProblemaConhecido,
    Totais,
)
from rdo_agent.gt_extractor.prompts import (
    CANAL_PARTE_QUESTIONS,
    CANAL_QUESTIONS,
    CONTRATO_QUESTIONS,
    OBRA_REAL_QUESTIONS,
    PAGAMENTO_CONF_QUESTIONS,
    PAGAMENTO_PEND_QUESTIONS,
    PROBLEMA_QUESTIONS,
    WELCOME_BANNER,
)

STOP_TOKENS = {"stop", "STOP", "quit", "QUIT"}
SKIP_TOKENS = {"skip", "SKIP", ""}


class InterviewSkipped(Exception):
    """Operador interrompeu antes de preencher secoes obrigatorias."""


@dataclass
class InterviewInput:
    obra: str
    output_path: Path
    input_fn: Callable[[str], str] = field(
        default_factory=lambda: _default_input,
    )
    output_fn: Callable[[str], None] = field(
        default_factory=lambda: _default_output,
    )


def _default_input(prompt: str) -> str:  # pragma: no cover - wrapper builtin
    return input(prompt)


def _default_output(msg: str) -> None:  # pragma: no cover - wrapper builtin
    print(msg)


def _ask(
    inp: InterviewInput, prompt: str, *, required: bool = False,
    default: str | None = None,
) -> str | None:
    """
    Pergunta um campo ao usuario.
    Retorna None se skipped (e nao-required).
    Levanta InterviewSkipped se stop.
    """
    while True:
        full = f"  {prompt}"
        if default is not None:
            full += f" [{default}]"
        full += ": "
        ans = inp.input_fn(full).strip()
        if ans in STOP_TOKENS:
            raise InterviewSkipped("stop by user")
        if ans in SKIP_TOKENS:
            if required and default is None:
                inp.output_fn("    (campo obrigatorio — preencha)")
                continue
            return default
        return ans


def _coerce(raw: str | None, type_hint: str | None) -> object:
    """Converte string para int/float se `type_hint` indicar; senao str."""
    if raw is None:
        return None
    if type_hint == "int":
        try:
            return int(raw)
        except ValueError:
            return None
    if type_hint == "float":
        try:
            # aceita formato BR "3.500,00" e internacional "3500.00"
            cleaned = raw.replace(".", "").replace(",", ".") \
                if "," in raw else raw
            return float(cleaned)
        except ValueError:
            return None
    return raw


def _ask_block(
    inp: InterviewInput, questions: list[dict],
) -> dict[str, object]:
    """Itera sobre lista de perguntas; retorna dict com responses."""
    out: dict[str, object] = {}
    for q in questions:
        ans = _ask(
            inp, q["question"],
            required=q.get("required", False),
            default=q.get("default"),
        )
        out[q["key"]] = _coerce(ans, q.get("type"))
    return out


def _ask_obra_real(inp: InterviewInput) -> ObraReal:
    inp.output_fn("\n--- Obra real ---")
    ans = _ask_block(inp, OBRA_REAL_QUESTIONS)
    return ObraReal(
        nome=str(ans["nome"]),
        contratada=str(ans["contratada"]),
        codesc=ans.get("codesc") if ans.get("codesc") is not None else None,
        municipio=ans.get("municipio"),
        uf=ans.get("uf"),
        contratante_publico=ans.get("contratante_publico"),
    )


def _ask_canal(inp: InterviewInput, obra_default: str) -> Canal:
    inp.output_fn("\n--- Canal ---")
    # pre-popula id com obra se operador nao sabe
    canal_qs = [
        {**q, "default": obra_default} if q["key"] == "id" else q
        for q in CANAL_QUESTIONS
    ]
    ans = _ask_block(inp, canal_qs)
    inp.output_fn("  Parte A (normalmente o operador):")
    pa = _ask_block(inp, CANAL_PARTE_QUESTIONS)
    inp.output_fn("  Parte B (contraparte):")
    pb = _ask_block(inp, CANAL_PARTE_QUESTIONS)
    return Canal(
        id=str(ans["id"]),
        tipo=str(ans["tipo"]),
        parte_A=CanalParte(
            nome=str(pa["nome"]),
            papel=str(pa["papel"]),
            especialidade=pa.get("especialidade"),
        ),
        parte_B=CanalParte(
            nome=str(pb["nome"]),
            papel=str(pb["papel"]),
            especialidade=pb.get("especialidade"),
        ),
    )


def _ask_contratos(inp: InterviewInput) -> list[Contrato]:
    inp.output_fn("\n--- Contratos ---")
    inp.output_fn("Quantos contratos? (Enter=0)")
    ans = _ask(inp, "numero de contratos", required=False, default="0")
    try:
        n = int(ans or "0")
    except ValueError:
        n = 0
    contratos: list[Contrato] = []
    for i in range(n):
        inp.output_fn(f"\n  Contrato #{i + 1}:")
        fields = _ask_block(inp, CONTRATO_QUESTIONS)
        valor = fields.get("valor_total")
        if valor is None:
            inp.output_fn("    (valor_total invalido; pulando contrato)")
            continue
        contratos.append(Contrato(
            id=str(fields["id"]),
            escopo=str(fields["escopo"]),
            valor_total=float(valor),
            forma_pagamento=fields.get("forma_pagamento"),
            origem=fields.get("origem"),
            data_acordo=fields.get("data_acordo"),
            status=fields.get("status"),
            observacao=fields.get("observacao"),
        ))
    return contratos


def _ask_pagamentos_confirmados(
    inp: InterviewInput,
) -> list[PagamentoConfirmado]:
    inp.output_fn("\n--- Pagamentos confirmados ---")
    inp.output_fn("Quantos pagamentos ja efetivados? (Enter=0)")
    ans = _ask(inp, "numero", required=False, default="0")
    try:
        n = int(ans or "0")
    except ValueError:
        n = 0
    out: list[PagamentoConfirmado] = []
    for i in range(n):
        inp.output_fn(f"\n  Pagamento confirmado #{i + 1}:")
        fields = _ask_block(inp, PAGAMENTO_CONF_QUESTIONS)
        valor = fields.get("valor")
        data = fields.get("data")
        if valor is None or not data:
            inp.output_fn("    (valor/data invalido; pulando)")
            continue
        out.append(PagamentoConfirmado(
            valor=float(valor),
            data=str(data),
            hora=fields.get("hora"),
            contrato_ref=fields.get("contrato_ref") or None,
            parcela=fields.get("parcela"),
            tipo=fields.get("tipo"),
            descricao_pix=fields.get("descricao_pix"),
            nota=fields.get("nota"),
        ))
    return out


def _ask_pagamentos_pendentes(
    inp: InterviewInput,
) -> list[PagamentoPendente]:
    inp.output_fn("\n--- Pagamentos pendentes ---")
    ans = _ask(
        inp, "quantos pendentes (Enter=0)",
        required=False, default="0",
    )
    try:
        n = int(ans or "0")
    except ValueError:
        n = 0
    out: list[PagamentoPendente] = []
    for i in range(n):
        inp.output_fn(f"\n  Pendente #{i + 1}:")
        fields = _ask_block(inp, PAGAMENTO_PEND_QUESTIONS)
        valor = fields.get("valor")
        if valor is None:
            inp.output_fn("    (valor invalido; pulando)")
            continue
        out.append(PagamentoPendente(
            valor=float(valor),
            contrato_ref=fields.get("contrato_ref") or None,
            parcela=fields.get("parcela"),
            gatilho_pagamento=fields.get("gatilho_pagamento"),
            data_prevista=fields.get("data_prevista"),
            nota=fields.get("nota"),
        ))
    return out


def _ask_problemas(
    inp: InterviewInput,
) -> list[ProblemaConhecido]:
    inp.output_fn("\n--- Problemas conhecidos ---")
    ans = _ask(
        inp, "quantos problemas relevantes (Enter=0)",
        required=False, default="0",
    )
    try:
        n = int(ans or "0")
    except ValueError:
        n = 0
    out: list[ProblemaConhecido] = []
    for i in range(n):
        inp.output_fn(f"\n  Problema #{i + 1}:")
        fields = _ask_block(inp, PROBLEMA_QUESTIONS)
        desc = fields.get("descricao")
        if not desc:
            inp.output_fn("    (descricao vazia; pulando)")
            continue
        out.append(ProblemaConhecido(
            descricao=str(desc),
            detectado_em=fields.get("detectado_em"),
            impacto=fields.get("impacto"),
            responsabilidade=fields.get("responsabilidade"),
        ))
    return out


def run_simple_interview(inp: InterviewInput) -> GroundTruth:
    """
    Entrevista sincrona sem IA. Retorna GroundTruth pronto pra
    serializacao via yaml_writer.

    `InterviewSkipped` eh levantada se operador digita 'stop' ANTES
    de preencher obra_real ou canal (secoes sem fallback razoavel).
    """
    inp.output_fn(WELCOME_BANNER)

    obra_real = _ask_obra_real(inp)
    canal = _ask_canal(inp, obra_default=inp.obra)

    # Secoes opcionais: stop aqui nao deve abortar, so cortar
    try:
        contratos = _ask_contratos(inp)
    except InterviewSkipped:
        contratos = []
    try:
        pag_conf = _ask_pagamentos_confirmados(inp)
    except InterviewSkipped:
        pag_conf = []
    try:
        pag_pend = _ask_pagamentos_pendentes(inp)
    except InterviewSkipped:
        pag_pend = []
    try:
        problemas = _ask_problemas(inp)
    except InterviewSkipped:
        problemas = []

    estado = EstadoAtual(problemas_conhecidos=problemas) if problemas else None

    gt = GroundTruth(
        obra_real=obra_real,
        canal=canal,
        contratos=contratos,
        pagamentos_confirmados=pag_conf,
        pagamentos_pendentes=pag_pend,
        totais=Totais(),
        estado_atual=estado,
        aspectos_nao_registrados_em_evidencia=[],
    )
    return gt


__all__ = [
    "InterviewInput",
    "InterviewSkipped",
    "run_simple_interview",
]
