---
id: branch-create
version: 1
description: Criar branch com git checkout -b
phase: [CLEAN, MODIFIED, STAGED]
tools:
  - git_status
  - git_branch_create
requires_clean_conflicts: true
max_steps: 5
---

# branch-create

1. **Input** — nome da branch (ex. `feature/login`).
2. **Estruturar** — validar nome (sem espaços, sem `-` ambíguo como flag).
3. **Operar** — `git_branch_create` com `name`.
4. **Executar** — confirmar branch ativa no snapshot após sucesso.

Se há mudanças não commitadas, avise que elas seguem na nova branch.
