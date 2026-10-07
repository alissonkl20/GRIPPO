---
id: stage-changes
version: 1
description: Mapear intenção do usuário para git add (git_stage)
phase: [MODIFIED, STAGED]
tools:
  - git_status
  - git_diff
  - git_stage
requires_clean_conflicts: true
max_steps: 8
---

# stage-changes

1. **Input** — “só X”, “tudo menos Y”, ou lista explícita.
2. **Estruturar** — cruzar pedido com `worktree.modified`, `untracked`, `added`.
3. **Operar** — `git_stage` com `files[]` validados (existem no snapshot).
4. **Executar** — confirmar paths staged no feedback.

Não inclua `.env`, `*.pem`, chaves ou credenciais nos paths.

Exemplo: `{"name":"git_stage","arguments":{"files":["src/auth.ts"]}}`
