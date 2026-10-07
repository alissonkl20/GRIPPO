---
id: push
version: 1
description: Enviar branch atual ao remoto (git push)
phase: [CLEAN, STAGED, COMMITTED, AHEAD]
tools:
  - git_status
  - git_push
requires_clean_conflicts: true
max_steps: 6
---

# push

1. **Input** — “push”, remote opcional (default `origin`).
2. **Estruturar** — branch atual, upstream, ahead/behind se disponível no snapshot.
3. **Operar** — `git_push` sem `--force` salvo policy/approval.
4. **Executar** — mostrar remote + branch antes de push; reportar stderr se falhar.

Se não houver commits à frente do remoto, explique em vez de repetir push à toa.
