---
id: semantic-commit
version: 1
description: Propor Conventional Commit e git commit após aprovação do usuário
phase: [MODIFIED, STAGED]
tools:
  - git_status
  - git_diff
  - git_diff_cached
  - git_stage
  - git_commit
requires_clean_conflicts: true
max_steps: 10
---

# semantic-commit

1. **Input** — “commita isso”, escopo opcional.
2. **Estruturar** — se nada staged, stage mínimo necessário; ler `git_diff_cached`.
3. **Operar** — proposta de mensagem `type(scope): subject` (feat, fix, docs, chore, …).
4. **Executar** — **só** chame `git_commit` após confirmação explícita do usuário.

Um assunto por commit. Se o diff mistura feat + refactor, sugira dois commits.

Exemplo mensagem: `feat(auth): add login flow`
