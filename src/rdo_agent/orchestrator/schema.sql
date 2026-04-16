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
