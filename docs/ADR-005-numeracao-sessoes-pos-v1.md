# ADR-005 — Numeração de Sessões pós-v1.0

**Data:** 25/04/2026
**Status:** ACEITO
**Sprint:** Higiene Documental (`v1.0.2-docs-sync`)
**Referência:** `docs/audits/AUDIT_2026-04-25_state_of_roadmap.md` (inconsistência #6)

## Contexto

A auditoria de 25/04/2026 detectou ambiguidade na numeração de Sessões
no `docs/PROJECT_CONTEXT.md`:

- Seção 7.2 mapeava **"SESSÃO 4"** → `v1.1-web-ui` (UI Web).
- Seção 7.3 mapeava **"SESSÃO 4"** → `v2.0-alpha` (Consolidador multi-canal).

O mesmo rótulo carregava trabalhos diferentes em duas seções, criando
risco real de confusão na próxima execução autônoma — qual Sessão 4
seria disparada por um prompt como "rode a Sessão 4"?

Adicionalmente, a numeração das Sessões 1-3 já estava parcialmente
desalinhada com a numeração das Sprints/Fases (Sessão 1 = Fase C +
6 dívidas; Sessão 2 = Fase D+E + 5 dívidas; Sessão 3 = Laudo Vestígio;
Sessão 3.8 = Markdown fix). Não existe uma "Sessão 4" pré-existente —
o número estava livre.

## Decisão

**Sessão 4 = UI Web (v1.1-web-ui)**.

Justificativa de prioridade:

1. **Continuidade temporal.** A UI Web é o sucessor natural da Sessão
   3.8 (último marco terminado). Os ativos visuais já estão prontos
   em `docs/brand/design-skill/` e `src/rdo_agent/web/static/`; só
   falta o app FastAPI + tradução das views React→Jinja.
2. **Risco baixo, valor visível.** Não envolve breaking changes. A
   próxima sessão entrega coisa palpável (dashboard funcionando) sem
   tocar pipeline forense.
3. **Consolidador exige refactoring semântico.** O trabalho de
   "obra↔canal" é breaking change no schema e no código — merece o
   bump de major version (v2.0). Forçar isso para v1.1 misturaria
   dois eixos de mudança numa janela só.

Em consequência, todo o roadmap pós-v1.0 desloca um número:

| Antes | Depois | Marco |
|---|---|---|
| Sessão 4 (UI Web) | **Sessão 4** | `v1.1-web-ui` |
| Sessão 4 (Consolidador) | **Sessão 5** | `v2.0-alpha` |
| Sessão 5 (Divergências inter-canais) | **Sessão 6** | `v2.1` |
| Sessão 6 (Full production batch) | **Sessão 7** | `v2.2-full-production` |

## Consequências

- `docs/PROJECT_CONTEXT.md` seção 7 reescrita conforme esta numeração
  (commit da Sprint de Higiene).
- Próxima execução autônoma que receber "rode a Sessão 4" entende sem
  ambiguidade: UI Web.
- SESSION_LOG da Sessão 4, quando criado, deve referenciar este ADR.
- O trabalho de Consolidador NÃO foi cancelado — só renumerado e
  realocado para depois da UI Web.

## Notas para sessões futuras

A numeração é ordinal (sequencial, na ordem de execução), **não**
temática. Se uma sessão futura precisar quebrar essa ordem (ex: bug
crítico que vire Sessão 4.5), seguir a convenção decimal já usada na
Sessão 3.8 — sufixo decimal preserva a ordem ordinal sem renumerar
tudo de novo.
