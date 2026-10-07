---
id: branch-switch
version: 1
description: Trocar de branch com git checkout
phase: [CLEAN, MODIFIED, STAGED]
tools:
  - git_status
  - git_checkout
requires_clean_conflicts: true
max_steps: 8
---

# branch-switch

1. **Input** — nome da branch destino (ex. `main`, `develop`).
2. **Estruturar** — se worktree suja, sugira stash ou commit antes de trocar.
3. **Operar** — `git_checkout` com `branch`.
4. **Executar** — não use checkout forçado de paths; não descarte mudanças sem approval.

Se o checkout falhar por mudanças locais, explique e ofereça `stash-save`.
