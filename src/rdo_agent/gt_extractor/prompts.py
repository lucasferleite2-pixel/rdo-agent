"""
prompts.py — questionario canonico do extrator sincrono (Fase D1).

As perguntas sao organizadas por secao e replicam a schema de GT.
Cada pergunta tem:
  - key: nome do campo no dataclass
  - question: texto exibido ao operador
  - required: True se bloqueia se vazio
  - default: valor default quando operador digita Enter vazio (so se
    required=False)

Mantido como lista de dicts em vez de classes pra ficar legivel e
facilmente expansivel.
"""

from __future__ import annotations


# Dicas comuns exibidas no inicio
WELCOME_BANNER = """
=== Extrator de Ground Truth — modo SIMPLE ===

Vou te fazer perguntas sobre a obra, canal, contratos e pagamentos.
Digite 'skip' (ou Enter vazio em campos opcionais) pra pular.
Digite 'stop' a qualquer momento para interromper e salvar o que
foi preenchido ate aqui.

Campos marcados com * sao obrigatorios.
"""

OBRA_REAL_QUESTIONS: list[dict] = [
    {"key": "nome", "question": "Nome da obra* (ex: Reforma EE X)",
     "required": True},
    {"key": "contratada", "question": "Empresa contratada*",
     "required": True},
    {"key": "codesc", "question": "Codigo interno (CODESC, numero)",
     "required": False, "type": "int"},
    {"key": "municipio", "question": "Municipio", "required": False},
    {"key": "uf", "question": "UF (sigla estado)", "required": False},
    {"key": "contratante_publico",
     "question": "Contratante publico (se obra publica)",
     "required": False},
]

CANAL_QUESTIONS: list[dict] = [
    {"key": "id", "question": "ID do canal* (ex: EVERALDO_SANTAQUITERIA)",
     "required": True},
    {"key": "tipo", "question": "Tipo do canal* (whatsapp/email/sms)",
     "required": True, "default": "whatsapp"},
]

CANAL_PARTE_QUESTIONS: list[dict] = [
    {"key": "nome", "question": "Nome*", "required": True},
    {"key": "papel",
     "question": "Papel* (ex: representante_empresa, prestador_servico)",
     "required": True},
    {"key": "especialidade",
     "question": "Especialidade (ex: serralheiro, pedreiro)",
     "required": False},
]

CONTRATO_QUESTIONS: list[dict] = [
    {"key": "id", "question": "ID do contrato* (ex: C1, C2)",
     "required": True},
    {"key": "escopo", "question": "Escopo*", "required": True},
    {"key": "valor_total", "question": "Valor total em R$*",
     "required": True, "type": "float"},
    {"key": "forma_pagamento",
     "question": "Forma de pagamento (ex: 50/50 sinal+saldo)",
     "required": False},
    {"key": "origem",
     "question": "Origem do acordo (ex: whatsapp_06_04, reuniao)",
     "required": False},
    {"key": "data_acordo", "question": "Data do acordo (YYYY-MM-DD)",
     "required": False},
    {"key": "status",
     "question": "Status (ex: quitado, em_execucao_50pct_pago)",
     "required": False},
    {"key": "observacao", "question": "Observacao", "required": False},
]

PAGAMENTO_CONF_QUESTIONS: list[dict] = [
    {"key": "valor", "question": "Valor pago em R$*",
     "required": True, "type": "float"},
    {"key": "data", "question": "Data* (YYYY-MM-DD)", "required": True},
    {"key": "hora", "question": "Hora (HH:MM)", "required": False},
    {"key": "contrato_ref",
     "question": "Contrato de referencia (C1/C2/... ou vazio se fora)",
     "required": False},
    {"key": "parcela",
     "question": "Parcela (ex: sinal_50pct, saldo_50pct)",
     "required": False},
    {"key": "tipo",
     "question": "Tipo (ex: reembolso_operacional) — vazio se contratual",
     "required": False},
    {"key": "descricao_pix",
     "question": "Descricao do PIX (se houver)",
     "required": False},
    {"key": "nota", "question": "Nota interpretativa", "required": False},
]

PAGAMENTO_PEND_QUESTIONS: list[dict] = [
    {"key": "valor", "question": "Valor pendente em R$*",
     "required": True, "type": "float"},
    {"key": "contrato_ref",
     "question": "Contrato de referencia (C1/C2/...)",
     "required": False},
    {"key": "parcela",
     "question": "Parcela (ex: saldo_50pct)",
     "required": False},
    {"key": "gatilho_pagamento",
     "question": "Gatilho (ex: conclusao_do_servico)",
     "required": False},
    {"key": "data_prevista",
     "question": "Data prevista (YYYY-MM-DD ou vazio)",
     "required": False},
    {"key": "nota", "question": "Nota", "required": False},
]

PROBLEMA_QUESTIONS: list[dict] = [
    {"key": "descricao", "question": "Descricao do problema*",
     "required": True},
    {"key": "detectado_em", "question": "Detectado em (YYYY-MM-DD)",
     "required": False},
    {"key": "impacto", "question": "Impacto", "required": False},
    {"key": "responsabilidade",
     "question": "Responsabilidade (ex: terceiros, everaldo, vale_nobre)",
     "required": False},
]


__all__ = [
    "CANAL_PARTE_QUESTIONS",
    "CANAL_QUESTIONS",
    "CONTRATO_QUESTIONS",
    "OBRA_REAL_QUESTIONS",
    "PAGAMENTO_CONF_QUESTIONS",
    "PAGAMENTO_PEND_QUESTIONS",
    "PROBLEMA_QUESTIONS",
    "WELCOME_BANNER",
]
