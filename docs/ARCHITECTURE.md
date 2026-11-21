# Arquitetura

**Mantra:** o modelo interpreta; o Git é a verdade; código determinístico valida,
autoriza, executa e verifica. A LLM nunca é a autoridade de segurança.

## Visão geral

```text
 usuário ──► CLI (grippo/cli) ──► Assistant (grippo/agent/assistant.py)
                                     │
             ┌───────────────────────┼────────────────────────────┐
             ▼                       ▼                            ▼
   RepoState (git/state)   Interpreter (llm/interpreter)   Session (agent/session)
   porcelain v2 + fingerprint   │  fast path: router determinístico
                                │  senão: Ollama (JSON schema) → fallback router
                                ▼
                     Interpretation {action, steps[op,args]}
                                │
                                ▼
                     Planner (agent/planner) ── CommitDrafter (llm/commit)
                     expande intenções de alto nível usando o estado real
                                │  Plan (passos normalizados)
                                ▼
                     GitExecutor.prepare (engine/executor)
                     risco + impacto + erros + política  ──► PreparedPlan
                                │
                     confirmação do usuário (cli/confirm) ── Decision.CONFIRM
                                │
                     GitPermissionManager.grant ──► Authorization (HMAC, uso único)
                                │
                     GitExecutor.execute
                       lock exclusivo do repositório (git/lock)
                       re-snapshot → revalida args → confere autorização × estado atual
                       por passo: check → ticket → run_git → snapshot → verify → audit
                                │
                                ▼
                     PlanResult (done | failed | conflict | interrupted | timeout | …)
```

## Módulos

| Pacote | Responsabilidade |
|---|---|
| `grippo/cli/` | argparse (`main.py`), REPL (`interface.py`), confirmação (`confirm.py`), renderização rich (`renderer.py`). Sem regra de negócio. |
| `grippo/agent/` | `router.py` (intenções pt/en determinísticas), `planner.py` (intenções → plano), `session.py` (memória limitada), `assistant.py` (um turno completo; usado pela CLI e pela API). |
| `grippo/llm/` | `client.py` (Ollama: pool HTTP, timeouts, JSON schema, 1 inferência por vez, métricas), `interpreter.py`, `commit.py` (seleção de arquivos + mensagem), `prompts.py`. |
| `grippo/engine/` | `operations.py` (catálogo de 57 operações: parâmetros, argv, risco, impacto, pré-condições, verificação), `plan.py`, `permissions.py` (`GitPermissionManager`), `executor.py`, `audit.py`. |
| `grippo/git/` | `runner.py` (único ponto que executa `git`), `validate.py` (refs, revisões, caminhos, URLs), `repo.py` (descoberta), `state.py` (snapshot + fingerprint), `reader.py` (consultas de leitura), `lock.py` (lock leitores/escritor por repositório). |
| `grippo/manage/` | `doctor`, `bench`, `models`, `update`, `uninstall`, `resources` (medições /proc + nvidia-smi), `install_layout`. |
| `grippo/api/` | API HTTP opcional (`grippo[api]`), protegida por token, sem endpoint de shell. |
| `grippo/skills/` | guias `SKILL.md` empacotados (documentação de fluxos; não impõem segurança). |
| `grippo/config.py`, `paths.py`, `logs.py`, `runtime.py` | configuração (TOML + env), diretórios XDG, logging com redação, composição dos componentes. |

## Decisões

- **Catálogo tipado em vez de argv vindo da LLM.** A LLM escolhe `op` + `args`; cada
  parâmetro tem tipo (`rev`, `refname`, `paths`, `url`…) validado antes de virar argv.
  Caminhos sempre após `--`; nenhum valor começa com `-`; URLs `ext::`/`fd::` bloqueadas.
- **Defesa em profundidade no runner.** Mesmo que um bug gere argv indevido: subcomando
  em allowlist (impede `-c`, `-C`, aliases), flags perigosas rejeitadas
  (`--upload-pack`, `--exec`, `--output`, modos interativos), e qualquer comando não
  comprovadamente de leitura exige um `MutationTicket` de uso único emitido pelo executor.
- **Classificação de leitura conservadora.** `branch`, `tag`, `stash`, `remote`,
  `config`, `clean`… só são leitura em formas específicas (`--list`, `-n`, `--get`).
- **Autorização vinculada ao estado.** Fingerprint = HEAD + branch + upstream + saída do
  porcelain v2 + operação em andamento + stashes + mtime/tamanho dos arquivos alterados.
  Para `clean`, a lista de arquivos que seria apagada também entra no vínculo.
- **Lock por repositório via `flock`.** Funciona entre threads e entre processos
  `grippo`; chave = diretório Git comum (worktrees do mesmo repositório se serializam;
  repositórios diferentes nunca se bloqueiam). Leituras compartilhadas; escrita exclusiva;
  reentrante; upgrade compartilhado→exclusivo é recusado (evita autodeadlock).
- **Subprocessos.** `stdin` fechado, `GIT_TERMINAL_PROMPT=0`, `GIT_EDITOR=true`,
  `ssh -o BatchMode=yes`, grupo de processos próprio; timeout/Ctrl-C/saída do programa
  encerram o grupo inteiro (TERM → KILL), sem órfãos.
- **Síncrono.** O trabalho é sequencial por natureza (uma conversa, uma operação por vez
  por repositório); asyncio adicionaria complexidade sem ganho e o modelo anterior
  bloqueava o event loop com chamadas síncronas.
- **Ollama como backend único.** Ver [LLM.md](LLM.md).

## Fluxo de um commit

1. `git status --porcelain=v2 -z --untracked-files=all` → `RepoState`.
2. Interpretação → `commit_changes{request}`.
3. Planner: se já há algo staged, comita só o staged; senão `CommitDrafter` escolhe os
   arquivos relacionados ao pedido (nunca sensíveis; pedido específico sem correspondência
   → pergunta, nunca "tudo") e a mensagem Conventional Commit.
4. Plano `add(paths) → commit(message)`; pré-visualização com arquivos não incluídos.
5. Confirmação → autorização → execução sob lock → verificação (HEAD mudou, assunto
   confere, staged esvaziou) → auditoria.

## Estados de saída

`done` (verificado) · `failed` (com dica: identidade, hook, rejeição, autenticação…) ·
`conflict` (estado intermediário explícito: arquivos em conflito e `*_continue`/`*_abort`)
· `interrupted` · `timeout` · `rejected` (pré-condição falhou antes de executar) ·
`skipped` (passos após uma falha).
