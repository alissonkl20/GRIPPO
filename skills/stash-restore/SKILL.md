---
id: stash-restore
version: 1
description: Recuperar stash (pop ou apply)
phase: [CLEAN, MODIFIED]
tools:
  - git_status
  - git_stash_list
  - git_stash_pop
  - git_stash_apply
requires_clean_conflicts: true
max_steps: 8
---

# stash-restore

1. **Input** — “último stash”, índice, ou pop vs apply.
2. **Estruturar** — `git_stash_list`; default índice `0`.
3. **Operar** — `git_stash_pop` se o usuário quer remover da pilha; senão `git_stash_apply`.
4. **Executar** — avisar risco de conflito se o worktree não estiver limpo.

“Recupera o stash” → pop no índice 0 salvo pedido contrário.
