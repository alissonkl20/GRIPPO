---
id: stage-changes
version: 1
description: Mapear intenção do usuário para git add (operação add)
phase: [MODIFIED, STAGED]
tools:
  - status
  - diff
  - add
requires_clean_conflicts: true
max_steps: 8
---

# stage-changes

1. **Input** — “só X”, “tudo menos Y”, ou lista explícita.
2. **Estruturar** — cruzar pedido com `worktree.modified`, `untracked`, `added`.
3. **Operar** — `add` com `files[]` validados (existem no snapshot).
4. **Executar** — confirmar paths staged no feedback.

Não inclua `.env`, `*.pem`, chaves ou credenciais nos paths.

Exemplo: `{"op":"add","args":{"paths":["src/auth.ts"]}}`
