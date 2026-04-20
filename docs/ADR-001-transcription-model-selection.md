# ADR-001 — Seleção do Motor de Transcrição (Camada 2)

- **Data:** 2026-04-20
- **Status:** Aceita
- **Autor:** Lucas Ferreira Leite (com análise de Claude / Anthropic)
- **Sprint:** Transição Sprint 2 → Sprint 3
- **Contexto-âncora:** `docs/SPRINT2_PHASES_2_3_E2E_RETROSPECTIVE.md`

## Contexto

O Sprint 2 foi fechado em 2026-04-20 com a afirmação forense de que *"105/105 áudios transcritos com 0 falhas, 0 alucinações"*. Essa afirmação baseou-se em métricas de processo (tasks `done`, `duration` do Whisper batendo com `ffprobe`, `confidence` médio 0.55 dentro da faixa esperada para sotaque mineiro), **não em validação humana do conteúdo transcrito**.

Na abertura do Sprint 3, durante calibração de categorias de classificação, o usuário identificou que a transcrição Whisper #1 da amostra (*"Aí, no caso, o barro se fechamenta e tem com a tela, né? Ou a tela é por conta do 6A"*) era **semanticamente muito distante** do áudio real (*"Aí no caso por baixo do fechamento tem que por uma tela, né? Ou a tela é por conta do cês lá?"*).

Essa descoberta motivou um spike empírico para avaliar se trocar de modelo ou adicionar prompt contextual resolveria o problema antes de reprocessar toda a vault EVERALDO_SANTAQUITERIA e antes de iniciar desenvolvimento do classificador da Sprint 3.

## Questão

O baseline atual (`whisper-1` sem prompt) é o melhor motor disponível para transcrição de áudios informais de canteiro mineiro, ou existe configuração que reduza Word Error Rate (WER) de forma custo-efetiva antes da Sprint 3 começar?

## Configurações avaliadas

| ID  | Modelo                | Prompt contextual | Custo/min | Observação                                    |
|-----|-----------------------|-------------------|-----------|-----------------------------------------------|
| A   | `whisper-1`           | não               | $0.006    | Baseline atual em produção                    |
| B   | `whisper-1`           | sim (~220 tokens) | $0.006    | Prompt com vocabulário mineiro + construção   |
| C   | `gpt-4o-transcribe`   | n/a (não suporta) | ~$0.006   | Modelo novo geração GPT-4o                    |

**Limitação técnica descoberta no spike:** `gpt-4o-transcribe` e `gpt-4o-mini-transcribe` **não suportam parâmetro `prompt`** — a API dessa família aceita apenas `model/file/language/response_format/temperature`. Configuração `D` (gpt-4o-transcribe + prompt) foi descartada como não viável.

## Metodologia

Amostra de 5 áudios selecionados deterministicamente por critério diversificado:
1. **Curta** (<100 chars transcrição baseline)
2. **Longa** (>500 chars transcrição baseline)
3. **Alta confidence** (>0.8)
4. **Mediana** (próximo à mediana do pool: ~206 chars, ~0.59 confidence)
5. **Menor confidence disponível** (<0.5)

**Ground truth** estabelecido pelo usuário via escuta integral de cada áudio e transcrição manual fiel (gírias, hesitações, dialeto mineiro preservados).

**Métrica primária:** Word Error Rate (WER) via `jiwer==4.0.0`, normalização leve (lowercase, remoção de pontuação, colapso de espaços).

**Métrica secundária:** latência (ms) e breakdown de erros (substitutions/deletions/insertions/hits).

**Ferramentas:** script Python dedicado (`/tmp/transcriber_spike/`) com logging estruturado, resultado em JSON + tabelas ASCII comparativas.

**Custo total do spike:** ~$0.15 (15 chamadas de API: 5 áudios × 3 configs).

## Resultados

### Tabela agregada de WER

| Áudio | Critério | A (whisper-1) | B (whisper-1 + prompt) | C (gpt-4o-transcribe) |
|-------|----------|---------------|------------------------|------------------------|
| #1    | curta    | 0.0%          | 0.0%                   | 40.0%                  |
| #2    | longa    | 52.6%         | **94.4%**              | 45.7%                  |
| #3    | alta_conf | 51.9%        | 50.0%                  | 51.0%                  |
| #4    | mediana  | 47.5%         | 47.5%                  | 47.5%                  |
| #5    | menor_conf | 80.0%       | 76.0%                  | 92.0%                  |
| **MÉDIA** |     | **46.4%**     | 53.6%                  | 55.2%                  |

### Descobertas qualitativas críticas

**D1 — Prompt contextual causa deleção agressiva em áudios longos.**
No áudio #2 (466 palavras ground truth), a config B deletou 285 palavras (61% do áudio), mantendo apenas 27 hits. Hipótese: o prompt contém vocabulário técnico ("tesoura, terça, cepo, ripa..."), o que condicionou o decoder a favorecer saídas com esse vocabulário e **descartar narrativa "off-topic"** (reclamação, negociação, contexto emocional). Efeito documentado em literatura do Whisper mas não conhecido pelos autores antes do spike.

**D2 — `gpt-4o-transcribe` alucina em áudios curtos e termos técnicos isolados.**
- Áudio #1: "Não, Lucas" virou "Não, Lucca, tá tudo ondulado" (2 substituições em 5 palavras = 40% WER em áudio que A e B transcreveram perfeitamente)
- Áudio #5: "uma MIG dessa novinha" (máquina de solda MIG) virou "amígdalas novas"
- Modelo novo não é necessariamente melhor para domínio específico

**D3 — Whisper entrou em loop patológico em áudio com gaguejo natural.**
Áudio #3 tem locutor gaguejando ("nao, nu...nu...num"). Whisper capturou literalmente como loop de "não" repetido 250+ vezes, **sobrescrevendo o conteúdo real dos ~45 segundos restantes**. Todas as 3 configurações apresentaram esse loop em grau similar — é padrão sistêmico do modelo Whisper, não configuração específica. Duration reportada: 50.3s; cobertura temporal 100%; mas output textual é lixo.

**D4 — Erros são consistentes entre configurações em áudios "médios".**
Áudios #3 e #4 tiveram WER praticamente idêntico nas 3 configs (~47-52%). Sugere que erros comuns (ô → o luxo, cê → você, ripa → repa, mensagem → mensa) são característicos de como modelos Whisper-family interpretam sotaque mineiro — **resiliente a troca de modelo entre variantes OpenAI**.

**D5 — Bug `.opus` documentado na Fase 2 continua válido.**
Whisper API rejeita arquivos com extensão `.opus` mesmo sendo container OGG/Opus. Fix atual (rename pra `.ogg` no upload, preservando nome real no request_hash) funcionou em todas as 15 chamadas do spike. `gpt-4o-transcribe` aceita o mesmo workaround.

### Contra-evidência: percepção inicial de "truncamento"

O usuário reportou que *"áudios maiores ficaram quase 80% cortado"*. Diagnóstico via `ffprobe` em todos os 105 áudios confirmou **cobertura temporal 100%** em todos os casos. A percepção correspondia, na verdade, ao áudio #3 com seu loop de "não" — 100% do áudio foi processado, mas o output textual continha 80% de ruído. **Truncamento técnico não existe; perda semântica por falha de modelo existe.**

### Afirmação "zero alucinações" da retrospectiva Sprint 2 revisada

A retro consolidada afirma *"0 alucinações observadas"* com base em inspeção de amostras curtas. O ground truth dos 5 áudios do spike revela:
- Sujeitos invertidos (#2: "eu tomo cacetada" → "ele tem que comprar carro zero")
- Palavras obscenas inventadas (#2: "umas notinhas" → "umas foda notinha")
- Frases inteiras inventadas (#2: "eu vou jogar você não sabe você quer tentar tá conferindo")
- Omissão de informação crítica (#5: "eu tenho umas 5 máquinas de solda" — simplesmente ausente)
- Loop catastrófico (#3)

**A afirmação de "0 alucinações" estava errada, mas não era má-fé:** era resultado de não ter ground truth validado por humano em 2026-04-19/20. Adendo separado na retrospectiva original registra essa correção (§ "Adendo 2026-04-20 tarde").

## Decisão

**Manter `whisper-1` sem prompt como motor de transcrição da Camada 2.**

Corolários:
1. **Não reprocessar as 105 transcrições existentes.** Nenhuma configuração alternativa oferece melhora líquida; reprocessar com B ou C resultaria em qualidade igual ou pior.
2. **Não implementar Camada 1 de vocabulário global (hardcoded no prompt).** A proposta inicial (incluir prompt com 200 tokens de vocabulário mineiro + construção) foi invalidada empiricamente. Tabela `obra_vocabulary` (Camada 2 da proposta original) também fica em backlog indefinido até surgir evidência de necessidade.
3. **Reconhecer 46% WER baseline como premissa de entrada da Sprint 3.** Não é meta a atingir; é realidade com a qual o classificador e o agente-engenheiro terão que lidar.

## Consequências

### Para Sprint 3 (imediato)

- **Pivô obrigatório:** classificador automático operando sobre texto com 46% WER geraria classificações não-confiáveis. Sprint 3 ganha novo componente: **detector de qualidade de transcrição + pipeline de revisão humana por amostragem**.
- **Nova arquitetura em 4 camadas:**
  1. Detector de qualidade (gpt-4o-mini lê transcrição, flag: "coerente" | "suspeita" | "ilegível")
  2. Interface de revisão humana (CLI ou web; usuário ouve áudio original + corrige texto)
  3. Classificador (roda em texto revisado)
  4. Output em tabela `classifications` (decisão da ADR-002 a ser escrita)
- **Custo humano adicional estimado:** 3-4h de revisão manual distribuídas ao longo do sprint (~30-40% das 105 transcrições sob flag de revisão).

### Para Sprint 4 e posteriores

- **Agente-engenheiro (Claude) receberá classificações com tag de proveniência:** "transcrição revisada por humano" vs "transcrição aceita como Whisper entregou". Isso permite que Claude ajuste confiança na narrativa final do RDO.
- **RDO piloto inicial terá marcadores `[REVISADO]` / `[NÃO REVISADO]`** nos eventos derivados de cada área. Aceitável como MVP; refinável em Sprint 5.

### Para arquitetura longo-prazo

- **Evidência acumulada sobre limites de APIs de transcrição para domínio forense regional.** Registro deste ADR permite revisitar a questão com evidência (não com hipótese) se surgir:
  - Whisper Large-v3 self-hosted em GPU local
  - Fine-tuning de Whisper com áudios Vale Nobre (~10h gravações + $30/h treino)
  - Modelos de transcrição especializados em português BR (AssemblyAI, Deepgram Nova)
- **Nenhuma dessas alternativas é viável em cronograma de MVP atual** (14 dias pra RDO piloto).

### Anti-consequências (o que este ADR NÃO bloqueia)

- Adição futura de pós-processamento LLM pra correção de termos técnicos isolados (ex: "amígdalas" → "MIG" quando contexto é soldagem). Não testado aqui; hipoteticamente viável em Sprint 5.
- Retomada de prompt contextual **se** confirmado empiricamente que não causa deleção em áudios curtos (onde o problema D1 não se manifesta). Não priorizado.
- Troca de modelo quando Anthropic ou OpenAI lançarem motor comprovadamente melhor em PT-BR regional.

## Alternativas consideradas e rejeitadas

### Opção Y.1 — Só trocar modelo (gpt-4o-transcribe sem prompt)

Rejeitada. Evidência direta: WER 55.2% > 46.4% do baseline.

### Opção Y.2 — Só adicionar prompt ao whisper-1

Rejeitada. Evidência direta: WER 53.6% > 46.4% do baseline. Caso específico catastrófico no áudio #2 (WER 94.4%) inviabiliza uso mesmo em configuração específica.

### Opção Y.3 — Combinar modelo novo + prompt

Não implementável (D1: `gpt-4o-transcribe` não suporta parâmetro `prompt`).

### Opção Y.5 — Vocabulário customizado por obra (tabela `obra_vocabulary`)

Não implementada. Com prompt contextual global invalidado, a camada por-obra perde base técnica (não há mecanismo API pra injeção de vocabulário no modelo novo). Retoma-se a ideia se/quando Camada 2 mudar.

## Referências

### Código e dados

- **Script do spike:** rodado one-shot (não versionado no repo) — reproduzível via ADR
- **Seleção dos áudios:** `~/rdo_vaults/EVERALDO_SANTAQUITERIA/99_logs/spike_Y4_transcriber/selection.json`
- **Resultados numéricos completos:** `~/rdo_vaults/EVERALDO_SANTAQUITERIA/99_logs/spike_Y4_transcriber/results.json`
- **Ground truth (transcrição humana):** `~/rdo_vaults/EVERALDO_SANTAQUITERIA/99_logs/spike_Y4_transcriber/GROUND_TRUTH.md`

### Commits-âncora

- `b075f68` (tag `v0.2.0-sprint2`) — estado de entrada do spike
- Este ADR é adicionado sem modificar código do `transcriber/__init__.py` — decisão é "não mudar nada".

### Literatura externa relevante

- OpenAI API docs (Audio): https://platform.openai.com/docs/guides/speech-to-text
- `jiwer` 4.0 API: https://github.com/jitsi/jiwer
- Discussão sobre efeito de `prompt` do Whisper em decoder bias: referências em fóruns OpenAI (não citadas formalmente)
