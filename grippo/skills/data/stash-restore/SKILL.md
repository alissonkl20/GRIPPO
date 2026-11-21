---
id: stash-restore
version: 1
description: Recuperar stash (pop ou apply)
phase: [CLEAN, MODIFIED]
tools:
  - status
  - stash_list
  - stash_pop
  - stash_apply
requires_clean_conflicts: true
max_steps: 8
---

# stash-restore

1. **Input** — “último stash”, índice, ou pop vs apply.
2. **Estruturar** — `stash_list`; default índice `0`.
3. **Operar** — `stash_pop` se o usuário quer remover da pilha; senão `stash_apply`.
4. **Executar** — avisar risco de conflito se o worktree não estiver limpo.

“Recupera o stash” → pop no índice 0 salvo pedido contrário.
