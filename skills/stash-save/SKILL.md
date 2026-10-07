---
id: stash-save
version: 1
description: Guardar mudanças locais com git stash push
phase: [MODIFIED, STAGED]
tools:
  - git_status
  - git_stash_push
requires_clean_conflicts: true
max_steps: 6
---

# stash-save

1. **Input** — mensagem opcional do stash.
2. **Estruturar** — listar paths que entrarão no stash a partir do snapshot.
3. **Operar** — `git_stash_push` com `message` se o usuário pediu.
4. **Executar** — confirmar worktree limpa ou estado após stash.

Avise se não há nada para guardar.
