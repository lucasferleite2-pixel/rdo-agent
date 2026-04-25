# rdo-agent

**Agente Forense de RDO — Sistema multi-agente para geração de Relatórios Diários de Obra a partir de exportações do WhatsApp, com cadeia de custódia auditável.**

Desenvolvido para Construtora e Imobiliária Vale Nobre Ltda. — contratos SEE-MG / SRE Manhuaçu.

---

## Visão Geral

Converte exportações de conversas do WhatsApp (incluindo mídias anexas) em RDOs tecnicamente consistentes, cronologicamente auditáveis e juridicamente defensáveis.

**Arquitetura:**
- **Camada 1 (local, Python):** ingestão, hashing SHA-256, parser do .txt, resolução temporal, extração de áudio de vídeo, orquestrador
- **Camada 2 (OpenAI API):** Whisper para transcrição, GPT-4 Vision para análise de imagens
- **Camada 3 (Anthropic API):** Claude como agente-engenheiro para síntese do RDO

**Documentação completa:** ver `docs/PROJECT_CONTEXT.md` (briefing institucional canônico) e `docs/Blueprint_V3_Agente_Forense_RDO.docx` (especificação de referência histórica).

---

## Stack

- Python 3.11+
- ffmpeg, exiftool (sistema)
- SQLite (nativo)
- OpenAI API (Whisper + GPT-4 Vision)
- Anthropic API (Claude)
- Obsidian (visualização da base) + Git (versionamento)

---

## Setup inicial (WSL2 Ubuntu 22.04)

```bash
# 1. Dependências de sistema
sudo apt update
sudo apt install -y ffmpeg exiftool libmediainfo0v5 \
                    python3.12 python3.12-venv python3-pip git
# libmediainfo0v5: necessário para extração de timestamp de vídeo/áudio via pymediainfo
# python3.12 é o recomendado (testado em desenvolvimento). 3.11 é o mínimo suportado.

# 2. Clonar e entrar no projeto
git clone git@github.com:lucasferleite2-pixel/rdo-agent.git
cd rdo-agent

# 3. Ambiente virtual
# Recomendado (testado em desenvolvimento):
python3.12 -m venv .venv
# Alternativa mínima suportada:
# python3.11 -m venv .venv
source .venv/bin/activate

# 4. Dependências Python
pip install -e ".[dev]"

# 5. Configurar variáveis de ambiente
cp .env.example .env
# Editar .env e adicionar OPENAI_API_KEY e ANTHROPIC_API_KEY

# 6. Verificar instalação
rdo-agent --version
pytest
```

---

## Uso básico (MVP)

```bash
# Processar um zip do WhatsApp
rdo-agent ingest path/to/WhatsApp.zip --obra CODESC_75817

# Ver status do processamento
rdo-agent status --obra CODESC_75817

# Gerar RDO de um dia específico
rdo-agent generate-rdo --obra CODESC_75817 --data 2026-03-12
```

---

## Exportar laudo forense (Vestígio · v1.0)

```bash
rdo-agent export-laudo --corpus NOME_DO_CANAL --output laudo.pdf
```

Opções:

- `--adversarial` — inclui contestações hipotéticas (narrator V4).
  Gera seção "Como a outra parte rebateria…" para preparar defesa.
- `--certified` — adiciona selo de certificação + marca d'água dourada.
- `--context FILE.yml` — injeta Ground Truth (metadata auditável).
- `--config FILE.yml` — overrides de cliente, processo, objeto, operador.

Exemplo completo:

```bash
rdo-agent export-laudo \
  --corpus EVERALDO_SANTAQUITERIA \
  --output ~/laudos/everaldo-2026-04.pdf \
  --adversarial \
  --context docs/ground_truth/EVERALDO_SANTAQUITERIA.yml
```

Amostra de referência: `docs/brand/Laudo-Real-EVERALDO-v1.1.pdf`
(51 páginas, gerada 100% a partir de dados reais do corpus piloto na
Sessão 5 com detector CONTRACT_RENEGOTIATION ativo).

## Streaming na geração de narrativa (v1.1+)

A flag `--stream` em `rdo-agent narrate` imprime a narrativa em tempo
real conforme o modelo gera, melhorando a UX em sessões longas
(overview pode levar 60–120s):

```bash
rdo-agent narrate --obra EVERALDO_SANTAQUITERIA \
  --scope obra --skip-cache --stream
```

Persistência (DB + arquivo) só acontece após o stream completar.
Falhas mid-stream propagam sem retry (sem semântica de recuperação
limpa).

## Variáveis de ambiente

### APIs externas (obrigatórias)

- `ANTHROPIC_API_KEY` — chave para o narrator Sonnet 4.6.
- `OPENAI_API_KEY` — Whisper local + GPT-4o-mini + GPT-4o Vision.

### Narrator (opcionais — v1.1+)

- `RDO_AGENT_MAX_TOKENS_OVERRIDE_<SCOPE>` — sobrescreve o
  `max_tokens` do narrator para um scope específico:

  ```bash
  RDO_AGENT_MAX_TOKENS_OVERRIDE_OVERVIEW=20000 \
    rdo-agent narrate --obra X --scope obra
  ```

  Valores default em `MAX_TOKENS_BY_SCOPE`: day=6144, week=8192,
  month=10240, overview/obra_overview=16384.

### Resiliência (opcionais — v1.2+)

Configuram `CircuitBreaker`, `RateLimiter` e `CostQuota` em
`src/rdo_agent/observability/resilience.py`. Defaults conservadores
funcionam pra desenvolvimento; ajustar em produção conforme limites
reais das APIs:

- `RDO_AGENT_CIRCUIT_FAILURE_THRESHOLD` (default `5`) — falhas
  consecutivas até abrir o circuit.
- `RDO_AGENT_CIRCUIT_RECOVERY_SEC` (default `300`) — segundos em
  estado OPEN antes de tentar HALF_OPEN.
- `RDO_AGENT_RATE_LIMIT_OPENAI_PER_MIN` (default `60`).
- `RDO_AGENT_RATE_LIMIT_ANTHROPIC_PER_MIN` (default `20`).
- `RDO_AGENT_DAILY_QUOTA_USD` (default `100.0`).

## Pre-flight check (v1.3+)

Antes de disparar processamento pesado em ZIP grande, estime
recursos sem extrair nada:

```bash
rdo-agent estimate --zip path/to/whatsapp-export.zip
```

Output cobre: counts (mensagens, áudios, imagens, vídeos, PDFs),
disco necessário vs disponível, custos estimados por estágio
(transcribe Whisper local, classify gpt-4o-mini, vision gpt-4o,
narrator Sonnet) com bounds ±50%, tempo estimado single-machine,
e warnings explícitos (custo > $50, disco insuficiente, chat.txt
ausente do ZIP).

Exit code `3` quando `disco insuficiente` ou `custo > $50` — útil
em scripts CI que querem gate sem ler stdout.

Rates calibradas por env vars (override de qualquer item):

```bash
RDO_AGENT_PREFLIGHT_VISION_USD_PER_IMAGE=0.01 \
  rdo-agent estimate --zip path.zip
```

## Pipeline state e logging (v1.2+)

Wrapper sobre a tabela `tasks` (state machine do orchestrator desde
Sprint 1, populada com ~675 jobs no vault piloto):

```bash
# Estado por (task_type, status), totais, e detecção de tasks
# resumíveis (running sem finished_at = possível crash).
rdo-agent pipeline-status --obra EVERALDO_SANTAQUITERIA

# Recovery após crash:
rdo-agent pipeline-reset --obra EVERALDO_SANTAQUITERIA --target running

# Retry de falhas transientes:
rdo-agent pipeline-reset --obra EVERALDO_SANTAQUITERIA --target failed \
    [--task-type transcribe]
```

Logging JSONL emitido em `~/.rdo-agent/logs/<obra>/<YYYY-MM-DD>.jsonl`
(quando o pipeline é instrumentado por `StructuredLogger`):

```bash
# Snapshot dos últimos N registros formatados:
rdo-agent watch --obra EVERALDO_SANTAQUITERIA [--last 20] [--event-type cost]

# Sumário agregado: counts por event_type, custo total por API,
# duração por stage (min/median/max), falhas por stage e error_type:
rdo-agent stats --obra EVERALDO_SANTAQUITERIA
```

### Dependências de sistema (WeasyPrint / Laudo PDF)

O módulo de geração de laudo PDF (Vestígio) usa WeasyPrint, que requer
**libcairo** e **libpango** instaladas no sistema. Sem essas libs,
`rdo-agent export-laudo` falha na inicialização.

**Ubuntu / Debian / WSL2 Ubuntu:**

```bash
sudo apt-get install -y libcairo2 libpango-1.0-0 libpangoft2-1.0-0 \
                        libgdk-pixbuf-2.0-0
```

(WSL2 Ubuntu 24.04 já vem com essas libs por padrão.)

**Fedora / RHEL:**

```bash
sudo dnf install -y cairo pango gdk-pixbuf2
```

**macOS (Homebrew):**

```bash
brew install cairo pango gdk-pixbuf
```

Em ambientes minimalistas (containers Alpine, imagens Docker `python:3.12-slim`,
etc), pode também ser necessário instalar `libffi-dev`, `shared-mime-info` e
fonts (ex: `fonts-liberation`). Ver
[documentação oficial do WeasyPrint](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation)
para casos não cobertos acima.

---

## Estrutura do código

```
src/rdo_agent/
├── ingestor/          # Camada 1: ingestão + hashing + evidence manifest
├── parser/            # Camada 1: parser do .txt do WhatsApp
├── temporal/          # Camada 1: resolução de timestamps (hierarquia de 4 fontes)
├── extractor/         # Camada 1: extração de áudio de vídeo + grafo de derivação
├── orchestrator/      # Camada 1: fila de tarefas e coordenação
└── utils/             # logging, config, db helpers
```

Cada módulo tem responsabilidade única e comunica-se apenas via SQLite (padrão blackboard).

---

## Roadmap

### Estado atual: `v1.3-safe-ingestion`

Última release de produto: `v1.3-safe-ingestion` (25/04/2026).

- `v1.0.2` (higiene documental): docs alinhados com código (sem
  mudança de comportamento).
- `v1.0.3` (cleanup): 7 dívidas cosméticas/menores fechadas
  (rename de seção do RDO, deps WeasyPrint, smart_truncate, strip_emoji,
  CSS Vestígio extra, pyMuPDF para validação, threshold adversarial).
- `v1.1` (narrator flexível): streaming via flag `--stream`,
  `MAX_TOKENS` dinâmico por scope com override env, validator com
  severity tiers, detector novo `CONTRACT_RENEGOTIATION`.
- `v1.2` (pipeline resiliente): `PipelineStateManager` (wrapper sobre
  tabela `tasks` — ADR-007) com CLI `pipeline-status` /
  `pipeline-reset`, dedup defensivo de messages via `content_hash`,
  logging JSONL estruturado em `~/.rdo-agent/logs/` com CLI `watch` /
  `stats`, primitivas de resiliência (`CircuitBreaker`, `RateLimiter`,
  `CostQuota`).
- `v1.3` (ingestão segura): parser streaming `iter_chat_messages`
  (RAM bounded em arquivos de centenas de MB), `MediaSource` para
  copy-on-demand de mídia (sem `extractall` up-front), pre-flight
  check com CLI `estimate` (custo/tempo/disco antes de processar),
  tabela `events` REMOVIDA do schema (ADR-006 resolvido).

Para roadmap completo e estado das sprints, ver:

- `docs/PROJECT_CONTEXT.md` — briefing institucional canônico
- `docs/sessions/` — logs cronológicos por sprint/sessão
- `docs/audits/` — auditorias periódicas (baseline 25/04/2026)
- `docs/ADR-001..006.md` — decisões arquiteturais travadas

### Próximos marcos

- **v1.1-web-ui** — Sessão 4: FastAPI + UI Web operacional
- **v2.0-alpha** — Sessão 5: refactoring obra↔canal + consolidador
- **v2.1+** — Sessões 6-7: ledger consolidado, ingestão batch multi-canal

A numeração de Sessões pós-v1.0 está travada em `docs/ADR-005-numeracao-sessoes-pos-v1.md`.

---

## Princípios de desenvolvimento

1. **Integridade primeiro.** Todo arquivo é hasheado antes de ser processado.
2. **Determinismo onde importa.** A Camada 1 é 100% determinística; LLMs ficam nas Camadas 2-3.
3. **Rastreabilidade total.** Todo evento registra suas fontes, os agentes que o tocaram e os hashes.
4. **Comunicação por base.** Agentes nunca chamam outros agentes diretamente; sempre via SQLite.
5. **Reprodutibilidade.** Mesmo zip de entrada + mesma versão do código = mesmo output local.

---

## Licença

Proprietário. Uso exclusivo da Construtora e Imobiliária Vale Nobre Ltda.

---

## Contato

Lucas Ferreira Leite — lucasferleite2-pixel (GitHub)
