# ADR-003 — Suporte multi-source em `classifications` via synthetic files row

**Status:** Aceito (registro retrospectivo de decisão implementada)
**Data da decisão:** 2026-04-22 (Sprint 4 Op1)
**Data deste registro:** 2026-04-22
**Decisores:** Lucas (proprietário do projeto), Claude Code (executor Sprint 4)
**Escopo:** Schema do pipeline `rdo-agent`, camada classifications (Blueprint §5)
**Relacionados:** ADR-001 (transcription model), ADR-002 (classifications schema)

---

## Contexto

O Sprint 3 consolidou `classifications` como ponto central da camada de
classificação semântica, assumindo que **toda classificação deriva de um
arquivo** em `files`. Isso era verdadeiro enquanto a única fonte eram
transcrições de áudio, cujos arquivos `.transcription.txt` vivem em `files`
com `derived_from` apontando para o áudio original.

O Sprint 4 ampliou o pipeline para **4 fontes heterogêneas**:

| source_type | Origem do texto | Vive naturalmente em `files`? |
|---|---|---|
| `transcription` | `transcriptions.text` via `.txt` derivado | Sim (arquivo físico) |
| `text_message` | `messages.content` direto | **Não** (mensagens não são arquivos) |
| `visual_analysis` | `visual_analyses.analysis_json` via `.json` derivado | Sim (arquivo físico) |
| `document` | `documents.text` via `.txt` derivado | Sim (arquivo físico) |

A introdução de `text_message` expôs tensão estrutural:

- `classifications.source_file_id` é `NOT NULL` com `FOREIGN KEY` → `files(file_id)`
- Mensagens de texto puro (sem anexo WhatsApp) não possuem entrada natural em `files`
- Briefing original propunha `source_file_id=NULL`, mas isso requereria
  `ALTER TABLE` destrutivo em SQLite (recriar tabela, migrar 105 rows)

## Decisão

Adotamos três mudanças coordenadas, aplicadas em commit `4130c6b`:

### 1. Nova coluna nullable `classifications.source_message_id`

```sql
ALTER TABLE classifications ADD COLUMN source_message_id TEXT;
-- FK lógica: REFERENCES messages(message_id)
```

- Populada quando `source_type='text_message'`
- `NULL` para demais source_types
- Migração idempotente via `_migrate_classifications_sprint4()` em
  `orchestrator/__init__.py`

### 2. Synthetic files row por mensagem de texto puro

Para preservar `source_file_id NOT NULL`, o ingestor cria uma linha
sintética em `files` por mensagem, com `file_id` determinístico:

```python
files(
    file_id = "m_<8_hex_do_hash_do_content>",  # pseudo-file_id estável
    obra = <obra>,
    file_path = "",                             # vazio — não há arquivo físico
    file_type = "message",                      # novo valor discriminador
    sha256 = sha256(content),
    size_bytes = len(content.encode("utf-8")),
    referenced_by_message = <message_id>,
    timestamp_resolved = <messages.timestamp_whatsapp>,
    timestamp_source = "whatsapp_txt",
    semantic_status = "awaiting_classification",
    created_at = <iso>
)
```

Propriedades desejáveis:
- `file_id` determinístico (hash do content) — idempotência natural
- `file_type='message'` funciona como discriminador em queries de auditoria
- `referenced_by_message` fecha ligação bidirecional com `messages`
- `UNIQUE(obra, source_file_id)` em `classifications` dedupa naturalmente

### 3. Helper central `_get_classification_text()` em `semantic_classifier.py`

Dispatcher único que resolve o texto a classificar baseado em `source_type`:

- `transcription` → `transcriptions.text`
- `text_message` → `messages.content` via `source_message_id`
- `visual_analysis` → concatenação de campos de `analysis_json`
- `document` → `documents.text`

Todas as queries filtram por `obra` (multi-tenancy preservada).

## Alternativas consideradas

### A) `source_file_id` nullable

Tornar a coluna `NULL`-permissível via recriação da tabela.

- **Seria correta se:** o projeto estivesse em fase pré-produção (zero dados
  reais) ou pudéssemos aceitar janela de manutenção com risco assumido
- **Custo se adotada hoje:** ~6h de trabalho + risco real de corrupção de
  dados em vault EVERALDO (105+ classifications em produção) + necessidade
  de backup + script de migração auditável + teste em staging
- **Rejeitada porque:** SQLite não suporta `ALTER COLUMN DROP NOT NULL`
  diretamente. O ganho (semântica mais limpa) não compensa o custo e o risco
  com volume atual de dados reais

### B) Tabela paralela `classifications_message`

Criar tabela dedicada para classificações originadas de textos puros.

- **Seria correta se:** as regras de negócio para classificar texto puro
  fossem radicalmente diferentes das demais (ex: ontologia separada,
  auditoria separada por requisito regulatório)
- **Custo se adotada:** multiplica esforço de `generate_rdo_piloto.py`
  (UNION ALL entre N tabelas), quebra premissa arquitetural de
  *classifications como hub único*, complica futuras expansões
  (imagine 5 tabelas de classificação quando chegar o 5º source_type)
- **Rejeitada porque:** o classifier reusa o mesmo prompt e modelo para
  todas as fontes atualmente. Separar por tabela otimizaria um problema
  que não existe e criaria débito arquitetural real

### C) Coluna `raw_content` literal em `classifications`

Armazenar o conteúdo da mensagem diretamente, sem FK para `messages`.

- **Seria correta se:** desejássemos independência total de `classifications`
  em relação a `messages` (ex: planejamento de arquivar `messages` após
  N dias e manter classifications viva)
- **Custo se adotada:** duplica storage, dessincroniza com
  `messages.content` (atualização em messages não se reflete), perde
  rastreabilidade "qual mensagem WhatsApp originou essa classificação",
  viola 3NF
- **Rejeitada porque:** rastreabilidade forense é requisito não-negociável
  (ver "Cenários forenses concretos" abaixo). Desnormalizar aqui é perda
  de capacidade probatória

## Consequências

### Impacto operacional (métricas)

| Métrica | Antes (Sprint 3) | Depois (Sprint 4) | Delta |
|---|---|---|---|
| Rows em `files` (EVERALDO) | ~360 | ~438 | +78 synthetic (+22%) |
| Storage de `files` (EVERALDO) | ~80 KB | ~95 KB | +15 KB (+19%) |
| Queries JOIN `classifications`↔`files` | baseline | sem regressão mensurável | p95 <50ms |
| Esforço futuro para 5º source_type | N/A | ~1 dia (padrão synthetic row) | vs ~5 dias se schema refatorado |
| Tempo real de implementação Op1 | — | ~3h | vs ~8-12h estimados para alternativa A |
| Custo de oportunidade economizado | — | ~5-9h | aplicadas em Op2-Op7 do mesmo sprint |

### Positivas

- **Zero risco de migração:** apenas `ALTER TABLE ... ADD COLUMN`, reversível
- **FK integrity preservada:** toda classification ainda tem `source_file_id` válido
- **Idempotência natural:** hash-based `file_id` + `UNIQUE(obra, source_file_id)` dedupa automaticamente sem lógica extra
- **Extensibilidade:** padrão "synthetic row em `files`" reutilizável para 5º/6º source_type futuro (`user_note`, `call_transcript`, `manual_annotation` etc.)
- **Velocidade de entrega:** permitiu Sprint 4 completar 7 operações em 1 dia

### Negativas / dívidas técnicas

- **`files` passa a ter linhas que não representam arquivos físicos** — 78
  rows em EVERALDO com `file_path=""`. Queries de auditoria de arquivos
  físicos precisam filtrar `WHERE file_type != 'message'`
- **Semântica mista de `files`:** tabela agora representa dois conceitos:
  (a) arquivo físico no disco, (b) "ponteiro canônico" para conteúdo
  classificável. Documentação inline do schema requer atualização
- **Path de migração futura em aberto:** se um dia for desejável separar
  conceitos em tabelas distintas (`content_pointers` vs `files`), haverá
  trabalho de migração das synthetic rows

### Neutras / a monitorar

- Performance de `JOIN` entre `classifications` e `files` pode degradar
  se volume de synthetic rows crescer desproporcionalmente
- Queries analíticas que agregam por `file_type` devem considerar
  `'message'` como categoria distinta
- Backups da vault ficam ~20% maiores, mas absolutamente aceitável

## Cenários forenses concretos

Esta decisão foi tomada com consciência explícita dos seguintes cenários
de uso forense do pipeline `rdo-agent` em contexto contratual Vale Nobre ×
SEE-MG ou contencioso similar:

### Cenário 1 — Contestação de pagamento prévio

**Situação hipotética:** Em disputa judicial, a outra parte (empreiteiro
Everaldo) afirma *"nunca recebi R$3.500 do Lucas antes de 08/04/2026"*.

**Como esta decisão ajuda:** A mensagem onde ele próprio afirma *"você já
me mandou 3 e 500"* (audio de 08/04 12:14 UTC, classification id 40) está
ancorada na cadeia:

```
WhatsApp export.zip (sha256 preservado no ingest)
  └─ messages(message_id="m_xyz", timestamp_whatsapp, sender="Everaldo", content, sha256)
       └─ files [synthetic] (file_id="m_<hash>", sha256_conteúdo, file_type='message')
            └─ classifications (source_type, source_message_id, source_file_id, api_call_id)
                 └─ api_calls (provider, request, response)
```

Cada elo é auditável. Se tivéssemos optado pela alternativa C
(`raw_content` literal), **perderíamos a âncora no message_id original**
— a defesa poderia alegar que o texto em `classifications` foi editado
após o fato.

### Cenário 2 — Reconstrução cronológica de negociação

**Situação hipotética:** Auditoria precisa reconstruir a sequência de
eventos do dia 08/04/2026 (19 negociações + 6 menções a pagamento) em
ordem cronológica, cruzando áudios transcritos + textos enviados por
WhatsApp + fotos de comprovante PIX.

**Como esta decisão ajuda:** os 4 source_types vivem na mesma tabela
`classifications`, com campo `source_type` discriminador e
`timestamp_resolved` homogeneizado via JOIN com `files`. Uma única query
ordena todos os eventos cronologicamente:

```sql
SELECT c.*, COALESCE(f_audio.timestamp_resolved,
                     m_direct.timestamp_whatsapp,
                     f_visual.timestamp_resolved) AS ts
FROM classifications c
LEFT JOIN ... -- dispatcher multi-source no generate_rdo_piloto.py
WHERE c.obra = ? AND DATE(ts) = ?
ORDER BY ts;
```

Se tivéssemos optado pela alternativa B (tabela paralela), precisaríamos
`UNION ALL` de 2-4 tabelas com schemas divergentes — reconstrução
cronológica seria frágil e sujeita a erro.

### Cenário 3 — Prova de integridade da cadeia

**Situação hipotética:** juiz solicita prova de que o sistema não
alterou conteúdo original das mensagens entre ingest e classificação.

**Como esta decisão ajuda:** a synthetic files row carrega `sha256` do
`content` na ingestão. A coluna `messages.content` também é `NOT NULL`.
Comparar ambos prova integridade:

```sql
SELECT m.message_id,
       sha256_col_content = f.sha256 AS integro
FROM messages m
JOIN files f ON f.referenced_by_message = m.message_id
WHERE f.file_type = 'message' AND f.obra = ?;
```

Qualquer divergência indica adulteração. A decisão preserva esse
invariante sem custo adicional.

## Gatilhos de reavaliação

Esta decisão deve ser reconsiderada se qualquer condição abaixo for
atendida. Enquanto nenhuma for disparada, manter a estrutura atual:

1. **Volume** — synthetic rows em `files` ultrapassarem **30% do total**
   (hoje: 17% em EVERALDO). Sinaliza que `files` está deixando de
   representar primariamente arquivos físicos
2. **Performance** — queries de `JOIN` entre `classifications` e `files`
   degradarem além de **500ms em p95** em vaults com >1000 classifications
3. **Novo source_type complexo** — surgir fonte que exija >3 colunas
   adicionais em `classifications` (sinal de que synthetic row não escala)
4. **Regulação externa** — exigência legal ou contratual futura de
   separar "arquivo físico" de "ponteiro de conteúdo" em sistemas
   forenses (ex: norma ABNT específica)
5. **Multi-tenancy crítica** — expansão para >10 obras simultâneas com
   volumes >1000 classifications/obra (sinal de escala em produção que
   justifica refatoração arquitetural)
6. **5º source_type com ontologia divergente** — se surgir fonte cujas
   categorias semânticas sejam radicalmente diferentes das 9 atuais
   (revisita alternativa B)

Ao disparar qualquer gatilho, o path de migração preferencial é:
nova tabela `content_pointers` (substituindo synthetic rows), com
script de migração não-destrutivo em staging primeiro.

## Referências

- Commit `4130c6b` — feat(sprint4-op1): ingestao e classificacao de texto puro WhatsApp
- Commit `fc5de34` — docs(sprint4): SESSION_LOG autonomo V2 (contexto de execução)
- Tag `v0.4.0-sprint4-ingestao` (9985f38) — estado estável pós-Sprint 4
- `src/rdo_agent/classifier/text_message_ingestor.py` — implementação do ingestor
- `src/rdo_agent/classifier/semantic_classifier.py::_get_classification_text` — dispatcher multi-source
- `src/rdo_agent/orchestrator/__init__.py::_migrate_classifications_sprint4` — migração idempotente
- `src/rdo_agent/orchestrator/schema.sql` — schema canônico
- ADR-001 — Transcription model selection (contexto Sprint 2)
- ADR-002 — Classifications table schema (contexto Sprint 3)
- Blueprint §5 — Camada de Classificação (contexto conceitual)
