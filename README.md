# GRIPPO

**Harness de agente Git local para modelos pequenos (Ollama, ~3–4B).**

O GRIPPO ajuda no fluxo do dia a dia com **linguagem natural e contexto semântico do repositório** — não substitui o Git nem abre shell livre. O harness lê o estado real (`git`), expõe **ferramentas estruturadas** e **skills** que ensinam o modelo *como* agir em cada tarefa.

Em **~4 GB de RAM**, o Ollama não “pensa” o Git inteiro: usa um [**mapa neural simbólico**](./docs/NEURAL_MAP.md) — intenção leve, visão rápida do repo, busca na skill certa e só então um **micro-plano** (poucos tokens). Diagrama: [GRIPPO-neural-map.excalidraw](./docs/diagrams/GRIPPO-neural-map.excalidraw).

## Ideia inicial (MVP)

Você abre o GRIPPO no repositório e pede coisas simples, por exemplo:

- “adiciona só os arquivos de auth” → **`git add`**
- “commita isso” / “faz um commit semântico” → **`git commit`**
- “manda pro remoto” → **`git push`**
- “guarda minhas mudanças e limpa a árvore” → **`git stash`**
- “cria branch `feature/login`” → **`git checkout -b`**
- “volta pra `main`” → **`git checkout` / troca de branch**

O modelo **interpreta e planeja**; o harness **valida e executa**. Mensagens de commit e lista de arquivos são **propostas** até você confirmar.

```
                 ┌───────────────────────┐
                 │        GRIPPO         │
                 │    Git Agent Harness  │
                 └───────────┬───────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
         Repository       Git Tools       Skills
            State          (add, …)      semânticas
              │              │              │
              └──────────────┼──────────────┘
                             │
                      LLM local (Ollama)
                             │
              Input → estruturar → operar → executar
                             │
                           Git
```

## Princípio

**LLM decide. Git verifica. Harness executa.**

- Estado do repo vem sempre do Git (snapshot JSON), não do texto do modelo.
- Mutations passam por tools fixas (`git_stage`, `git_commit`, …), não por comandos arbitrários.
- Skills carregam o “como fazer” de cada fluxo (Conventional Commits, quando perguntar, quais tools usar).

## Skills semânticas (MVP)

Cada skill é um `SKILL.md` em `skills/<nome>/` — é o jeito de **treinar o harness** sem encher o prompt com o manual inteiro do Git. Detalhes: [docs/SKILLS.md](./docs/SKILLS.md).

| Skill | Operação Git | Exemplo de pedido |
|-------|----------------|-------------------|
| `stage-changes` | `git add` | “stage só o README”, “adiciona tudo menos tests” |
| `semantic-commit` | `git commit` | “commita isso”, “commit semântico das mudanças staged” |
| `push` | `git push` | “push na branch atual”, “envia pro origin” |
| `stash-save` | `git stash` | “stash com mensagem”, “guarda mudanças locais” |
| `stash-restore` | `git stash pop/apply` | “recupera o último stash” |
| `branch-create` | `git checkout -b` | “cria branch feature/x a partir da atual” |
| `branch-switch` | `git checkout` | “muda para main”, “checkout develop” |
| `inspect-worktree` | (leitura) | status, diff, explicar mudanças |

## CLI (visão)

```bash
grippo                          # sessão interativa (skills conforme intenção)
grippo status                   # snapshot humano (sem LLM)
grippo add "só src/auth"        # skill stage-changes
grippo commit                   # semantic-commit + confirmação
grippo push
grippo stash                    # stash-save / restore conforme frase
grippo branch                   # criar ou trocar branch
grippo doctor                   # repo saudável? conflitos? detached?
```

## Filosofia

| Princípio | Descrição |
|-----------|-----------|
| Local first | Ollama na máquina; contexto compacto para pouca RAM |
| Git is the source of truth | Snapshot e diffs vêm do harness |
| Small model, strong harness | Skills + estado estruturado no lugar de memorizar Git |
| Skills composáveis | Uma skill por fluxo; você adiciona e versiona no repo |
| Human in the loop | `commit` e `push` com preview antes de executar |
| Safe by default | Sem shell; paths validados; policy em operações arriscadas |

## Documentação

- [ARCHITECTURE.md](./docs/ARCHITECTURE.md) — runtime, tools, agent loop, policy
- [SKILLS.md](./docs/SKILLS.md) — como escrever skills semânticas para o MVP
- [STRUCTURE.md](./docs/STRUCTURE.md) — pastas `app/harness`, API FastAPI, shell
- [NEURAL_MAP.md](./docs/NEURAL_MAP.md) — ativação esparsa A→D, L1/L2, 4 GB + Ollama

## Desenvolvimento (FastAPI)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8765
```

- API: http://127.0.0.1:8765/docs  
- `GET /api/v1/repo/snapshot` — estado do repo  
- `GET /api/v1/skills` — skills carregadas de `skills/`  
- `POST /api/v1/shell/run` — executa `git` no repo (subprocess)

Variáveis: ver [.env.example](./.env.example). Shell livre só com `GRIPPO_ALLOW_ARBITRARY_SHELL=1`.

## Roadmap

| Etapa | Conteúdo |
|-------|----------|
| **MVP** | Snapshot, tools de add/commit/push/stash/branch/checkout, loop + Ollama, skills da tabela acima |
| **Depois** | pull, divergência ahead/behind, merge/rebase, conflitos, recovery, policy avançada, plugins |

## O que o GRIPPO não é

IDE, agente de código geral, chatbot sem Git, nem wrapper de shell. Foco: **add, commit, push, stash, branches** e leitura do worktree — com skills que você controla.

## Status

🚧 **MVP em andamento.** Harness em Python (FastAPI), skills em `skills/`, Git via shell controlado.

## Contributing

Contribuições: parsers de estado Git, providers Ollama, agent loop, **novas skills semânticas**, testes com repos temporários, benchmarks com modelos 3B–4B Q4.

## License

TBD.

**GRIPPO** — *Your local Git agent.* Understand. Plan. Execute. Verify.
