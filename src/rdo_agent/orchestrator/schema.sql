-- ============================================================================
-- RDO Agent — Schema da base de conhecimento (SQLite)
--
-- Referência autoritativa: Blueprint V3 §7.2
-- Criado na Sprint 1 de forma completa e imutável — o SQLite é parte do
-- laudo de rastreabilidade jurídica, e ALTER TABLE futuros complicariam
-- auditoria. Tabelas ainda não usadas nas Sprints 1-2 ficam vazias mas
-- existem desde o dia 1.
--
-- Princípios:
--   - Isolamento por obra: toda tabela tem coluna obra (TEXT NOT NULL)
--   - created_at ISO 8601 em toda tabela (rastreabilidade)
--   - PRAGMA foreign_keys=ON e journal_mode=WAL são aplicados em init_db()
-- ============================================================================


-- ---------------------------------------------------------------------------
-- tasks — fila de execução do orchestrator
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type     TEXT NOT NULL,
    payload       TEXT NOT NULL,                       -- JSON
    status        TEXT NOT NULL
                  CHECK (status IN ('pending','running','done','failed')),
    depends_on    TEXT NOT NULL DEFAULT '[]',          -- JSON array de task_ids
    obra          TEXT NOT NULL,
    priority      INTEGER NOT NULL DEFAULT 0,          -- >= 0; maior = mais urgente
    created_at    TEXT NOT NULL,                       -- ISO 8601 UTC
    started_at    TEXT,
    finished_at   TEXT,
    error_message TEXT,
    result_ref    TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_obra_status ON tasks(obra, status);
CREATE INDEX IF NOT EXISTS idx_tasks_priority    ON tasks(priority DESC, created_at ASC);


-- ---------------------------------------------------------------------------
-- messages — mensagens extraídas do _chat.txt
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    message_id         TEXT PRIMARY KEY,
    obra               TEXT NOT NULL,
    timestamp_whatsapp TEXT NOT NULL,                  -- ISO 8601
    sender             TEXT,
    content            TEXT,
    media_ref          TEXT,                           -- nome do arquivo anexado
    is_deleted         INTEGER NOT NULL DEFAULT 0,
    is_edited          INTEGER NOT NULL DEFAULT 0,
    is_sticker         INTEGER NOT NULL DEFAULT 0,
    raw_line           TEXT,                           -- linha original do .txt
    created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_obra_ts ON messages(obra, timestamp_whatsapp);


-- ---------------------------------------------------------------------------
-- files — arquivos com hash SHA-256 e metadata temporal
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS files (
    file_id               TEXT PRIMARY KEY,
    obra                  TEXT NOT NULL,
    file_path             TEXT NOT NULL,
    file_type             TEXT NOT NULL,               -- video | audio | image | text | other
    sha256                TEXT NOT NULL,
    size_bytes            INTEGER,
    derived_from          TEXT,                        -- file_id do arquivo-fonte
    derivation_method     TEXT,                        -- ex: "ffmpeg -i ... -vn -ac 1 -ar 16000"
    referenced_by_message TEXT,                        -- message_id que menciona o arquivo
    timestamp_resolved    TEXT,                        -- ISO 8601 do evento real
    timestamp_source      TEXT,                        -- whatsapp_txt | filename | exif | mtime
    semantic_status       TEXT,                        -- awaiting_transcription | done | ...
    created_at            TEXT NOT NULL,
    FOREIGN KEY (derived_from)          REFERENCES files(file_id),
    FOREIGN KEY (referenced_by_message) REFERENCES messages(message_id)
);

CREATE INDEX IF NOT EXISTS idx_files_obra      ON files(obra);
CREATE INDEX IF NOT EXISTS idx_files_sha256    ON files(sha256);
CREATE INDEX IF NOT EXISTS idx_files_file_type ON files(file_type);


-- ---------------------------------------------------------------------------
-- media_derivations — grafo N:N de derivações (ex: vídeo → 3 frames extraídos)
-- Redundante com files.derived_from (que suporta apenas 1:1) mas necessário
-- quando um mesmo source gera múltiplos derivados (Blueprint §5.2: 3 frames/vídeo).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS media_derivations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    obra              TEXT NOT NULL,
    source_file_id    TEXT NOT NULL,
    derived_file_id   TEXT NOT NULL,
    derivation_method TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (source_file_id)  REFERENCES files(file_id),
    FOREIGN KEY (derived_file_id) REFERENCES files(file_id),
    UNIQUE (source_file_id, derived_file_id)
);


-- ---------------------------------------------------------------------------
-- api_calls — log de auditoria de toda chamada a APIs externas
-- (Blueprint §5.3 e §6.4: request/response pareados com hash SHA-256)
--
-- Sprint 2 Fase 2: latency_ms/model/error_type dedicados para
-- observabilidade de API. Derivar de started_at/finished_at/request_json
-- seria aceitável mas queries analíticas ficam custosas — priorizar
-- custo de query baixo. Vaults existentes recebem as colunas via
-- migração idempotente em init_db() (PRAGMA table_info + ALTER TABLE).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_calls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    obra           TEXT NOT NULL,
    provider       TEXT NOT NULL,                      -- openai | anthropic
    endpoint       TEXT NOT NULL,
    request_hash   TEXT NOT NULL,                      -- SHA-256 do request
    response_hash  TEXT,                               -- SHA-256 do response
    request_json   TEXT NOT NULL,
    response_json  TEXT,
    tokens_input   INTEGER,
    tokens_output  INTEGER,
    cost_usd       REAL,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    error_message  TEXT,
    latency_ms     INTEGER,                            -- finished_at - started_at em ms
    model          TEXT,                               -- ex: whisper-1, gpt-4o-mini
    error_type     TEXT,                               -- connection|rate_limit|timeout|auth_error|bad_request|api_error|NULL
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_calls_obra ON api_calls(obra);
CREATE INDEX IF NOT EXISTS idx_api_calls_hash ON api_calls(request_hash);


-- ---------------------------------------------------------------------------
-- transcriptions — resultado do Whisper API
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transcriptions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    obra           TEXT NOT NULL,
    file_id        TEXT NOT NULL,
    text           TEXT NOT NULL,
    language       TEXT,
    segments_json  TEXT,                               -- JSON do verbose_json do Whisper
    confidence     REAL,
    low_confidence INTEGER NOT NULL DEFAULT 0,
    api_call_id    INTEGER,
    created_at     TEXT NOT NULL,
    FOREIGN KEY (file_id)     REFERENCES files(file_id),
    FOREIGN KEY (api_call_id) REFERENCES api_calls(id)
);

CREATE INDEX IF NOT EXISTS idx_transcriptions_file ON transcriptions(file_id);


-- ---------------------------------------------------------------------------
-- visual_analyses — resultado do GPT-4 Vision
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS visual_analyses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    obra          TEXT NOT NULL,
    file_id       TEXT NOT NULL,
    analysis_json TEXT NOT NULL,                       -- resposta estruturada (JSON)
    confidence    REAL,
    api_call_id   INTEGER,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (file_id)     REFERENCES files(file_id),
    FOREIGN KEY (api_call_id) REFERENCES api_calls(id)
);

CREATE INDEX IF NOT EXISTS idx_visual_file ON visual_analyses(file_id);

-- ---------------------------------------------------------------------------
-- Sprint 4 Op11 Divida #10 — archive move-style
-- superseded_by aponta para a nova row em visual_analyses que substituiu
-- esta (ex: reprocessamento retroativo OCR-first). superseded_at registra
-- quando ocorreu a substituicao. Rows ativas: superseded_by IS NULL.
-- ---------------------------------------------------------------------------
-- Colunas adicionadas via _migrate_superseded_by_sprint4_op11 em init_db
-- (ALTER TABLE idempotente via PRAGMA table_info).

-- View helper pra consumer queries (semantic_classifier, RDO etc) —
-- so retorna rows ativas, escondendo versoes antigas.
CREATE VIEW IF NOT EXISTS visual_analyses_active AS
    SELECT * FROM visual_analyses WHERE superseded_by IS NULL;


-- ---------------------------------------------------------------------------
-- Sprint 5 Fase A — narrativas forenses geradas por agente Sonnet 4.6
--
-- Cada row representa uma narrativa produzida sobre:
--   - scope='day' + scope_ref=YYYY-MM-DD: narrativa do dia
--   - scope='obra_overview' + scope_ref=NULL: narrativa da obra inteira
--
-- UNIQUE (obra, scope, scope_ref, dossier_hash) eh cache key — mesmo
-- dossier gera a mesma narrativa, evita regeneracao cara.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS forensic_narratives (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    obra                       TEXT NOT NULL,
    scope                      TEXT NOT NULL
                               CHECK (scope IN ('day', 'obra_overview')),
    scope_ref                  TEXT,
    narrative_text             TEXT NOT NULL,
    dossier_hash               TEXT NOT NULL,
    model_used                 TEXT NOT NULL,
    prompt_version             TEXT NOT NULL,
    api_call_id                INTEGER,
    events_count               INTEGER,
    confidence                 REAL,
    validation_checklist_json  TEXT,
    created_at                 TEXT NOT NULL,
    FOREIGN KEY (api_call_id) REFERENCES api_calls(id),
    UNIQUE (obra, scope, scope_ref, dossier_hash)
);

CREATE INDEX IF NOT EXISTS idx_narratives_obra_scope
    ON forensic_narratives(obra, scope, scope_ref);


-- ---------------------------------------------------------------------------
-- Sprint 5 Fase B (ESQUELETO — implementacao na proxima sessao)
--
-- Correlacoes sao relacoes temporais/semanticas detectadas entre eventos
-- (ex: pedido de PIX seguido de transferencia real <30min). Nesta sessao,
-- apenas o schema esta pronto — detectores virao em Fase B.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS correlations (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    obra                  TEXT NOT NULL,
    correlation_type      TEXT NOT NULL,
    primary_event_ref     TEXT NOT NULL,
    primary_event_source  TEXT NOT NULL,
    related_event_ref     TEXT NOT NULL,
    related_event_source  TEXT NOT NULL,
    time_gap_seconds      INTEGER,
    confidence            REAL,
    rationale             TEXT,
    detected_by           TEXT,
    created_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_correlations_obra
    ON correlations(obra, correlation_type);


-- ---------------------------------------------------------------------------
-- documents — texto extraído de PDFs e similares (Sprint 2 §Fase 1)
--
-- ADICIONADA EM SPRINT 2: reconhecimento explícito de necessidade não prevista
-- no Blueprint V3 §7.2 original. NÃO é fragmentação indevida — texto extraído
-- de PDF é semanticamente distinto de transcrição de áudio: o primeiro é
-- determinístico (mesmo PDF + mesmo método = mesmo texto), o segundo é
-- estocástico (depende do modelo Whisper). Manter em tabelas separadas evita
-- queries que misturam confidence/segments_json (sem sentido para PDF) com
-- page_count (sem sentido para áudio).
--
-- file_id aponta para o .txt DERIVADO (em 20_transcriptions/), NÃO para o
-- PDF-fonte. Isso simplifica o join do classificador da Sprint 3, que opera
-- sobre rows de files com semantic_status='awaiting_classification'.
--
-- UNIQUE(file_id) garante idempotência: extração via pdfplumber é
-- determinística, então re-rodar o handler não cria duplicatas.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    obra              TEXT NOT NULL,
    file_id           TEXT NOT NULL,
    text              TEXT,                              -- pode ser '' (PDF escaneado)
    page_count        INTEGER,
    extraction_method TEXT NOT NULL,                     -- ex: "pdfplumber>=0.11"
    api_call_id       INTEGER,                           -- placeholder p/ OCR futuro
    created_at        TEXT NOT NULL,
    FOREIGN KEY (file_id)     REFERENCES files(file_id),
    FOREIGN KEY (api_call_id) REFERENCES api_calls(id),
    UNIQUE (file_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_file ON documents(file_id);


-- ---------------------------------------------------------------------------
-- events — unidades classificadas que alimentam o agente-engenheiro
-- (Blueprint §6.3: payload de entrada do Claude)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    event_id          TEXT PRIMARY KEY,
    obra              TEXT NOT NULL,
    event_date        TEXT NOT NULL,                   -- YYYY-MM-DD
    event_time        TEXT,                            -- HH:MM
    categories        TEXT NOT NULL DEFAULT '[]',      -- JSON array
    content           TEXT NOT NULL,
    confidence        TEXT,                            -- high | medium | low
    evidence_refs     TEXT NOT NULL DEFAULT '[]',      -- JSON array de file_ids
    source_message_id TEXT,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (source_message_id) REFERENCES messages(message_id)
);

CREATE INDEX IF NOT EXISTS idx_events_obra_date ON events(obra, event_date);


-- ---------------------------------------------------------------------------
-- clusters — agrupamentos de eventos relacionados
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clusters (
    cluster_id  TEXT PRIMARY KEY,
    obra        TEXT NOT NULL,
    event_date  TEXT NOT NULL,                         -- YYYY-MM-DD
    description TEXT,
    event_ids   TEXT NOT NULL DEFAULT '[]',            -- JSON array
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clusters_obra_date ON clusters(obra, event_date);


-- ---------------------------------------------------------------------------
-- classifications — output das Camadas 1 e 3 da Sprint 3
--
-- ADICIONADA EM SPRINT 3 §Fase 1. Ver docs/ADR-002-classifications-table-
-- schema.md (+ adendo pós-implementação).
--
-- source_file_id aponta para o arquivo DERIVADO (20_transcriptions/*.txt,
-- 20_visual_analyses/*.json, 20_documents/*.txt). source_type distingue
-- o formato para que o classificador adapte prompt na Fase 3.
--
-- State machine:
--   pending_quality  -> (detector)  -> pending_classify (se coerente)
--                                    | pending_review   (se suspeita/ilegivel)
--   pending_review   -> (humano)    -> pending_classify (corrigiu) | rejected
--   pending_classify -> (classifier)-> classified
--
-- UNIQUE(obra, source_file_id) garante idempotência: reprocessar o
-- mesmo arquivo sobrescreve atomicamente.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS classifications (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    obra                    TEXT NOT NULL,
    source_file_id          TEXT NOT NULL,
    source_type             TEXT NOT NULL,

    -- Camada 1 (detector de qualidade, gpt-4o-mini)
    quality_flag            TEXT,
    quality_reasoning       TEXT,
    human_review_needed     INTEGER NOT NULL DEFAULT 0,
    quality_api_call_id     INTEGER,
    quality_model           TEXT,

    -- Camada 2 (revisão humana via CLI)
    human_reviewed          INTEGER NOT NULL DEFAULT 0,
    human_corrected_text    TEXT,
    human_reviewed_at       TEXT,

    -- Camada 3 (classificador semântico, gpt-4o-mini)
    categories              TEXT NOT NULL DEFAULT '[]',
    confidence_model        REAL,
    reasoning               TEXT,
    classifier_api_call_id  INTEGER,
    classifier_model        TEXT,

    -- Auditoria + state machine
    source_sha256           TEXT NOT NULL,
    semantic_status         TEXT NOT NULL DEFAULT 'pending_quality'
                            CHECK (semantic_status IN (
                                'pending_quality',
                                'pending_review',
                                'pending_classify',
                                'classified',
                                'rejected'
                            )),
    created_at              TEXT NOT NULL,
    updated_at              TEXT,

    -- Sprint 4 Op1: referencia mensagem de texto puro quando source_type=
    -- 'text_message'. NULL para outros source_types.
    source_message_id       TEXT,

    FOREIGN KEY (source_file_id)         REFERENCES files(file_id),
    FOREIGN KEY (source_message_id)      REFERENCES messages(message_id),
    FOREIGN KEY (quality_api_call_id)    REFERENCES api_calls(id),
    FOREIGN KEY (classifier_api_call_id) REFERENCES api_calls(id),
    UNIQUE (obra, source_file_id)
);

CREATE INDEX IF NOT EXISTS idx_classifications_obra_status ON classifications(obra, semantic_status);
CREATE INDEX IF NOT EXISTS idx_classifications_review     ON classifications(human_review_needed, human_reviewed);
CREATE INDEX IF NOT EXISTS idx_classifications_source    ON classifications(source_file_id);


-- ---------------------------------------------------------------------------
-- financial_records — dados estruturados de comprovantes financeiros
-- (Sprint 4 Op8 — pipeline OCR-first)
--
-- Populada pelo handler `ocr_first_handler` quando OCR + classificador
-- estrutural detectam comprovante PIX/TED/boleto/nota/recibo. Separada
-- de `documents` pq o schema de dados tabulares (valor, pagador,
-- recebedor, chave Pix etc.) eh radicalmente distinto de texto livre.
-- Valor em centavos (INTEGER) para evitar erros de ponto flutuante.
--
-- UNIQUE(obra, source_file_id) garante 1 registro por imagem —
-- reprocessar a mesma imagem sobrescreve atomicamente.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS financial_records (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    obra                TEXT NOT NULL,
    source_file_id      TEXT NOT NULL,   -- file_id da imagem original
    doc_type            TEXT,             -- 'pix', 'ted', 'boleto', 'nota_fiscal', 'outro'
    valor_centavos      INTEGER,          -- valor em centavos (evita float)
    moeda               TEXT DEFAULT 'BRL',
    data_transacao      TEXT,             -- ISO YYYY-MM-DD
    hora_transacao      TEXT,             -- HH:MM:SS
    pagador_nome        TEXT,
    pagador_doc         TEXT,             -- CNPJ/CPF como aparece (mascarado se mascarado)
    recebedor_nome      TEXT,
    recebedor_doc       TEXT,
    chave_pix           TEXT,
    descricao           TEXT,             -- "Informação para o recebedor" em PIX
    instituicao_origem  TEXT,
    instituicao_destino TEXT,
    raw_ocr_text        TEXT,             -- texto bruto OCR pra auditoria
    confidence          REAL,             -- 0.0-1.0, retorno do modelo
    api_call_id         INTEGER,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (source_file_id) REFERENCES files(file_id),
    FOREIGN KEY (api_call_id)    REFERENCES api_calls(id),
    UNIQUE (obra, source_file_id)
);

CREATE INDEX IF NOT EXISTS idx_financial_records_obra_data
    ON financial_records(obra, data_transacao);


-- ---------------------------------------------------------------------------
-- visual_analyses_archive — historico de analyses superseded por
-- reprocessamentos (Sprint 4 Op9 pipeline OCR-first retroativo).
--
-- Mirror exato de visual_analyses + archived_at + archive_reason.
-- Preserva forense: qualquer analise substituida continua auditavel
-- pra prova de linhagem de dados.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS visual_analyses_archive (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    original_id     INTEGER,            -- id original de visual_analyses
    obra            TEXT NOT NULL,
    file_id         TEXT NOT NULL,
    analysis_json   TEXT NOT NULL,
    confidence      REAL,
    api_call_id     INTEGER,
    created_at      TEXT NOT NULL,      -- created_at original da analyse
    archived_at     TEXT NOT NULL,      -- quando foi movida pra archive
    archive_reason  TEXT                -- ex: 'superseded_by_ocr_first_retroactive_sprint4_op9'
);

CREATE INDEX IF NOT EXISTS idx_visual_analyses_archive_obra
    ON visual_analyses_archive(obra, archived_at);
CREATE INDEX IF NOT EXISTS idx_visual_analyses_archive_fileid
    ON visual_analyses_archive(file_id);
