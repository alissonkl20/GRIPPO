---
id: semantic-commit
version: 1
description: Propor Conventional Commit e git commit após aprovação do usuário
phase: [MODIFIED, STAGED]
tools:
  - status
  - diff
  - add
  - commit
requires_clean_conflicts: true
max_steps: 10
---

# semantic-commit

1. **Input** — “commita isso”, escopo opcional.
2. **Estruturar** — se nada staged, stage mínimo necessário; ler `diff` (staged).
3. **Operar** — proposta de mensagem `type(scope): subject` (feat, fix, docs, chore, …).
4. **Executar** — **só** chame `commit` após confirmação explícita do usuário.

Um assunto por commit. Se o diff mistura feat + refactor, sugira dois commits.

Exemplo mensagem: `feat(auth): add login flow`
