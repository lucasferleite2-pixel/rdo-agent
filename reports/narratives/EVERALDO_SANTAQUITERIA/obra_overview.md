# Narrativa: EVERALDO_SANTAQUITERIA — Obra Overview

## Sumário Executivo

O canal WhatsApp identificado como EVERALDO_SANTAQUITERIA registra a comunicação entre Lucas Fernandes Leite (representante da Construtora e Imobiliária Vale Nobre Ltda) e Everaldo Caitano Baia (serralheiro prestador de serviço) no contexto da **Reforma da Escola Estadual Povoado de Santa Quitéria** (CODESC 75817, município de Santana do Manhuaçu/MG, contratante público SEE-MG / SRE Manhuaçu). O corpus cobre o período de **4 a 16 de abril de 2026**, totalizando 239 eventos classificados (195 amostrados neste dossier), distribuídos em 11 dias de comunicação ativa. O volume é expressivo — com picos de 48 eventos em 08/04 e 52 em 15/04 — e o conteúdo é dominado por negociação comercial, especificação técnica, reporte de execução e pagamentos, com substancial ruído off-topic. Dois contratos foram firmados no período (C1 e C2), quatro transferências PIX foram realizadas, e a obra permanecia em execução ao momento do snapshot do operador (23/04/2026).

---

## Cronologia em Bloco

### Bloco 1 — Abertura da Negociação e Impasse Inicial (04/04/2026)

O dia 4 de abril é o mais denso em termos narrativos: 37 eventos, com arco que vai da apresentação do escopo à negociação intensa e ao pré-acordo verbal. Às 11h48, Everaldo Caitano Baia já se encontrava em outro serviço e sinalizou que ligaria ao interlocutor ao terminar (f_22e21dda92e2). À tarde, às 15h48, iniciou-se a discussão técnica sobre o escopo: colocação de tesouras, ripamento e fechamento (f_8a79177d6646). Às 16h12, Lucas Fernandes Leite enviou ao canal o arquivo "ESCOLA ESTADUAL POOVOADO DE SANTA QUITERIA FOLHA 02-Model.pdf" (m_11ecc90fd074), sugerindo que a planta ou projeto da obra foi compartilhado neste momento como referência para o orçamento.

A partir das 16h16, a conversa técnica se aprofundou: discutiu-se se a tela de fechamento estava incluída no escopo ou era item separado (f_d74291d5609d; m_36afc1062203). Everaldo Caitano Baia esclareceu que a tela seria separada e que o orçamento cobriria apenas telhado e fechamento estrutural (f_14819610a954). Lucas Fernandes Leite confirmou: "Vamos combinar só no telhado e no fechamento então" (m_59549b1cc373), com Everaldo Caitano Baia concordando (f_1d2f1aaf3319).

À noite, a negociação de valores se intensificou. Às 19h36, Everaldo Caitano Baia apresentou sua proposta inicial de **R$12.000,00** para o serviço completo de fechamento e telhado (f_5698c4479dc7). Às 20h43, ele próprio recuou para **R$11.000,00**, justificando que havia pago material (f_386aa52c2bc2). Lucas Fernandes Leite, às 22h06, sinalizou resistência ao modelo de pagamento proposto — exigindo metade adiantada antes do início — e explicou a lógica de pagamento por etapa: metade ao colocar o pé na obra, metade ao finalizar (f_e3958cb0696e). Everaldo Caitano Baia, às 22h06, respondeu que só trabalharia com sinal (f_8bad08231b6a). O dia encerrou sem acordo formal, mas com as partes alinhadas em torno de R$11.000,00 e do modelo 50/50.

> **Segundo informação complementar do operador — sem evidência no canal WhatsApp:** as negociações de 04 e 05/04 podem ter tido continuidade fora do WhatsApp; o acordo formal do C1 só se materializou em 06/04.

---

### Bloco 2 — Fechamento do C1 e Primeiro Pagamento (05–06/04/2026)

O dia 5 de abril registrou apenas 5 eventos (off-topic e cronograma), sem negociação relevante amostrada. Em 06/04, com 12 eventos, o canal registra o **fechamento do Contrato C1** e o pagamento do sinal correspondente.

Às 11h13 de 06/04, a Construtora e Imobiliária Vale Nobre Ltda realizou PIX de **R$3.500,00** para Everaldo Caitano Baia, com descrição explícita: *"50% de sinal do serviço de serralheria (subir e instalar tesouras e terças do telhado e fechamento)"* (f_7d3f788778ab). Este pagamento corresponde ao **sinal de 50% do C1** (valor total R$7.000,00), conforme confirmado pelo ground truth.

A correlação temporal validada (TEMPORAL_PAYMENT_CONTEXT, confidence 1,0) indica que, aproximadamente 3 minutos e 35 segundos antes deste PIX (delta = −215s), houve mensagem com múltiplas keywords de pagamento no canal (c_131) — padrão consistente com a solicitação da chave PIX imediatamente antes da transferência. As correlações semânticas validadas (SEMANTIC_PAYMENT_SCOPE, confidence 0,80) ligam este pagamento às negociações de 04/04 sobre "serviço", "telhado", "tesoura" e "terça" (c_3, c_9, c_16), confirmando que o PIX de R$3.500,00 é o desdobramento direto das tratativas iniciadas dois dias antes.

> **Nota forense:** A descrição do PIX menciona "fechamento", o que poderia gerar ambiguidade. Segundo o ground truth, este "fechamento" refere-se ao **esqueleto estrutural** (C1), não ao fechamento com tela/alambrado (C2). Não há divergência detectada — a descrição é consistente com o escopo do C1.

---

### Bloco 3 — Execução do C1 e Reabertura de Negociação (07–08/04/2026)

Em 07/04 (7 eventos: material e especificação técnica), o canal registra discussões sobre materiais e detalhes técnicos, sugerindo que Everaldo Caitano Baia já estava mobilizado ou em preparação para o serviço.

O dia 08/04 é o segundo mais denso do corpus (48 eventos) e constitui um **ponto de inflexão narrativo**: a negociação do C2 foi aberta, travada e fechada no mesmo dia. Pela manhã, Everaldo Caitano Baia retomou a discussão de escopo e valores, apresentando cálculo de R$50/metro para o fechamento lateral e sinalizando que o valor de R$11.000,00 anteriormente discutido não cobria o fechamento completo — apenas o telhado (f_c9e008c74a4c; f_4a5b4347572f). Lucas Fernandes Leite reagiu: "Você não tinha falado comigo que você ia fazer 11 mil e tudo?" (f_9f1926960841), evidenciando divergência de entendimento sobre o escopo do acordo anterior.

Everaldo Caitano Baia esclareceu a estrutura de dois contratos: **R$7.000,00 para deixar engradado** (C1, já com sinal pago) e **R$11.000,00 para o acabamento completo** (C2 — telhado, calha, fechamento, alambrado) (f_8d71fada5763). Lucas Fernandes Leite, às 09h03, revelou sua restrição orçamentária: "eu tenho só sete mil, além do que eu já tenho" (f_c9073788ceac), e às 20h31 propôs fechar em R$10.000,00 com sinal imediato de R$5.000,00 (f_6b289ad150a3).

Às 20h36, Lucas Fernandes Leite recuou para o valor original: "vamos fechar nos 11 então, só que eu vou te pagar junto com os R$3.500 que eu vou te pagar no final do ripamento, aí eu te pago os R$3.500 mais os R$5.500 do outro" (f_5fa1642121f7) — correlação MATH_VALUE_MATCH validada (confidence 1,0) liga esta menção de R$3.500,00 ao segundo PIX do C1 (fr_2), que seria pago em 10/04. Às 20h38, Everaldo Caitano Baia ajustou: pediu que Lucas Fernandes Leite mandasse apenas R$3.000,00 de sinal do C2, ficando com R$2.000,00 para repassar a um auxiliar (f_8818a3e9ce31). Às 20h39, Lucas Fernandes Leite respondeu "Fechado" (m_2bc09ed4e365) — **C2 formalmente acordado**.

Everaldo Caitano Baia comprometeu-se a trabalhar quinta e sexta na obra, com possibilidade de sábado (f_74dc8c677f7f), e reforçou que a equipe trabalha rápido (f_cc41abc4b46b).

---

### Bloco 4 — Quitação do C1 e Levantamento de Material (09–10/04/2026)

Em 09/04 (17 eventos: off-topic, cronograma, reporte de execução), o canal registra acompanhamento de andamento, sem eventos financeiros amostrados.

Em 10/04 (26 eventos: material, especificação técnica), o dia foi marcado por dois movimentos simultâneos: **quitação do C1** e **levantamento detalhado de material para o C2**.

Às 12h04, Everaldo Caitano Baia solicitou a chave PIX e o envio do dinheiro (f_70ec3d40eb02). Às 12h17, a chave foi fornecida por texto: "33988420122 Pix" (m_8ff36e5e7e97). Às 12h42, a Vale Nobre realizou PIX de **R$3.500,00** para Everaldo Caitano Baia, sem descrição (f_56bd10688411). Este pagamento corresponde ao **saldo de 50% do C1**, conforme ground truth. A correlação MATH_VALUE_MATCH validada (confidence 1,0) confirma que a menção de R$3.500,00 em c_60 (negociação de 08/04) antecipava exatamente este valor (delta = −73.589s, aproximadamente 20 horas antes).

Ao longo da tarde, Everaldo Caitano Baia e Lucas Fernandes Leite trocaram especificações detalhadas de telhas: 21 telhas de 5,10m + 21 telhas de 8,20m para o telhado (m_274b9d5a5747); 40 telhas de 2m onduladas para fechamento lateral (m_1d433df1e45b); 20 telhas de 2,80m para fechamento frontal (m_79d2e366feaf). Everaldo Caitano Baia alertou que no fechamento frontal haveria perda de material por corte em grau (f_b244b43ff177; f_a35295f7a404).

---

### Bloco 5 — Execução em Campo e Reembolso Operacional (11–14/04/2026)

Os dias 11 a 13/04 registraram atividade reduzida (3 eventos em 13/04: especificação técnica e material), sugerindo que Everaldo Caitano Baia estava em campo com comunicação mínima.

Em 14/04 (27 eventos), o canal registrou um **reembolso operacional** e o relato de um incidente externo. Às 12h26, Everaldo Caitano Baia informou que precisava de tinta e gasolina para o serviço (f_b5a81acff938). Às 12h27, Lucas Fernandes Leite solicitou a chave PIX (m_7001eefb6b51); às 12h30, a chave foi fornecida (m_9f39869314d6); às 12h31, Lucas Fernandes Leite perguntou o valor necessário (m_13a7d103cfe5); às 12h34, Everaldo Caitano Baia respondeu "30 da" (m_1491f59721eb). Às 13h43, a Vale Nobre realizou PIX de **R$30,00** com descrição "Gasolina tinta" (f_d0e30d9f1533) — reembolso operacional fora dos contratos principais, conforme ground truth.

À tarde, Everaldo Caitano Baia relatou que havia tombado seu trator no dia anterior e conseguido endireitá-lo (f_926046b3000c; f_1cc81fd7236d). Múltiplas análises visuais do mesmo horário (17h06) confirmam imagens de trator tombado/estacionado (f_6aabe626c533; f_0f35f87c47e5). Às 18h32, Lucas Fernandes Leite informou: "Amanhã acaba o rolamento lá tá ai vamos continuar no lambrado" (m_7ac9e5e7adb7), indicando que o ripamento estava em fase final.

---

### Bloco 6 — Ripamento Concluído, Problema no Alambrado e Sinal do C2 (15–16/04/2026)

O dia 15/04 é o mais denso do corpus (52 eventos) e concentra o **marco de conclusão do ripamento** e a **descoberta de problema técnico grave no alambrado**.

Às 09h35, Everaldo Caitano Baia reclamou da falta de eletrodo para solda, sinalizando que o material de consumo deveria ser fornecido pela contratante (f_1a95546ac312). Às 10h36, reportou progresso: "tudo pronto, só falta esse vão aqui" (f_48d9c497429e). Às 10h40, Lucas Fernandes Leite declarou por texto: "Ripamento liberado" (m_341b91627965), confirmado em áudio por Everaldo Caitano Baia às 10h48: "o ripamento ta liberado, tá? Todos os filé. Prontinho. Agora começar nos alambrados ali." (f_58eca1943d80).

A partir das 13h03, o canal registrou uma sequência de reportes técnicos preocupantes: Everaldo Caitano Baia identificou que o alambrado havia sido instalado por terceiros com medidas erradas — peças cortadas abaixo de 2 metros, instaladas torcidas, exigindo retrabalho extenso (f_8e34259c9abb; f_3da4b041e3d3; f_7727cd122b1e). Às 13h07, medição com fita métrica confirmou déficit de 4 centímetros na altura (f_d3862d087302; f_a7aea9b9bc34). Às 17h28, Everaldo Caitano Baia estimou que o alambrado consumiria o restante da semana (f_0ae453ef91c1). Lucas Fernandes Leite confirmou: "tá tudo errado as medidas lá / Isso que vai atrasar" (m_1c611436ebd0; m_77b2988cad83).

> Segundo o ground truth, a responsabilidade pelo alambrado com medidas erradas é de **terceiros anteriores** à intervenção de Everaldo Caitano Baia — fato corroborado pelas mensagens do canal, que atribuem o erro a "os caras" que fizeram o serviço antes.

Em 16/04 (5 eventos), às 10h08, Everaldo Caitano Baia solicitou pagamento, mencionando que "em cima tá terminado" (f_12da80b0a8e3). Às 10h15, a chave PIX foi fornecida novamente (m_ce36775e9749). Às 10h17, a Vale Nobre realizou PIX de **R$5.500,00** com descrição "Metade do serviço telhado" (f_447ffc4b9024) — este é o **sinal de 50% do C2**. Às 10h20, Everaldo Caitano Baia respondeu "Muito obrigado" (m_8921a35649fb).

---

## Ledger Financeiro Consolidado

| Data | Hora | Valor | Contrato | Parcela | Descrição PIX | Arquivo |
|---|---|---|---|---|---|---|
| 06/04/2026 | 11:13 | **R$ 3.500,00** | C1 | Sinal 50% | "50% de sinal do serviço de serralheria (subir e instalar tesouras e terças do telhado e fechamento)" | f_7d3f788778ab |
| 10/04/2026 | 12:42 | **R$ 3.500,00** | C1 | Saldo 50% | *(sem descrição)* | f_56bd10688411 |
| 14/04/2026 | 13:43 | **R$ 30,00** | — | Reembolso operacional | "Gasolina tinta" | f_d0e30d9f1533 |
| 16/04/2026 | 10:17 | **R$ 5.500,00** | C2 | Sinal 50% | "Metade do serviço telhado" | f_447ffc4b9024 |
| **TOTAL PAGO** | | **R$ 12.530,00** | | | | |
| **PENDENTE** | | **R$ 5.500,00** | C2 | Saldo 50% | A pagar na conclusão | — |
| **TOTAL NEGOCIADO** | | **R$ 18.000,00** | | | | |

O C1 (R$7.000,00) está **integralmente quitado**. O C2 (R$11.000,00) tem 50% pago (R$5.500,00) e saldo de R$5.500,00 pendente, condicionado à conclusão do serviço. O reembolso de R$30,00 é item externo aos contratos principais. O percentual financeiro concluído é de **69,4%** do total negociado.

---

## Padrões Observados

**Padrão de pagamento PIX com solicitação de chave:** Em todos os três pagamentos contratuais, o fluxo foi idêntico — Everaldo Caitano Baia sinalizou disponibilidade ou conclusão de etapa, Lucas Fernandes Leite solicitou a chave PIX, a chave foi fornecida por texto (número de telefone 33988420122), e o PIX foi executado em minutos. Este padrão é consistente e não apresenta desvios.

**Negociação em múltiplas rodadas:** O C1 levou dois dias de negociação (04–06/04) antes do fechamento; o C2 foi negociado e fechado no mesmo dia (08/04), mas com múltiplas rodadas de proposta e contraproposta no mesmo dia (R$12.000 → R$11.000 → R$10.000 → R$11.000 final). Isso sugere que Lucas Fernandes Leite tinha restrição orçamentária real e que Everaldo Caitano Baia tinha margem limitada para concessão.

**Gap de comunicação 11–13/04:** Apenas 3 eventos registrados em 13/04 e nenhum amostrado em 11–12/04, sugerindo que Everaldo Caitano Baia estava em campo com comunicação mínima — padrão típico de execução intensa.

**Ruído off-topic elevado:** 96 dos 239 eventos (40,2%) foram classificados como off-topic, incluindo o episódio do trator tombado (14/04) e conversas sobre máquinas de solda (08/04). Isso é característico de canal de relacionamento pessoal entre contratante e prestador, não de canal exclusivamente operacional.

---

## Observações Forenses

**1. Divergência de entendimento de escopo (08/04):** A troca de mensagens da manhã de 08/04 revela que Lucas Fernandes Leite entendia que R$11.000,00 cobria "tudo" (telhado + fechamento completo), enquanto Everaldo Caitano Baia entendia que R$11.000,00 era o C2 (acabamento), separado do C1 (esqueleto, R$7.000,00). A resolução foi explicitada por Everaldo Caitano Baia em f_8d71fada5763 e aceita por Lucas Fernandes Leite. Não há divergência residual detectada — o entendimento foi alinhado no mesmo dia.

**2. MATH_VALUE_DIVERGENCE — atenção:** O dossier registra 2 correlações do tipo MATH_VALUE_DIVERGENCE (não validadas, confidence < 0,70). Embora não detalhadas nos top_validated, a presença deste tipo de correlação sugere que em algum momento valores mencionados no corpus estão na faixa de 50%–150% do valor pago sem match exato. Possivelmente relacionado às propostas intermediárias de R$10.000,00 (C2) ou R$12.000,00 (proposta inicial) que não se converteram em pagamento. Recomenda-se verificação manual dos eventos correlacionados.

**3. Sinal do C2 menor que o acordado:** Na negociação de 08/04, Everaldo Caitano Baia pediu que Lucas Fernandes Leite mandasse R$3.000,00 de sinal do C2 (f_8818a3e9ce31), não R$5.500,00 (50% de R$11.000,00). O PIX efetivamente pago em 16/04 foi de R$5.500,00 — valor correspondente a 50% exato do C2. Isso sugere que o modelo 50/50 prevaleceu sobre o ajuste pedido por Everaldo Caitano Baia em 08/04, possivelmente por acordo posterior não registrado no corpus amostrado.

**4. Ausência de contrato escrito:** Segundo o ground truth, nenhum contrato formal foi firmado — todos os acordos são verbais via áudio WhatsApp. Isso é um ponto de atenção para fins de comprovação contratual, especialmente considerando que a obra é de reforma de escola pública (SEE-MG / SRE Manhuaçu).

**5. Problema no alambrado — responsabilidade de terceiros:** As mensagens de 15/04 são inequívocas em atribuir o erro de medidas do