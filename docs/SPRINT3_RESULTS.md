# Sprint 3 — Resultados (fechamento de código)

- **Sprint:** 3 (Classificação semântica + revisão humana)
- **Código:** fases 1–4 implementadas
- **Execução em produção:** Fase 1 ✅ rodada; Fases 2–4 aguardam ação manual de Lucas
- **Repo HEAD:** após Op3, `main` sincronizado com `origin/main`
- **ADRs:** ADR-001 (modelo transcrição), ADR-002 (schema classifications)

## Distribuição observada — Fase 1 em produção

Detector de qualidade rodou em 2026-04-20 contra vault EVERALDO (105 transcrições):

| Status pós-detector | Contagem | % |
|---|---:|---:|
| `pending_classify` (coerente)     | 72 | 68.6% |
| `pending_review` (suspeita+ilegível) | 33 | 31.4% |
| **Total**                          | 105 | 100% |

Calibração manual esperava ~30 (28.6%) em `pending_review`. Observado 33 (31.4%) — dentro da tolerância (±3%). Custo real: **US$ 0.0115** (projeção era ~US$ 0.03).

## Distribuição de categorias — projeção (Fase 3 ainda não rodada)

Projeção baseada em calibração manual de 31 transcrições estratificadas (seed=42):

| Categoria | Projeção em 105 | % projetado |
|---|---:|---:|
| `ilegivel` | ~30 | 28.6% |
| `negociacao_comercial` | ~17 | 16.2% |
| `pagamento` | ~14 | 13.3% |
| `cronograma` | ~14 | 13.3% |
| `especificacao_tecnica` | ~10 | 9.5% |
| `solicitacao_servico` | ~10 | 9.5% |
| `material` | ~4 | 3.8% |
| `reporte_execucao` | ~3 | 2.9% |
| `off_topic` | ~3 | 2.9% |

Distribuição real só pode ser verificada depois que Lucas rodar `rdo-agent classify` contra vault (pós-revisão humana).

## Limitações conhecidas

1. **WER ≈ 46% no Whisper baseline.** Transcrições de sotaque mineiro rural têm erros sistemáticos ("MIG" → "amiga", "ripa" → "repa"). O detector da Fase 1 tolera esses erros se o sentido ainda for recuperável; casos degradados viram `pending_review`.
2. **Multi-label rudimentar.** Limitado a 2 categorias, primary-first. Casos de tripla interseção (ex.: `negociacao_comercial` + `pagamento` + `cronograma`) colapsam para as 2 mais salientes.
3. **PDF depende de weasyprint + libs de sistema.** `weasyprint` foi instalado via `pip install weasyprint` durante a sessão autônoma, **mas NÃO foi adicionado a `pyproject.toml`**. Para reproduzir em outra máquina:
   ```
   pip install weasyprint
   # ou adicione a pyproject.toml antes de `pip install -e .`
   ```
   Se weasyprint ou suas dependências C (Pango/Cairo/GObject) estiverem ausentes, o script gera **apenas markdown** (aviso em stderr).
4. **Fases 2–3 não rodadas em produção.** O caminho completo ainda precisa da ação de Lucas:
   - **Fase 2** (~1h): `rdo-agent review --obra EVERALDO_SANTAQUITERIA` — 33 itens para revisar.
   - **Fase 3** (~$0.30, ~5 min): `rdo-agent classify --obra EVERALDO_SANTAQUITERIA` — depois da Fase 2.
   - **Fase 4** (imediato): `python scripts/generate_rdo_piloto.py --obra EVERALDO_SANTAQUITERIA --data <YYYY-MM-DD>` — depois da Fase 3.

## Como rodar o RDO piloto

```bash
cd ~/projetos/rdo-agent && source .venv/bin/activate

# Gera reports/rdo_piloto_EVERALDO_SANTAQUITERIA_2026-04-08.md
# e reports/rdo_piloto_EVERALDO_SANTAQUITERIA_2026-04-08.pdf
python scripts/generate_rdo_piloto.py \
    --obra EVERALDO_SANTAQUITERIA \
    --data 2026-04-08
```

Parâmetros:

- `--obra` (obrigatório): CODESC da obra (ex.: EVERALDO_SANTAQUITERIA)
- `--data` (obrigatório): YYYY-MM-DD
- `--output-dir` (opcional, default `reports/`)

Exit codes:
- `0` — sucesso (markdown + PDF gerados)
- `1` — zero classifications para a data informada
- `2` — banco `index.sqlite` não encontrado na vault

## Qualidade do código (métricas do sprint)

- Testes novos Sprint 3: 44 (10 Fase 2 + 23 Fase 3 + 11 Fase 4)
- Testes totais do projeto: 193+ (era 149 antes da Sprint 3)
- `ruff check` limpo em todos os módulos novos da Sprint 3
- Zero erros novos em arquivos pré-existentes (cli.py mantém os 7 warnings pré-Sprint-3 aceitos pelo escopo)

## Próximos passos (para Lucas na manhã do 2026-04-21)

1. **Rodar `rdo-agent review --obra EVERALDO_SANTAQUITERIA`** e processar as 33 revisões (~1h, áudio à mão).
2. **Rodar `rdo-agent classify --obra EVERALDO_SANTAQUITERIA`** para classificar os pending_classify (~$0.30).
3. **Escolher um dia-piloto** (sugestão: 2026-04-08, que tem maior densidade de classificações segundo a calibração) e rodar `python scripts/generate_rdo_piloto.py`.
4. **Validação Q1/Q2** do plano da Sprint 3: revisar amostra de 30 classificações automáticas, calcular acerto.
5. **Se tudo verde**, criar tag `v0.3.0-sprint3` (a tag `v0.3.0-sprint3-code` foi criada só para o código desta sessão).

## Adendo Sprint 4 Op5 — RDO estendido para multi-source e multi-label (2026-04-22)

O script `generate_rdo_piloto.py` foi estendido na sessao autonoma Sprint 4:

- **Multi-label real:** evento com 2 categorias aparece em AMBAS secoes
  (antes: so primary). Secoes secundarias anotadas com "(primary em ...)".
- **`--modo-fiscal`:** flag opcional que omite a secao "Eventos fora de
  escopo (off-topic)" para entregas a fiscalizacao contratual.
- **Resumo numerico:** secao "Resumo do dia" agora inclui contagem por
  source (audios, text_messages, imagens, documentos) e por categoria
  primary (cronograma: 6, pagamento: 3, etc.).
- **Tags de rastreabilidade:** cada linha de evento recebe tag por source:
  `[ÁUDIO]`, `[TEXTO]`, `[IMAGEM]`, `[VIDEO-FRAME]`, `[PDF]`.
- **Multi-source:** query estendida para incluir `text_message` (via
  `messages.content`), `visual_analysis` (concatena campos Vision) e
  `document` (via `documents.text`); timestamps resolvidos por source.

Testes novos: 6 (17 totais no arquivo). Suite total do projeto: 212.
