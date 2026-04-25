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

Amostra de referência: `docs/brand/Laudo-Real-EVERALDO-v1.0.pdf`
(50 páginas, gerada 100% a partir de dados reais do corpus piloto).

### Dependências de sistema (WeasyPrint)

No Ubuntu/Debian mínimo, WeasyPrint pode exigir:

```bash
sudo apt install -y libpango-1.0-0 libpangoft2-1.0-0 \
                    libcairo2 libgdk-pixbuf-2.0-0
```

(Em WSL2 Ubuntu 24.04 já vem por padrão.)

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

### Estado atual: `v1.0.1-markdown-fix`

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
