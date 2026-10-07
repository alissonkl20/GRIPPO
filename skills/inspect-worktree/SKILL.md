---
id: inspect-worktree
version: 1
description: Explicar status e diff do repositório sem mutações
phase: [CLEAN, MODIFIED, STAGED]
tools:
  - git_status
  - git_diff
  - git_log
requires_clean_conflicts: false
max_steps: 8
---

# inspect-worktree

1. **Input** — pedido do usuário + `phase` + snapshot (branch, paths).
2. **Estruturar** — listar o que mudou; pedir `git_diff` só para paths relevantes.
3. **Operar** — somente tools de leitura.
4. **Executar** — resumir em linguagem clara; não stage/commit.

Nunca chame tools mutáveis nesta skill.
