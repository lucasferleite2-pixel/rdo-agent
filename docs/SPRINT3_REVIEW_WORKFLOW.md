# Sprint 3 — Fluxo de Revisão Humana (Camada 2)

- **Escopo:** transcrições marcadas pelo detector de qualidade como `suspeita` ou `ilegivel` (`classifications.semantic_status='pending_review'`).
- **Custo:** zero financeiro; ~2 min/transcrição de operador.
- **Entrada canônica:** `rdo-agent review --obra <CODESC>`

## Visão geral

```
                   (Sprint 3 Fase 1 — detector rodou)
                                │
                                ▼
                   classifications com
                   semantic_status = 'pending_review'
                                │
            ┌───────────────────┼───────────────────┐
            │                   │                   │
            ▼                   ▼                   ▼
      [E]ditar             [A]ceitar           [R]ejeitar
      (tmp + $EDITOR)      (sem alterar texto) (áudio inútil)
            │                   │                   │
            ▼                   ▼                   ▼
  human_corrected_text  human_reviewed=1    human_reviewed=1
  = <texto corrigido>   semantic_status     semantic_status
  human_reviewed=1      = 'pending_classify'= 'rejected'
  semantic_status              │                   │
  = 'pending_classify'         │                   │
            │                   │                   │
            └──────────┬────────┘                   │
                       ▼                            ▼
                  (Fase 3)                    (não entra no RDO;
           classificador semântico             auditoria mantida)
```

## Ações (teclas)

| Tecla | Ação | Transição | Grava `human_corrected_text`? |
|---|---|---|---|
| `E` | Abre $EDITOR (fallback `nano`) com texto atual | → `pending_classify` | sim |
| `A` | Aceita texto original como está | → `pending_classify` | não |
| `R` | Rejeita: áudio inaproveitável | → `rejected` | não |
| `S` | Pula (mantém estado) | — | não |
| `Q` | Sai salvando progresso | — | não |

Todas as ações que não sejam `S`/`Q` gravam também `human_reviewed=1` e `human_reviewed_at` (ISO-8601 UTC).

## Como ouvir o áudio

A CLI exibe o `audio_path` (ex.: `10_media/00000097-AUDIO-....opus`) relativo à raiz da vault. Abra manualmente com seu player preferido (`mpv`, `ffplay`, VLC). Player embutido é deliberadamente fora de escopo (overbuild).

## Retomando depois do `Q`

O comando é idempotente: pode sair com `Q` a qualquer momento e voltar depois. A query seleciona apenas linhas em `pending_review` — linhas já processadas não reaparecem.

## Verificando progresso

```sql
SELECT semantic_status, COUNT(*)
FROM classifications
WHERE obra = '<CODESC>'
GROUP BY semantic_status;
```

## Decisão de design

- **Sem player embutido:** mostrar path e deixar o operador escolher o player. Reduz superfície de manutenção e falhas dependentes de sistema.
- **Injeção de callbacks (`prompt_fn`, `edit_fn`, `print_fn`):** permite testes sem TTY/subprocess real.
- **Commit por linha, não em batch:** `Ctrl+C` ou crash no meio não perde progresso.
