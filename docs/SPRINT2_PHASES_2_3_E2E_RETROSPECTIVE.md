# Sprint 2 — Fases 2 e 3 (TRANSCRIBE + VISUAL_ANALYSIS) — Retrospectiva pós-E2E

Documento de fechamento consolidado das Fases 2 e 3 do Sprint 2 após validação E2E contra a vault real `EVERALDO_SANTAQUITERIA` nos dias 2026-04-19 (noite) e 2026-04-20 (manhã). Complementa e atualiza a retrospectiva preliminar `SPRINT2_PHASE2_RETROSPECTIVE.md` escrita em 18/abril, antes do primeiro E2E contra dados reais.

Ver também: `SPRINT2_PLAN.md` (plano original), `SPRINT2_BACKLOG.md` (dívidas pré-sprint), `SPRINT2_PHASE2_RETROSPECTIVE.md` (retrospectiva preliminar Fase 2).

## Contexto de saída

Após E2E concluído contra `EVERALDO_SANTAQUITERIA`, o sistema tem:

- **Fase 2 (TRANSCRIBE/Whisper):** 105/105 áudios transcritos, 0 falhas, 0 sentinels, custo US$ 0.2507, duração 5min 33s
- **Fase 3 (VISUAL_ANALYSIS/Vision):** 10/10 imagens analisadas, 0 falhas, 4 sentinels legítimos, custo US$ 0.3024, duração ~1min
- Suite completa: **134/134 verde** (+10 testes do visual_analyzer agregados à suite anterior de 124)
- CLI unificada `rdo-agent process` + `rdo-agent status` (commit `ba4e255`) — substituiu os scripts ad-hoc, deletados em `b97d6d5`
- Três bugs descobertos durante E2E, todos com fix commitado
- Primeiro registro empírico do sistema operando fim-a-fim em dados de produção

**Gasto acumulado durante validação:** US$ 0.56 (Fases 2 + 3 somadas, incluindo smokes).

**Estado do Sprint 2:** ✅ **FECHADO** em 2026-04-20. Fases 1-4 completas e validadas contra vault real. Tag git: `v0.2.0-sprint2` (se criada).

---

## Parte I — Adendo empírico à Fase 2 (TRANSCRIBE)

A retrospectiva preliminar Fase 2 foi escrita em 18/abril com base em 124/124 testes unitários e golden fixture, mas sem validação contra áudios reais. O E2E em 19/abril revelou três coisas importantes não previstas.

### Bug descoberto: Whisper rejeita extensão `.opus`

**Sintoma:** primeira tentativa de E2E, task `id=1`, áudio `00000003-AUDIO-2026-04-04-11-48-41.opus` (14KB):
openai.BadRequestError: Error code: 400 - {'error': {'message':
"Invalid file format. Supported formats: ['flac', 'm4a', 'mp3',
'mp4', 'mpeg', 'mpga', 'oga', 'ogg', 'wav', 'webm']"}}

**Causa raiz:** 100% dos áudios exportados do WhatsApp vêm como `.opus`. A API Whisper aceita container OGG (`.ogg`, `.oga`) mas rejeita o sufixo `.opus` mesmo quando o codec Opus é exatamente o mesmo — a validação é puramente por extensão do nome do arquivo.

**Por que a golden fixture não pegou:** o `capture_whisper_fixture.py` usou áudio sintético gerado via ffmpeg com saída `.wav`, nunca testou com `.opus` real. A lacuna estava explícita na retrospectiva preliminar: *"validação E2E contra áudios reais da vault está pendente"*.

**Fix (commit `b5aecf8`, 2026-04-19 23:38):** 5 linhas em `src/rdo_agent/transcriber/__init__.py`. Passa tuple `(upload_name, file_bytes, "audio/ogg")` ao SDK, renomeando só o nome de upload de `.opus` para `.ogg`. Arquivo em disco, `request_hash` e `response_hash` continuam capturando o nome original — o rename é artefato de upload, não muda registro forense.

```python
upload_name = audio_path.name
if audio_path.suffix.lower() == ".opus":
    upload_name = audio_path.stem + ".ogg"
response = client.audio.transcriptions.create(
    model=MODEL,
    language=LANGUAGE,
    temperature=TEMPERATURE,
    response_format=RESPONSE_FORMAT,
    file=(upload_name, f, "audio/ogg"),
)
```

**Testes continuaram passando** (10/10 `test_transcriber.py`) porque os mocks são frouxos — inspeccionam estrutura geral da chamada, não o tipo exato do arg `file`. Isso é uma lição sobre limites de testes mockados: caminho feliz + schema genérico não substitui validação contra API real.

### Resultados quantitativos Fase 2

Execução completa em 2026-04-19 23:30:39 → 23:36:12:

| Métrica | Valor |
|---|---|
| Total de tasks processadas | 105 |
| Sucesso | 105 (100%) |
| Falhas | 0 |
| Sentinels (áudio vazio) | 0 |
| Custo total USD | 0.2507 |
| Custo médio por áudio | 0.0024 |
| Latência média | 2.89s |
| Duração total do batch | 5min 33s |
| Throttle entre calls | 0.3s |

**Distribuição de `confidence` (derivado de `exp(avg_logprob)`):**

| Faixa | n | % |
|---|---|---|
| <0.3 | 0 | 0% |
| 0.3–0.5 | 10 | 9.5% |
| 0.5–0.7 | 89 | 84.8% |
| 0.7–0.9 | 6 | 5.7% |
| ≥0.9 | 0 | 0% |

Mediana em 0.55. Concentração ~85% na faixa 0.5-0.7. Isso reflete o ground truth linguístico dos áudios: **sotaque mineiro interior, fala informal de canteiro, regionalismos** ("meter o bambu", "tô longe de casa", "cedinho", "cepo"). O modelo `whisper-1` entrega transcrição semanticamente útil mas com logprob modesto porque o vocabulário está fora do centro da distribuição de treino.

**Nenhuma alucinação observada** (tipo *"Obrigado por assistir!"* em silêncio, erro conhecido do Whisper). Zero transcrições vazias. O SNR dos áudios parece alto o suficiente para evitar o modo alucinação.

**Distribuição de tamanho da transcrição (chars):**

| Faixa | n |
|---|---|
| 0 (vazio) | 0 |
| <50 | 7 |
| 50–200 | 45 |
| 200–500 | 27 |
| 500–1000 | 16 |
| ≥1000 | 10 |

Distribuição bimodal esperada: mensagens curtas de resposta/confirmação + áudios longos de descrição de serviço.

### Validação qualitativa — amostra de transcrições reais

Amostras aleatórias da vault demonstram conteúdo semanticamente rico:

> *"Ô gente boa, nós aqui trabalha em dois, leu uma menina aqui, deixou, nós estamos terminando o serviço aqui, aí depois eu vou te ligar pra você ir."* (primeiro áudio transcrito no projeto, task_id=1)

> *"Os caras aqui é doido demais, velho. Eu vou filmar agora e te mandar pra você ver aí. Não sei como é que eu vou fazer com isso aqui não, velho. Eu acho que se tiver ferro sobrando aqui não... Eu pensava até em cortar a rente ali e fazer."* (decisão técnica sobre ferragem)

> *"eu boto mas tá metendo o cepo lá [...] o Julio é o meu engenheiro [...] eu já arranquei, já alinhei ele alinhadinho e tá faltando só uma tesoura só"* (relato de execução de estrutura metálica)

Categorias semânticas espontâneas identificáveis:

- **Cronograma** ("amanhã cedinho tô lá")
- **Decisões técnicas de campo** ("cortar a rente", "passar cadê pra ele")
- **Gestão de subcontratação** ("fazendo um serviço na oficina")
- **Relato de execução** ("já arranquei, já alinhei")
- **Negociação financeira** ("metade agora e metade na hora que entregar")

Isso é RDO em estado bruto. A Sprint 4 (sintetizador) terá matéria-prima abundante.

---

## Parte II — Retrospectiva Fase 3 (VISUAL_ANALYSIS)

### Contexto de entrada

Fase 3 entrou no dia 20/abril com código escrito em 18/abril à noite pelo Claude Code do Mac (commits `230374f` + `a0962db` + `f4310a8`): 601 linhas de `visual_analyzer/__init__.py`, 542 linhas de testes, golden fixture sintético 64×64 capturado. Suite 134/134 verde.

**Diferença de processo em relação à Fase 2:** o código não foi escrito interativamente comigo — apareceu pronto na sessão desta manhã. Decisão consciente: **fazer revisão manual camada-a-camada antes do E2E** (30-45 min), em vez de confiar nos testes mockados.

A revisão identificou 11 achados de severidade variável, dos quais 3 justificaram fix antes do E2E.

### Revisão manual — 11 achados em 5 camadas

Revisão realizada em 2026-04-20 manhã, dividindo o arquivo em 5 camadas lógicas:

1. Topo (imports, constantes, schema esperado) — linhas 1-80
2. Entry point + helpers (system prompt, mime, sentinel builder) — linhas 80-180
3. Chamada da API Vision + retry + logging — linhas 180-320
4. Persistência + handler principal — linhas 320-460
5. Pipeline end-to-end (INSERTs, UPDATEs, commit) — linhas 460-601

Cada camada foi lida criticamente buscando: bugs latentes, comparação com padrão do `transcriber` (ground truth validado no dia anterior), assunções sobre API externa que fixtures sintéticos não capturam.

### Fixes aplicados (commit `ca4b2e4`, 2026-04-20 08:58)

**Fix 1 — `detail="high"` explícito no `image_url`:**

```python
# Antes
{"type": "image_url", "image_url": {"url": image_data_url}},

# Depois
{"type": "image_url", "image_url": {"url": image_data_url, "detail": "high"}},
```

Justificativa: quando `detail` é omitido, a OpenAI usa `"auto"` como default, que escolhe `low` ou `high` baseado em regras internas sujeitas a mudança. Para laudo forense, **custo e tokens reportados devem ser determinísticos por resolução**, não dependentes de heurística do provedor. Também adicionado ao `request_body_for_hash` para que o hash reflita a escolha.

**Fix 2 — `log.warning` quando `response.usage` ausente:**

```python
# Antes
usage = response_dict.get("usage") or {}  # silencia falta de usage

# Depois  
usage = response_dict.get("usage")
if not usage:
    log.warning(
        "Vision response sem campo 'usage'; cost_usd será 0.0 — "
        "auditar api_call subsequente (request_hash=%s)",
        request_hash,
    )
```

Justificativa: o fallback silencioso mascarava um sinal potencialmente importante (mudança de contrato do SDK, filtro de moderação, etc.). Trocado por detecção explícita que não bloqueia o E2E (`cost=0` ainda persiste), mas grita em log.

**Fix 3 — `_validate_schema` checa total de caracteres ≥ 100:**

```python
total_chars = sum(len(str(payload.get(k, ""))) for k in REQUIRED_FIELDS)
if total_chars < 100:
    return False, f"response_too_short:{total_chars}_chars"
```

Justificativa: o system prompt pede "200-2000 caracteres somados", mas a validação anterior só verificava presença e não-vazio dos 4 campos. Um modelo preguiçoso poderia retornar `{"campo1": "ok", "campo2": ".", ...}` — passaria no schema mas seria lixo semântico. Threshold 100 é permissivo (25 chars/campo em média).

**Este fix detectou exatamente 4 casos legítimos no E2E** — ver seção de resultados abaixo. Sem ele, o RDO receberia 4 "análises válidas" vazias.

**Testes após os fixes:** 10/10 `test_visual_analyzer.py`, 134/134 suite. Nenhum mock precisou de ajuste — a fixture golden já trazia respostas realistas com descrições longas.

### Resultados quantitativos Fase 3

Execução em 2026-04-20 09:09:32 → 09:10:28 (56 segundos, + smoke prévio de 10.871s):

| Métrica | Valor |
|---|---|
| Total de tasks processadas | 10 |
| Sucesso (análise válida) | 6 |
| Sucesso (sentinel legítimo) | 4 |
| Falhas | 0 |
| Custo total USD | 0.3024 |
| Custo médio por imagem | 0.0302 |
| Latência média (batch) | 5.95s |
| Latência smoke (img 1.4MP) | 10.87s |
| Duração batch | 56s |

**Correlação resolução × tokens × sentinel:**

| Resolução | Arquivo típico | Tokens input | Custo/img | N imgs | Resultado |
|---|---|---|---|---|---|
| 1599×899 ou 900×1600 (1.4 MP) | 120-194 KB | **37.020** | $0.0058 | 6 | Análise válida |
| 377×1280 ou 367×1280 (0.5 MP) | 45-48 KB | **20.019** | $0.003 | 4 | Sentinel `response_too_short:64_chars` |

**Padrão determinístico:** tokens input são constantes por faixa de resolução, não variam por KB. As imagens 1.4MP consumiram exatamente 37.020 tokens cada; as 0.5MP exatamente 20.019. Isso confirma que o modo `detail="high"` processa em tiles baseado em resolução, mas **a contagem efetiva é ~13x maior do que a fórmula pública sugere** (85 base + 170/tile; para 16 tiles, esperaríamos ~2800 tokens, mas a API cobra ~37k).

### Validação qualitativa — amostra de análises reais

**Exemplo de análise válida (task id 49, imagem 900×1600 de equipamentos em feira):**

> *"Não há atividade de construção em andamento visível na imagem. Os equipamentos parecem estar em uma exposição ou feira, onde estão sendo apresentados para demonstração ou venda. [...] Equipamentos de soldagem da marca Vonder, incluindo um modelo identificado como MM 403 e outro como MM 305. Ambos os equipamentos possuem estrutura metálica, com acabamento em pintura amarela e detalhes em preto."*

**O modelo recusou alucinar atividade de canteiro** mesmo com system prompt de "engenheiro civil analisando canteiro de obra". Identificou contexto real (showroom), fabricante e modelos específicos (Vonder MM 403, MM 305). Isso é defesa anti-alucinação funcionando — a tripla (temperatura 0 + schema JSON + prompt anti-invenção) entregou.

**Exemplo de análise de execução (task id 112, estrutura metálica do telhado):**

> *"Atualmente, a atividade em curso é a montagem da estrutura metálica do telhado. As peças metálicas estão sendo posicionadas e fixadas, e pode haver necessidade de verificação de alinhamento e nivelamento. Não há trabalhadores visíveis na imagem, mas a atividade sugere que a equipe pode estar realizando ajustes ou fixações."*

**Exemplo de identificação de desenho técnico (task id 86):**

> *"O desenho sugere que a atividade em curso pode ser a montagem da estrutura metálica do telhado, incluindo a instalação das terças e do arco. A alvenaria pode estar em fase de finalização ou já concluída. [...] a imagem é um desenho técnico."*

**O modelo distinguiu foto de execução de desenho de projeto.** Esse tipo de discriminação é essencial para o classificador da Sprint 3 — desenhos de projeto vão pro RDO como referência documental, não como relato de execução.

**Elemento recorrente confirmado:** "montagem da estrutura metálica do telhado" aparece em 4 das 6 análises válidas, consistente com a fase construtiva conhecida de Santa Quitéria naquela semana. O Vision captura ground truth verificável.

### Os 4 sentinels — detectados exatamente pelo Fix 3

As 4 imagens que caíram em sentinel são todas do mesmo padrão: resolução 367×1280 ou 377×1280 (portrait esticado 1:3.5), tamanho 45-48 KB, todas do mesmo período temporal. Assinatura digital idêntica ao que o WhatsApp gera como **thumbnail placeholder** quando a foto original foi removida do cache do celular.

Cada uma retornou JSON válido sintaticamente (4 campos presentes, não-vazios) mas com conteúdo mínimo (~64 chars somados). Sem o Fix 3, elas entrariam como "confidence=1.0 análise válida" e contaminariam o RDO.

**Com o Fix 3, todas caíram corretamente em sentinel com `reason=response_too_short:64_chars`.** O sistema identifica exatamente o que são: arquivos que o Vision processou, mas cujo conteúdo é inútil para descrição forense.

Essa taxa (4/10 = 40%) é anômala para a vault total. Provável que seja artefato de teste — os 10 arquivos imagem foram selecionados no teste de ingest original e podem ter desproporcional representação de thumbnails. Em vaults completas reais, a expectativa é 2-5% de thumbnails.

---

## Parte III — Decisões arquiteturais tomadas (Fases 2 e 3)

As decisões documentadas em `SPRINT2_PHASE2_RETROSPECTIVE.md` (numeradas 1-6) continuam válidas. Adicionamos abaixo apenas as decisões novas que surgiram pós-E2E ou na Fase 3.

### 7. Rename de `.opus` para `.ogg` no upload (Fase 2, pós-E2E)

Ver seção "Bug descoberto" acima. Decisão: rename é **artefato de upload**, não de disco; `request_hash` continua fiel ao nome original. Dívida técnica anotada: mimetype `"audio/ogg"` hardcoded aplica-se também a formatos não-opus (irrelevante para WhatsApp, latente para vaults de outras origens).

### 8. `detail="high"` explícito (Fase 3)

Trade-off: custo ~6x maior vs modo `"low"`, mas resolução alta necessária para identificar elementos construtivos (tipo de tijolo, ferragem, não-conformidades visuais). Em modo `low`, a imagem é reduzida para 512×512 máximo — perde detalhe crítico de laudo.

Validado empiricamente: a análise das máquinas Vonder identificou modelos específicos ("MM 403, MM 305") e detalhes de acabamento ("pintura amarela, rodízios visíveis") que exigem resolução preservada.

**Custo real consolidado:** $0.03/imagem média para `detail="high"`. Extrapolação para vault real de 500 imagens: ~$15/obra. Para 30 obras em 6 meses: ~$450. Absorvível.

### 9. `_validate_schema` verifica total de caracteres mínimo (Fase 3)

Padrão: quando um validador é "presença + não-vazio", está aberto a bypass semântico (um campo com "." passa). Para prompts que pedem riqueza descritiva, a validação deve incluir **critério de volume**. Threshold 100 chars é permissivo mas filtra preguiça extrema.

Esse padrão deve ser replicado em Sprint 3/4 se houver handlers que peçam descrições longas.

### 10. Scripts `run_phase*_e2e.py` como ferramentas descartáveis

Decisão consciente: **não investir em CLI definitivo agora** (Fase 4 do Sprint 2 está planejada para isso). Os scripts `run_phase2_e2e.py` e `run_phase3_e2e.py` são idênticos em 90% do código; o Phase 3 foi gerado do Phase 2 via 13 substituições sed. Ambos foram deletados no commit `b97d6d5` após o `rdo-agent process` CLI ser implementado em `ba4e255`.

Vantagem dessa decisão: E2E foi validado em 2 dias em vez de 1 semana. Custo: ~500 linhas de código descartável rastreável em commits.

### 11. Revisão manual prévia ao E2E (Fase 3)

Decisão de processo, não de código. Após o bug `.opus` da Fase 2 (ontem), decidi fazer revisão manual linha-por-linha do código Phase 3 antes do E2E, em vez de confiar nos 134 testes passando.

A revisão identificou 3 fixes válidos, 1 dos quais detectou anomalias reais no E2E (Fix 3 → 4 sentinels legítimos). Sem revisão manual, esses 4 casos entrariam como "análises válidas" com 64 chars de lixo.

**Este padrão deve ser institucionalizado para Sprint 3+:** qualquer código gerado por LLM em grande volume (>200 linhas) passa por revisão manual camada-a-camada antes do primeiro E2E contra dados reais.

---

## Parte IV — Dívidas técnicas (numeradas)

Dívidas descobertas durante revisão manual + E2E. Numeração contínua a partir da retrospectiva preliminar Fase 2.

| # | Origem | Severidade | Descrição | Ação sugerida |
|---|---|---|---|---|
| 1 | Fase 2, E2E | Baixa | Mimetype `"audio/ogg"` hardcoded no fix `.opus` (aceitável para WhatsApp, latente para outros formatos) | Anotar; corrigir se aparecer `.wav`/`.m4a` |
| 2 | Fase 3, revisão | Baixa | `_guess_mime_type` fallback silencioso para `image/jpeg` quando extensão desconhecida | Anotar; converter para rejeição explícita se `.heic`/`.raw` aparecer |
| 3 | Fase 3, revisão | Muito baixa | `_encode_image_data_url` carrega arquivo inteiro em memória (ok <500KB, limite ~20MB) | Considerar streaming em Sprint 5+ |
| 4 | Fase 3, revisão | Baixa | `request_json` não inclui hash do `system_prompt` | Adicionar se for alterar prompt |
| 5 | Fase 3, revisão | Cosmético | `assert` antes de `raise` no fim do retry loop | Substituir por `raise RuntimeError` |
| 6 | Fase 3, E2E | **Média** | Custo real Vision `detail="high"` é ~15x estimativa inicial ($0.03/img vs $0.002) | Documentado aqui; considerar estratégias de mitigação em Sprint 3 |
| 7 | Fase 3, revisão | Cosmético | `api_call_id` não logado no warning quando `usage` ausente | Acompanha #6 |
| 8 | Fase 3, E2E | Baixa | 4/10 thumbnails placeholder no teste — proporção provavelmente anômala | Validar em vault completa real |
| 9 | Fase 2, revisão | Baixa | Fixture Whisper sintético não testou `.opus` — lacuna descoberta só no E2E | Adicionar fixture real `.opus` quando possível |
| 10 | Fase 3, revisão | Baixa | `semantic_status='analyzed'` é genérico demais (coexiste com `transcribed`) | Renomear para `vision_analyzed` em refactor futuro |

### Sobre a dívida #6 — custo real da Vision API

Esta é a descoberta mais importante do E2E. Detalhe:

- **Minha estimativa inicial:** $0.002/imagem (baseado em fórmula documentada "85 + 170×tiles")
- **Custo real em modo `high`:** $0.006/img para 1.4 MP (37.020 tokens), $0.003/img para 0.5 MP (20.019 tokens)
- **Divergência:** ~3-15x maior que estimativa, dependendo de resolução
- **Causa provável:** a API cobra mais do que a fórmula pública sugere, possivelmente incluindo overhead do data URL inline

**Estratégias de mitigação a considerar em Sprint 3:**

- **Pre-filtro heurístico por resolução:** pular imagens <400px largura (provável thumbnail); economiza ~40% em vaults com muitas thumbnails
- **Downscale local com Pillow antes do upload:** redimensionar para 1024×1024 máximo economiza ~80% de tokens mantendo qualidade suficiente para análise estrutural
- **A/B test `high` vs `low`:** validar empiricamente se `low` entrega análise suficiente para elementos construtivos básicos; custo ~6x menor

Nenhuma dessas é bloqueador para Sprint 3, mas devem ser avaliadas antes de processar vault completa de produção.

---

## Parte V — Lições para Sprints 3+

Meta-lições sobre processo, não sobre código. Cada uma deve ser levada explicitamente para o Sprint 3.

### Lição 1 — Testes mockados não substituem E2E contra API real

**Evidência:** 10/10 test_transcriber.py passaram durante 24h mascarando o bug `.opus`. O teste validava que o handler chamava `client.audio.transcriptions.create()` com argumentos estruturalmente corretos, mas nunca validou que a API real aceita esses argumentos.

**Regra para Sprint 3+:** toda integração com API externa tem **dois níveis de teste**:

1. Unitário mockado (caminho feliz + erros + retry) — garante lógica interna
2. Smoke E2E com 1 input real (mesmo custando centavos) — garante contrato real com API

O smoke real deve ser parte do fechamento de fase, não ser adiado para "depois". Custo de fazer smoke depois: descobrir bug em produção com 105 tasks na fila.

### Lição 2 — Revisão manual prévia ao E2E é muito mais barata que debug pós-E2E

**Evidência:** 30-45 min de revisão manual do `visual_analyzer` descobriu 3 fixes úteis. Se fossem encontrados só no E2E:

- Fix 1 (`detail="high"`) não seria detectado — custo imprevisível seria registrado como ground truth
- Fix 2 (`usage` warning) não seria detectado — falhas silenciosas só apareceriam em auditoria posterior
- Fix 3 (`min chars`) quebraria 4 análises em produção; debug + re-run ~30 min + custo duplicado

**Regra para Sprint 3+:** código gerado por LLM com >200 linhas (próprio ou Claude Code) passa por revisão manual camada-a-camada antes do E2E. O padrão de 5 camadas (topo → handler → API call → persistência → pipeline) funciona.

### Lição 3 — Distinguir "falha" de "lixo legítimo" com sentinels

**Evidência:** Fase 3 teve 4 sentinels. Se o sistema tratasse sentinel como falha, teríamos "40% de failure rate" alarmista. Mas os 4 casos eram **dados legítimos de baixa qualidade** (thumbnails placeholder), não erros do sistema.

**Princípio:** em pipelines forenses, três categorias coexistem:

1. **Dado válido:** conteúdo útil processado corretamente
2. **Lixo legítimo (sentinel):** input processável mas de qualidade inadequada — documentado como tal
3. **Falha (failed):** erro do sistema, retry não resolveu

As métricas de sucesso devem separar os três. Um dashboard que agrega "sentinel + failed" esconde sinal importante. A tabela de resumo do `run_phase*_e2e.py` já faz isso corretamente (`done` / `sentinel` / `failed` separados).

### Lição 4 — Estimativas de custo de APIs multimodais são não-confiáveis sem dados

**Evidência:** estimativa inicial $0.002/img Vision vs realidade $0.03/img. Erro de 15x em cima do tipo de medida mais importante para planejamento de produto.

**Regra para Sprint 3+:** antes de qualquer planejamento de custo de produção, **fazer pelo menos 1 call real e capturar métricas reais** (tokens_input, tokens_output, cost_usd, latency). Não extrapolar de documentação teórica.

### Lição 5 — Imagens off-topic são realidade em WhatsApp de canteiro

**Evidência:** das 6 análises válidas na Fase 3, 1 era de máquinas em feira (não canteiro) e 1 era desenho técnico (não execução). 2/6 = 33%. Vaults reais têm essa proporção de ruído semântico.

**Implicação para Sprint 3 (classificador):** o classificador não pode assumir que toda imagem é de canteiro ativo. Precisa de etapa explícita de triagem: "isto é foto-de-canteiro-em-execução, foto-de-material-off-topic, desenho-de-projeto, documento-fotografado, screenshot, thumbnail?"

**Boa notícia:** o Vision já entrega sinal para essa triagem. A análise da máquina Vonder disse "exposição ou feira"; a análise do desenho disse "imagem é um desenho técnico". Um classificador simples pode filtrar por essas frases.

---

## Parte VI — Comparativo Fase 2 vs Fase 3

Dados lado-a-lado para comparação empírica:

| Dimensão | Fase 2 (TRANSCRIBE) | Fase 3 (VISUAL_ANALYSIS) |
|---|---|---|
| Modelo | `whisper-1` | `gpt-4o-mini` |
| Tasks processadas | 105 | 10 |
| Sucesso | 105 (100%) | 6 válidas + 4 sentinels (100% processadas) |
| Falhas | 0 | 0 |
| Custo total | $0.2507 | $0.3024 |
| Custo por task | $0.0024 | $0.0302 |
| Latência média | 2.89s | 5.95s |
| Duração batch | 5min 33s | 56s |
| Input típico | Áudio Opus 14-50KB | Imagem JPEG 45-200KB |
| Output típico | Texto 50-1000 chars | JSON 800-1500 chars |
| Alucinações observadas | 0 | 0 |
| Sentinels legítimos | 0 | 4 (thumbnails placeholders) |
| Bugs descobertos no E2E | 1 (`.opus`) | 0 novos (3 fixes preventivos via revisão) |

**Observações cruzadas:**

- **Vision é ~12x mais caro por task** que Whisper ($0.030 vs $0.0024). Em vault típica com 500 áudios + 50 imagens, Whisper domina volume mas Vision domina custo (~60/40).
- **Vision é mais lento por task** (2x) mas **tem menos tasks** então o batch total é mais rápido.
- **Ambos têm alucinação = 0** — as defesas de temperatura 0 + sentinel pattern + schema JSON (Vision) funcionaram.
- **Ambos tiveram bugs do tipo "contrato real da API difere da documentação/expectativa"** — o `.opus` da Fase 2 e o custo 15x da Fase 3. Padrão de que APIs externas precisam de validação empírica.

---

## Estado do Sprint 2 e recomendações

### Estado atual

- **Fase 1 (EXTRACT_DOCUMENT):** completa (Sprint 1)
- **Fase 2 (TRANSCRIBE):** ✅ validada E2E — 105/105 sucesso
- **Fase 3 (VISUAL_ANALYSIS):** ✅ validada E2E — 10/10 sucesso
- **Fase 4 (CLI `rdo-agent process` + `rdo-agent status`):** ✅ concluída (commit `ba4e255`, 2026-04-20 12:54 UTC). Scripts ad-hoc removidos em `b97d6d5`.

**Suite:** 134/134 verde local.  
**Commits últimos 2 dias:** 4 (b5aecf8, 38c481b, ca4b2e4, 771311a).  
**Gasto total validação:** US$ 0.56.

### Retrospectiva do fechamento

Fase 4 foi iniciada em 2026-04-20 manhã logo após a retrospectiva das Fases 2 e 3 ser escrita. O escopo permaneceu contido (apenas `process` + `status`), conforme plano original, e o `run_worker` existente do orchestrator foi preservado sem modificações — a CLI é apenas um wrapper com progress bar, filtros, SIGINT graceful e registro dos handlers no dict `HANDLERS`.

Descobertas durante a Fase 4:

1. **`next_pending` já resolvia `depends_on` desde Sprint 1** — a query com `json_each(depends_on)` + `NOT EXISTS` era elegante e completa. Zero trabalho adicional em graph de dependências.
2. **Decisão arquitetural corrigida pelo Claude Code:** meu prompt sugeria filtrar `task_type` no consumidor, mas o Claude Code identificou que SQLite sem `SKIP LOCKED` causaria re-fetch infinito — correto filtrar na query (`_fetch_next_eligible`).
3. **Idempotência validada em produção:** o smoke da task 1 via CLI retornou o mesmo `result_ref=f_22e21dda92e2` do smoke de 19/abril, provando que o SHA256-based file_id do handler funciona corretamente em runs repetidos.

### Sprint 2 — encerrado

**Commits do Sprint 2 (8 no total):**

- `ba4e255` feat(cli): comando 'process' + 'status' (Fase 4)
- `b97d6d5` chore: remove scripts E2E ad-hoc
- `db0cab8` docs: retrospectiva consolidada Fases 2+3
- `771311a` feat: run_phase3_e2e.py (removido em b97d6d5)
- `ca4b2e4` fix: 3 defesas anti-alucinação Vision
- `38c481b` feat: run_phase2_e2e.py (removido em b97d6d5)
- `b5aecf8` fix: rename .opus → .ogg Whisper
- (+ commits anteriores de 18/abril fora do escopo de E2E)

**Próximo marco:** Sprint 3 (classificador semântico). Plano em `docs/SPRINT3_PLAN.md` (a escrever).

## Anexo A — Commits relevantes

| Hash | Data | Descrição |
|---|---|---|
| `230374f` | 2026-04-18 23:17 | feat(visual_analyzer): handler GPT-4o-mini com retry, sentinel JSON e logging granular |
| `a0962db` | 2026-04-18 23:17 | test(visual_analyzer): 10 testes mockados |
| `f4310a8` | 2026-04-19 20:44 | feat(fixtures): captura golden fixture Vision (imagem sintética 64x64) |
| `b5aecf8` | 2026-04-19 23:38 | fix(transcriber): renomear .opus → .ogg no upload para Whisper |
| `38c481b` | 2026-04-19 23:38 | feat(scripts): run_phase2_e2e.py — runner E2E da Fase 2 |
| `ca4b2e4` | 2026-04-20 08:58 | fix(visual_analyzer): 3 defesas anti-alucinação forense (review pré-E2E) |
| `771311a` | 2026-04-20 09:16 | feat(scripts): run_phase3_e2e.py — runner E2E da Fase 3 |

## Anexo B — Comandos de reprodução

Para reexecutar E2E completo em nova vault (supondo ingest já feito):

```bash
cd ~/projetos/rdo-agent
source .venv/bin/activate

# Ver estado da obra antes de processar
rdo-agent status --obra NOME_OBRA

# Dry-run pra prever o que será processado
rdo-agent process --obra NOME_OBRA --dry-run

# Processa todos os tipos de task pendentes
rdo-agent process --obra NOME_OBRA

# Ou filtrado por tipo específico
rdo-agent process --obra NOME_OBRA --task-type transcribe --throttle 0.3
```

O estado forense é auditável diretamente via `rdo-agent status --obra NOME_OBRA` e por queries SQL contra `tasks` e `api_calls` no `index.sqlite`. Logs de texto são dispensáveis.

---

*Documento escrito em 2026-04-20 manhã, após E2E validado de ambas as fases. Autor: Lucas Ferreira Leite com auxílio de Claude (Anthropic).*
---

## Adendo 2026-04-20 tarde — Revisão empírica da afirmação "zero alucinações"

**Este adendo foi adicionado em 2026-04-20 entre 13:00 e 15:00 BRT, durante transição Sprint 2 → Sprint 3. O corpo original desta retrospectiva é preservado intacto acima como registro histórico de como os fatos foram percebidos no momento de fechamento do Sprint 2. Este adendo documenta o que foi descoberto depois, não corrige o texto anterior.**

### Contexto do adendo

Ao iniciar calibração de categorias da Sprint 3 (exercício de classificação manual de 30 transcrições reais da vault EVERALDO_SANTAQUITERIA), Lucas identificou que a primeira transcrição da amostra era semanticamente muito distante do áudio real:

- **Whisper entregou:** *"Aí, no caso, o barro se fechamenta e tem com a tela, né? Ou a tela é por conta do 6A."*
- **Áudio real:** *"Aí no caso por baixo do fechamento tem que por uma tela, né? Ou a tela é por conta do cês lá?"*

Três erros graves em uma frase curta: "por baixo do fechamento" virou a palavra inexistente "barro se fechamenta"; "tem que por uma tela" (ação) virou "tem com a tela" (descrição); "cês lá" (vocês lá, coloquial mineiro) virou "6A" (parecido com especificação técnica plausível).

Essa observação motivou um spike empírico de comparação de modelos (registrado em `docs/ADR-001-transcription-model-selection.md`).

### O que o spike revelou

**Word Error Rate (WER) médio do baseline atual** (`whisper-1` sem prompt) em amostra de 5 áudios diversos: **46.4%**. Distribuição:

| Áudio | Duração | WER |
|-------|---------|-----|
| Curta (28 chars) | ~5s | 0.0% |
| Longa (2061 chars) | 129s | 52.6% |
| Alta confidence (1109 chars) | 50s | 51.9% |
| Mediana (212 chars) | 22s | 47.5% |
| Baixa confidence (116 chars) | 17s | 80.0% |

A métrica `confidence` reportada pelo Whisper (média 0.55 no dataset, tida como "aceitável para sotaque mineiro") **não correlaciona com WER real**. O áudio #3 tem confidence alta (0.874) e WER 51.9%. O áudio #5 tem confidence baixa (0.405) e WER 80%. Mas o confidence mede certeza **do modelo sobre sua própria transcrição**, não fidelidade ao áudio — conforme documentado em literatura do Whisper. A Sprint 2 interpretou confidence como proxy de qualidade; **essa interpretação estava errada**.

### Revisão da afirmação "0 alucinações observadas"

A Parte I desta retrospectiva afirma:
> *Nenhuma alucinação observada (tipo "Obrigado por assistir!" em silêncio, erro conhecido do Whisper). Zero transcrições vazias.*

Essa afirmação é **falsa no critério forense mais rigoroso**, mas foi **correta no critério observado em 19-20/abril** (ausência dos padrões de alucinação conhecidos em voz humana). O que o spike revelou:

1. **Inversão de sujeitos.** Áudio #2: *"eu tomo umas cacetada aqui também"* (1ª pessoa) virou *"ele tem que comprar carro zero"* (3ª pessoa). Sujeito inventado, informação original perdida.

2. **Palavras obscenas inventadas.** Áudio #2: *"umas fotos de umas notinhas"* virou *"umas foda notinha"*. Whisper alucinou vocabulário sexual onde o áudio era mundano.

3. **Frases inteiras fabricadas.** Áudio #2: depois de *"eu vou jogar aquelas tesouras pra cima"*, o áudio continua *"cê num sabe se aquele trem tá conferindo"*. Whisper produziu: *"você quer tentar tá conferindo"*. "Quer tentar" é ficção.

4. **Omissão de informação crítica.** Áudio #5: final do áudio contém *"eu tenho umas 5 máquinas de solda"*. Whisper omitiu completamente essa sentença, substituindo por *"ou tem que ter uma super mais forte"* (invenção).

5. **Loop catastrófico em fala com gaguejo.** Áudio #3: locutor gaguejou *"nao, nu...nu...num tem problema"*. Whisper interpretou o padrão como repetição infinita e produziu ~250 instâncias da palavra "não" consecutivas, ocupando 1097 chars do output. Os ~45s restantes do áudio **foram transcritos como parte da repetição, não como seu conteúdo real**. Para o pipeline downstream, esse áudio é texto 100% inútil.

6. **Perda sistemática do registro coloquial mineiro.** Whisper converte `cê → você`, `sô → só`, `num → não`, `tô → estou`, `tá → está`, `ocê → você`. Isso muda o **registro semântico** da fala: *"cê num ta errado não"* (tom conciliador) vira *"você tá errado"* (tom acusatório) — inversão de polaridade.

7. **Perda de termos técnicos específicos.** Áudio #4: `ripa` (vergalhão de madeira) virou `repa`. Áudio #5: `MIG` (máquina de solda MIG/MAG) virou `amiga`. Erros de 1-2 fonemas em palavras-chave de engenharia.

### Consequência interpretativa

A Parte V lição 3 desta retrospectiva estabelece:
> *Distinguir "falha" de "lixo legítimo" com sentinels. Em pipelines forenses, três categorias coexistem: dado válido, lixo legítimo (sentinel), falha.*

O spike adiciona uma **quarta categoria não detectada em Sprint 2**:
> **Dado aparentemente válido mas semanticamente distorcido.** Task `done`, `duration` correta, `confidence` dentro da faixa, texto não-vazio, sem repetições textuais óbvias — mas conteúdo informacional perdido ou invertido.

Essa categoria é a mais perigosa porque **não há sinal interno do pipeline que a detecte**. Requer comparação com ground truth externo (áudio original + escuta humana) para ser identificada.

### Decisão de não-retrabalho

Os testes do spike (3 configurações × 5 áudios = 15 chamadas de API, custo ~$0.15) demonstraram que **nenhuma alternativa disponível via OpenAI API reduz WER abaixo de 46.4% do baseline atual**:

- `gpt-4o-transcribe` sem prompt: WER médio 55.2% (pior que baseline)
- `whisper-1` + prompt contextual mineiro: WER médio 53.6% (pior que baseline, com caso catastrófico de 94.4% no áudio longo devido a deleção agressiva induzida pelo prompt)

Detalhes técnicos completos em `docs/ADR-001-transcription-model-selection.md`.

Dado o prazo de Sprint 3 (RDO piloto em 14 dias desde 2026-04-20), **reprocessar a vault com alternativa inferior seria desperdício** ($0.50+ de custo, output pior que o atual). Decisão registrada no ADR: **manter whisper-1 sem prompt e pivotar design da Sprint 3** para incluir detector de qualidade de transcrição + pipeline de revisão humana por amostragem.

### Implicações para Sprints futuras

**Sprint 3 (imediato):** arquitetura em 4 camadas, não mais apenas "classificador":
1. Detector de qualidade de transcrição (gpt-4o-mini lê texto, flag: coerente | suspeito | ilegível)
2. Interface de revisão humana (usuário ouve áudio + corrige texto flagged)
3. Classificador semântico (gpt-4o-mini) sobre texto revisado
4. Output em tabela dedicada (ADR-002 a ser escrita)

**Sprint 4 (agente-engenheiro Claude):** classificações levam tag de proveniência (revisada | aceita como entregue pelo Whisper). Claude ajusta confiança narrativa de acordo.

**Sprint 5+ (hardening / piloto):** explorar Whisper Large-v3 self-hosted em GPU local ou fine-tuning da OpenAI se escala do projeto justificar. Não priorizado no MVP.

### Honestidade metodológica

A retrospectiva original foi escrita de boa-fé em 2026-04-20 manhã, com base nos dados então disponíveis. O erro de interpretação ("confidence como proxy de qualidade", "ausência de padrões conhecidos de alucinação como evidência de fidelidade") é comum em desenvolvimento de pipelines de ML, e especificamente em pipelines de transcrição onde ground truth não é trivial de estabelecer.

**A lição metodológica extraída:** antes de declarar fase "completa" em pipeline com API externa estocástica, validar conteúdo contra ground truth humano em amostra ≥ 5 itens diversos. Custo da validação tardia (este adendo + pivô da Sprint 3) foi ~4h de trabalho e ~$0.15 em API — prejuízo aceitável. Custo caso a descoberta tivesse vindo depois da Sprint 3 concluída: potencialmente 3-5 dias de refatoração + perda de credibilidade do MVP com fiscal SEE-MG.

Este adendo fecha o Sprint 2 com precisão maior — não invalidando o que foi feito, mas reconhecendo explicitamente o que não foi medido na hora.

*Registro: 2026-04-20 tarde, durante transição Sprint 2 → Sprint 3. Autor: Lucas Ferreira Leite com análise de Claude / Anthropic.*
