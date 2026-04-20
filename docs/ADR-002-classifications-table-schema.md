# ADR-002 — Tabela de Output da Classificação Semântica (Sprint 3)

- **Data:** 2026-04-20
- **Status:** Aceita
- **Autor:** Lucas Ferreira Leite (com análise de Claude/Anthropic)
- **Sprint:** 3 (classificação semântica + revisão humana)
- **ADRs relacionadas:** ADR-001 (seleção do motor de transcrição)
- **Backed by:** exercício de calibração em `~/rdo_vaults/EVERALDO_SANTAQUITERIA/99_logs/classificacao_manual/`

## Contexto

Sprint 3 converte 105 transcrições (Whisper baseline, WER ~46% — ver ADR-001) em eventos classificados que alimentam o agente-engenheiro Claude da Sprint 4. A tabela `events` do schema original foi desenhada para receber output agregado do agente-engenheiro, não da classificação intermediária.

Calibração manual de 31 transcrições estratificadas (seed=42, 4 strata de confidence) demonstrou que:
- 29% das transcrições são **ilegíveis** na saída Whisper (loops, inversões, alucinações) e exigem revisão humana
- 71% têm conteúdo semântico classificável em 8 categorias emergentes
- Multi-label real é raro (<15% dos casos); single-label com primary é o caso comum

## Questão

Onde persistir o output da classificação de Sprint 3, e com qual schema, de modo a:
1. Preservar rastreabilidade forense (transcrição original → classificação);
2. Permitir revisão humana iterativa sem reprocessar todo o pipeline;
3. Separar responsabilidades entre Camada 3 (classificador) e Sprint 4 (agente-engenheiro);
4. Evitar poluir `events` (que continuará sendo output da Sprint 4)?

## Decisão

**Nova tabela `classifications`** entre `transcriptions` e `events`. Relação `N classifications -> 1 event` (o agente-engenheiro agrega classificações relacionadas em um evento).

### Schema

    CREATE TABLE classifications (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        obra                    TEXT NOT NULL,
        source_file_id          TEXT NOT NULL,
        source_type             TEXT NOT NULL,
        categories              TEXT NOT NULL DEFAULT '[]',
        confidence_model        REAL,
        reasoning               TEXT,
        human_review_needed     INTEGER NOT NULL DEFAULT 0,
        human_reviewed          INTEGER NOT NULL DEFAULT 0,
        human_corrected_text    TEXT,
        model                   TEXT NOT NULL,
        api_call_id             INTEGER,
        source_sha256           TEXT NOT NULL,
        semantic_status         TEXT NOT NULL DEFAULT 'classified',
        created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY (source_file_id) REFERENCES files(file_id),
        FOREIGN KEY (api_call_id) REFERENCES api_calls(id),
        UNIQUE (obra, source_file_id)
    );
    CREATE INDEX idx_classifications_obra_status ON classifications(obra, semantic_status);
    CREATE INDEX idx_classifications_review ON classifications(human_review_needed, human_reviewed);

### Vocabulário inicial (hardcoded no prompt do classificador)

| Código | Descrição |
|---|---|
| `negociacao_comercial` | Discussão de valor, termos, acordo, propostas |
| `pagamento` | Mecânica de pagamento (adiantamento, forma, PIX) |
| `cronograma` | Prazos, encontros, checagens de andamento |
| `especificacao_tecnica` | Como trabalho deve ser feito (dimensões, método) |
| `solicitacao_servico` | Pedido explícito de execução |
| `material` | Insumos como objeto principal da fala |
| `reporte_execucao` | Relato de trabalho feito no canteiro |
| `off_topic` | Conversa fora do escopo contratual |
| `ilegivel` | Transcrição degradada, não classificável |

Regras de fronteira entre categorias: ver `~/rdo_vaults/EVERALDO_SANTAQUITERIA/99_logs/classificacao_manual/amostra_31_consolidado.md`.

## Consequências

### Positivas

- **Classifier determinístico por definição:** `UNIQUE (obra, source_file_id)` garante 1 classificação por fonte. Reprocessar sobrescreve atomicamente.
- **Revisão humana sem perda:** `human_corrected_text` preservado; agente-engenheiro da Sprint 4 usa prioritariamente texto revisado se presente.
- **Auditabilidade:** `source_sha256` + `api_call_id` permitem reconstruir pipeline completo.
- **Separação limpa:** `events` continua vazio até Sprint 4; classificações são insumo, não output final.

### Negativas / trade-offs aceitos

- **Duplicação parcial de dados:** `human_corrected_text` duplica porção de `transcriptions.text` quando houver correção. Aceitável pois texto original permanece imutável em `transcriptions`.
- **Complexidade do estado:** 3 flags (`human_review_needed`, `human_reviewed`, `semantic_status`) criam matriz de 2x2x3 = 12 estados teóricos. Apenas 5 são válidos na prática (documentados no SPRINT3_PLAN). Não é complexidade excessiva, mas requer state machine clara.
- **`events` fica "órfã" até Sprint 4:** tabela existe no schema sem receber INSERT por ~2 sprints. Aceitável pois ela está prevista em Blueprint_V3.

## Alternativas rejeitadas

### R1 — Escrever direto em `events` (schema original)

Rejeitada. `events.categories` foi projetada para output do agente-engenheiro (narrativa agregada por dia/área), não por-transcrição. Reusar mistura responsabilidades e dificulta reprocessamento isolado.

### R2 — Adicionar colunas em `transcriptions`

Rejeitada. Explode o schema de transcriptions (que é só sobre resultado Whisper) com concerns de Sprint 3. Também quebra imutabilidade: mesma transcrição pode ter classificações diferentes ao longo do tempo (ex: vocabulário evolui), e alterar `transcriptions` exigiria migrations mais arriscadas.

### R3 — Tabela única `classifications_v2_revised` apartada para humano

Rejeitada. Duplica estrutura sem ganho; forçaria JOINs complexos para recuperar estado atual. `human_*` columns na mesma tabela é mais simples.

## Referências

- Calibração empírica: `amostra_31_trabalho.md` + `amostra_31_consolidado.md` na vault EVERALDO
- Schema atual: `orchestrator/schema.sql`
- Commit de entrada: `38bb50f` (tag anterior `v0.2.0-sprint2` em `b075f68`)
