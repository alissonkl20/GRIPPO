# GRIPPO — visão do produto

**GRIPPO** é um agent CLI open source de Git para a máquina local. Roda com uma LLM no **Ollama** (quantização **Q4** ou **Q2**, ~3–4B) em cerca de **4 GB de RAM**. Não é um code agent geral: é um assistente **especialista em versionamento**, worktree e fluxos do dia a dia.

Princípio: **o modelo interpreta; o Git é a verdade; o harness executa.**

## Por que modelo pequeno + quantização

As tarefas são estreitas (status → add → commit → push, diff, branch, stash, revert, rebase, conflito). Não precisa de raciocínio longo. O “conhecimento de Git” vive no harness:

| Camada | Onde | Papel |
|--------|------|--------|
| Léxico (L1) | [docs/GIT.md](./docs/GIT.md) | O que cada peça do Git *é* (index, HEAD, worktree, rebase…) |
| Skills (L2) | `skills/*/SKILL.md` | Como executar *um* fluxo (commit semântico, push, stash…) |
| Snapshot | `git status` / tools | Estado real agora |
| Micro-plano | Ollama Q4/Q2 | Escolher skill, paths, mensagem; poucos tokens |
| Execução | tools Git (sem shell livre) | `git add`, `commit`, `push`, … |

Treinar ou especializar o modelo **não** significa colar o manual inteiro no prompt. Significa léxico curto + skills + snapshot. Resumo oficial condensado: [docs/GIT.md](./docs/GIT.md) (fontes: `gitglossary`, `giteveryday`, man pages Git 2.53).

## O que o usuário pede (linguagem natural)

Exemplos:

- “faça commit e push na branch develop” → conferir worktree, `checkout`/`switch` para `develop` se preciso, `git add` se houver mudanças unstaged, commit com mensagem, push (com confirmação).
- “mostra o diff da branch atual com a antiga” → `git diff` / `git log` entre refs.
- “o que mudou desde o último commit?” → status + diff.
- “preciso reverter / rebase / resolver conflito” → skill de recovery (fase 2), sempre com estado lido do Git.

O agente decide se precisa de `git add .` (ou paths específicos), `commit` e `push` a partir do **snapshot**, não de chute.

## Superfícies de aprimoramento

| Peça | Função |
|------|--------|
| **Skills** | Um fluxo por pasta (`semantic-commit`, `push`, `inspect-worktree`, …) |
| **Actions / tools** | Git estruturado: stage, commit, push, stash, branch, worktree |
| **Text / human-in-the-loop** | Preview de mensagem, lista de arquivos e push **antes** de executar |
| **Context** | Snapshot JSON: branch, ahead/behind, staged, untracked, conflitos |
| **Projects** | Repo atual + worktrees ligadas ao mesmo `.git` |
| **Thinking (micro)** | Plano curto: observar → skill → tool; sem cadeia longa de raciocínio |

## Escopo

**MVP:** add, commit semântico, push, stash, criar/trocar branch, inspecionar worktree (status, diff).

**Depois:** comparar commits/branches, pull, ahead/behind, merge, rebase, revert, conflitos, várias worktrees (`git worktree`).

**Não é:** IDE, agente de código genérico, chatbot sem Git, nem wrapper de shell arbitrário.

## Stack (já no repositório)

Python 3.11+, FastAPI (`app/`), harness em `app/harness/`, skills em `skills/`. Ver [README.md](./README.md) e [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md).
