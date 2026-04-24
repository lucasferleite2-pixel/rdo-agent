"""
gen_laudo_example.py — Exemplo de uso do LaudoGenerator

Este script mostra como rdo-agent (ou qualquer outra aplicação Python)
pode usar o módulo vestigio_laudo pra gerar um laudo forense.

Os dados aqui são um exemplo realista baseado no caso EE Santa Quitéria,
renomeados/anonimizados pra fins de demonstração.
"""
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR))

from vestigio_laudo import (
    LaudoGenerator, LaudoData,
    SecaoNarrativa, EventoCronologia, Correlacao
)


# =============================================================================
# DADOS DO EXEMPLO — Caso fictício baseado em pattern real
# =============================================================================

dados = LaudoData(
    caso_id="VST-2026-0042",
    titulo="Análise Forense · EE Povoado de Santa Quitéria",
    periodo_inicio="15/11/2025",
    periodo_fim="28/03/2026",
    operador="Lucas Fernandes Leite",

    # Metadata do corpus
    corpus_hash="a7f2b4c9d1e8",
    total_mensagens=3847,
    total_documentos=127,
    total_audios=43,
    total_correlacoes=18,

    # Identificação
    cliente="Dr. Sérgio Albernaz · OAB-MG 142.XXX",
    processo="5002345-78.2026.8.13.0396",
    objeto="Apuração de divergências técnicas e contratuais entre o regime de execução previsto no Contrato nº 01/2026 e as condições efetivamente aplicadas ao canteiro de obra.",

    # Resumo executivo
    resumo_executivo=(
        "A análise forense do corpus de comunicações digitais do contrato nº 01/2026 "
        "identificou padrões recorrentes de divergência entre as exigências técnicas "
        "formalizadas em ordem de serviço e as orientações transmitidas informalmente "
        "pelo corpo de fiscalização. Foram detectadas 18 correlações com confiança "
        "superior a 70%, distribuídas em três categorias principais: temporal "
        "(sequência de eventos inconsistente com o cronograma contratual), semântica "
        "(uso de terminologia técnica divergente entre diferentes atores) e "
        "financeira (variações de escopo sem formalização contratual)."
    ),

    # Seções narrativas
    secoes_narrativa=[
        SecaoNarrativa(
            titulo="Contexto e escopo da perícia",
            conteudo=(
                "A presente perícia forense foi instaurada a pedido do contratado "
                "Vale Nobre Construtora e Imobiliária Ltda, no âmbito de controvérsia "
                "administrativa com o órgão fiscalizador SEE-MG / SRE Manhuaçu, "
                "tendo por objeto a execução do Contrato nº 01/2026, relativo à "
                "reforma de áreas específicas da EE Povoado de Santa Quitéria "
                "(CODESC 75817), no município de Santana do Manhuaçu, Minas Gerais.\n\n"
                "O corpus analisado compreende 3.847 mensagens trocadas em cinco "
                "canais distintos de WhatsApp, 127 documentos técnicos (planilhas "
                "de medição, memoriais descritivos, ordens de serviço, atas de "
                "reunião) e 43 áudios transcritos. Todo o material foi submetido "
                "a cadeia de custódia digital com hash sha256 preservado e "
                "reproduzível a partir dos arquivos originais.\n\n"
                "A metodologia empregada segue o fluxo operacional padrão do "
                "sistema Vestígio: extração estruturada dos eventos, detecção "
                "automática de correlações, construção de linha do tempo "
                "consolidada e produção de narrativa forense com marcação de "
                "pontos sensíveis pra apreciação jurídica."
            ),
        ),
        SecaoNarrativa(
            titulo="Achados relevantes",
            conteudo=(
                "No período analisado, identificaram-se três clusters de divergência "
                "especialmente relevantes ao mérito da controvérsia.\n\n"
                "O primeiro cluster envolve o item 150107 do Caderno de "
                "Especificações SEEMG 2025 Rev.01, que especifica simultaneamente "
                "o uso de agregados de alta dureza e granitina marmorizada — "
                "exigências tecnicamente incompatíveis segundo a escala de Mohs. "
                "O corpus evidencia que a fiscalização aceitou, em outras unidades "
                "sob jurisdição da mesma SRE, agregados calcários com dureza "
                "inferior à especificada, enquanto exigiu conformidade estrita "
                "no caso em análise.\n\n"
                "O segundo cluster diz respeito à sequência temporal de "
                "comunicações informais entre o engenheiro fiscal e a equipe "
                "do contratado. Em 15 ocasiões distintas, orientações transmitidas "
                "via WhatsApp contradisseram termos formalizados em ordem de "
                "serviço da mesma data, configurando antinomia operacional entre "
                "fontes normativas internas.\n\n"
                "O terceiro cluster refere-se a decisões financeiras: em três "
                "oportunidades, valores de medição foram ajustados unilateralmente "
                "pela fiscalização sem passagem formal por aditivo contratual, "
                "conforme evidenciado por excertos de mensagens e planilhas "
                "divergentes entre versões."
            ),
        ),
    ],

    # Cronologia
    cronologia=[
        EventoCronologia(
            data="15/11/2025",
            hora="09:14",
            autor="Eng. Fiscal (SEE-MG)",
            conteudo=(
                "Emissão de ordem de serviço para execução do item 150107, com "
                "exigência literal dos agregados de alta dureza conforme Caderno "
                "de Especificações."
            ),
            tipo="documento",
            tags=["OS", "item-150107"],
        ),
        EventoCronologia(
            data="17/11/2025",
            hora="14:32",
            autor="Encarregado de Obra",
            conteudo=(
                "Reporte do recebimento do primeiro lote de agregados conforme "
                "nota fiscal. Solicitação de instrução adicional sobre "
                "granitina marmorizada."
            ),
            tipo="mensagem",
        ),
        EventoCronologia(
            data="19/11/2025",
            hora="10:07",
            autor="Eng. Fiscal (SEE-MG)",
            conteudo=(
                "Orientação informal via WhatsApp: \"pode prosseguir com o lote, "
                "depois a gente ajusta\". Sem formalização em adendo à OS."
            ),
            tipo="decisao",
            tags=["informal", "antinomia"],
        ),
        EventoCronologia(
            data="02/12/2025",
            hora="16:45",
            autor="Eng. Responsável · Vale Nobre",
            conteudo=(
                "Emissão de memorando técnico apontando incompatibilidade entre "
                "agregados de alta dureza e granitina marmorizada conforme "
                "escala de Mohs. Solicitação de definição formal."
            ),
            tipo="documento",
            tags=["memorando", "alerta-tecnico"],
        ),
        EventoCronologia(
            data="15/01/2026",
            hora="11:20",
            autor="Financeiro · Vale Nobre",
            conteudo=(
                "Emissão da primeira medição do período, valor R$ 47.382,15, "
                "aprovada integralmente pela fiscalização."
            ),
            tipo="pagamento",
            tags=["medicao-01"],
        ),
        EventoCronologia(
            data="28/01/2026",
            hora="15:55",
            autor="Eng. Fiscal (SEE-MG)",
            conteudo=(
                "Reunião presencial em canteiro. Ata registra aceite técnico "
                "dos agregados, sem menção à divergência do item 150107."
            ),
            tipo="documento",
            tags=["ata-reuniao"],
        ),
        EventoCronologia(
            data="15/02/2026",
            hora="09:32",
            autor="Eng. Fiscal (SEE-MG)",
            conteudo=(
                "Emissão de notificação unilateral de rescisão contratual por "
                "alegado descumprimento do item 150107, com aplicação de penalidade "
                "de 10% sobre o valor contratual."
            ),
            tipo="decisao",
            tags=["rescisao", "penalidade"],
        ),
    ],

    # Correlações detectadas
    correlacoes=[
        Correlacao(
            tipo="TEMPORAL",
            descricao=(
                "Orientação informal em 19/11 contradiz ordem de serviço de 15/11, "
                "com intervalo de apenas 4 dias entre os documentos. A sequência "
                "configura inversão do fluxo normativo — comunicação informal "
                "sobrepondo-se a instrução formal anterior da mesma autoridade."
            ),
            excerto_a=(
                "Item 150107: exigência literal de agregados de alta dureza "
                "conforme Caderno de Especificações SEEMG 2025 Rev.01."
            ),
            excerto_b=(
                "Pode prosseguir com o lote, depois a gente ajusta."
            ),
            confianca=0.94,
        ),
        Correlacao(
            tipo="SEMANTIC",
            descricao=(
                "A expressão \"conformidade técnica\" aparece 23 vezes no corpus, "
                "com dois sentidos operacionais distintos: (1) aceite por "
                "inspeção visual informal, e (2) verificação documental por ensaio "
                "laboratorial. A alternância entre os sentidos não é sinalizada "
                "e varia conforme o autor e o momento do ciclo contratual."
            ),
            excerto_a=(
                "Vistoriei aqui rapidamente e tá dentro da conformidade técnica."
            ),
            excerto_b=(
                "Conformidade técnica depende da apresentação do laudo de ensaio "
                "do agregado — sem isso não libera."
            ),
            confianca=0.81,
        ),
        Correlacao(
            tipo="MATH",
            descricao=(
                "A planilha de medição 03 apresenta divergência de R$ 4.127,80 "
                "entre a versão preliminar aprovada pelo fiscal em 12/02 e a "
                "versão final registrada no sistema, sem formalização de aditivo "
                "ou justificativa técnica documentada."
            ),
            excerto_a=(
                "Planilha preliminar · 12/02/2026 · valor total R$ 62.418,00."
            ),
            excerto_b=(
                "Planilha final · 20/02/2026 · valor total R$ 58.290,20."
            ),
            confianca=0.97,
        ),
    ],

    # Controles
    incluir_marca_dagua_certificacao=True,
    incluir_cronologia_completa=True,
    versao_laudo="1.0",
)


# =============================================================================
# GERAR O PDF
# =============================================================================

if __name__ == "__main__":
    out_path = Path(__file__).parent / "Laudo-Exemplo-Santa-Quiteria.pdf"
    gen = LaudoGenerator()
    result = gen.generate(dados, out_path)

    size_kb = result.stat().st_size / 1024
    print(f"✓ Laudo gerado: {result}")
    print(f"  Tamanho: {size_kb:.1f} KB")
    print(f"  Caso: {dados.caso_id}")
    print(f"  Hash do corpus: {dados.corpus_hash}")
