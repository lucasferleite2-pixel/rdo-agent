# Guia de Desenvolvimento

Este documento é tanto para humanos quanto para Claude Code — descreve convenções, estrutura e como desenvolver a Sprint 1 em diante.

## Estado atual

**Scaffold Sprint 1 — stubs criados, implementação pendente.**

Todos os módulos em `src/rdo_agent/` têm assinaturas, docstrings e `raise NotImplementedError` onde há trabalho a fazer. A ordem recomendada de implementação:

1. `utils/hashing.py` — ✅ já implementado (tem testes passando)
2. `utils/config.py` — ✅ já implementado
3. `utils/logging.py` — ✅ já implementado
4. `orchestrator/__init__.py` — **próximo** — criar schema do SQLite e funções básicas
5. `ingestor/__init__.py` — usa hashing + orchestrator
6. `parser/__init__.py` — parser do `_chat.txt`
7. `temporal/__init__.py` — depende do parser
8. `extractor/__init__.py` — usa ffmpeg, depende do resolver temporal
9. Conectar tudo no CLI (`cli.py`)

## Convenções de código

- **Python 3.11+** com type hints completos (`from __future__ import annotations` no topo de cada arquivo)
- **Black** + **Ruff** para formatação (`black .` e `ruff check .`)
- **pytest** para testes — cobertura mínima: happy path + 1 erro comum por função
- **Dataclasses** para estruturas de dados (preferir sobre dict/TypedDict)
- **pathlib.Path** em vez de `os.path`
- **Docstrings estilo Google** em toda função pública

## Princípios arquiteturais (não violar)

1. **Camada 1 é determinística.** Nenhuma chamada a LLM nesta pasta. Se rodar duas vezes sobre o mesmo input, output idêntico byte-a-byte.
2. **Comunicação por base.** Módulos não se chamam diretamente — escrevem no SQLite e o orchestrator coordena.
3. **Hashes primeiro.** Qualquer arquivo que entra no sistema é hasheado antes de qualquer outra operação.
4. **Rastreabilidade.** Toda tabela do SQLite tem `created_at` e referências às origens.
5. **Isolamento por obra.** Nada cruza a fronteira entre CODESCs diferentes.

## Fixture para desenvolvimento

Coloque um zip real exportado do WhatsApp em `fixtures/real_CODESC_75817/` (pasta ignorada pelo git).

Para criar uma fixture sintética testável sem dados reais:

```bash
# TODO: script que gera um zip fake com _chat.txt plausível + mídias sintéticas
python scripts/generate_fake_fixture.py
```

## Rodando os testes

```bash
source .venv/bin/activate
pytest                          # todos os testes
pytest tests/test_hashing.py   # arquivo específico
pytest -v -k "sha256"           # por nome
pytest --cov=rdo_agent          # com cobertura
```

## Fluxo Git sugerido

- `main` → branch estável
- Features em branches `sprint-1/ingestor`, `sprint-1/parser`, etc.
- Commit messages no formato:
  - `feat(ingestor): implementa cálculo de hash do zip`
  - `test(parser): adiciona casos de mensagens multi-linha`
  - `fix(temporal): corrige parsing de filename com padrão IMG_`
  - `docs: atualiza CONTRIBUTING.md`

## Para Claude Code

Quando você (Claude Code) abrir este projeto:

1. **Leia primeiro:** `README.md`, este arquivo, e `docs/Blueprint_V3.docx` (se disponível)
2. **Rode os testes:** `pytest` — deve passar 7 testes (hashing + config)
3. **Escolha um módulo** da lista de ordem recomendada acima
4. **Leia o docstring completo** do módulo antes de implementar — ele contém o pipeline esperado
5. **Escreva testes antes ou junto** — não depois
6. **Commit frequente** — um commit por função implementada e testada

Princípios para você seguir:

- **Não invente requisitos** — se algo não está claro no docstring ou no blueprint, pergunte ao Lucas
- **Prefira simples** — SQLite cru é ok, não precisa de SQLAlchemy nesta fase
- **Handle erros explicitamente** — nada de `except: pass`
- **Log estruturado** — use o `get_logger()` do `utils/logging.py`
