<div align="center">

**🌐 English · [Português (Brasil)](README.pt-BR.md)**

# 🐆 GRIPPO

**Your local Git assistant, in natural language — with the LLM running on your machine.**

Say what you want to do. GRIPPO reads the real state of the repository, shows the impact,
asks for your confirmation and runs it through a deterministic, audited layer.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Git](https://img.shields.io/badge/git-%E2%89%A5%202.23-orange)
![LLM](https://img.shields.io/badge/LLM-100%25%20local%20(Ollama)-brightgreen)
![Status](https://img.shields.io/badge/status-pre--1.0-yellow)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)

[Install](#installation) · [Usage](#usage) · [AI integration (MCP)](#integration-with-other-ais-mcp) ·
[Safety](#safety) · [Documentation](#documentation) · [Contributing](CONTRIBUTING.md)

</div>

---

```text
❯ quero fazer um commit das alterações do formulário
╭─ operação proposta ──────────────────────────────────────────────╮
│ Repositório  /home/voce/projetos/loja                            │
│ Branch       feature/cadastro  HEAD 3f2a91c0                     │
│ Working tree 0 staged, 1 não staged, 3 não rastreados            │
│ Risco        MODERADO                                            │
│ 1. add  (moderado)                                               │
│   • os arquivos passam para a staging area (index)               │
│   arquivos: src/forms/signup_form.html, src/forms/signup_form.js │
│ 2. commit  (moderado)                                            │
│   argumentos: {'message': 'feat(forms): add signup form'}        │
│ ⚠ não incluídos (parecem não relacionados): scripts/cleanup.sh   │
╰──────────────────────────────────────────────────────────────────╯
[c] confirmar  [n] cancelar  [d] detalhes  [e] editar mensagem:
```

> The interface and the built-in phrase router are currently in **Brazilian Portuguese**
> (the screen above reads: *"I want to commit the form changes"* → proposed operation,
> risk MODERATE, files to stage, one unrelated file left out, confirm/cancel/details/edit).
> English requests work through the local LLM; see [Known limitations](#known-limitations).

## Why GRIPPO?

You already use a coding agent (Claude Code, Cursor, Copilot…) to **implement** the task.
What is missing is someone to take care of **version control**: create the right branch,
pick what goes into the commit, write a decent message, publish without wrecking history
and get you out of a conflict. That is what GRIPPO does — and nothing else.

|  | Plain Git | Coding agents | **GRIPPO** |
|---|:-:|:-:|:-:|
| Natural language | ✗ | ✓ | ✓ |
| 100% local (no code sent to the cloud) | ✓ | ✗ | ✓ |
| Shows impact and how to undo before acting | ✗ | varies | ✓ |
| Confirmation bound to the repository state | ✗ | ✗ | ✓ |
| Audit trail of every operation | ✗ | ✗ | ✓ |
| Works without an LLM (deterministic router) | ✓ | ✗ | ✓ |

## What it does

- **Branches** — create, switch, rename, delete, list (including merged), set upstream.
- **Commits** — picks the files that belong to the change, classifies the type
  (`feat`, `fix`, `docs`, `refactor`…) and proposes a
  [Conventional Commits](https://www.conventionalcommits.org) message. You can edit it.
- **Sync** — fetch, pull, push (sets the upstream for you), tags, remotes.
- **Integration** — merge, rebase, cherry-pick, with conflict detection.
- **Conflicts** — *"how do I resolve the conflict?"* explains **why** it happened (who
  changed what, in which commits, which hunks collide). On *"continue the merge"* it checks
  that no `<<<<<<<` markers remain, then runs `add` + `--continue`. `abort` and `skip` are
  available too. It explains; **you** choose the side.
- **Undo** — `revert`, `reset` (soft/mixed/hard), `restore`, stash, `reflog` — always
  showing the recovery command, with an **automatic backup** before destructive operations.
- **Inspection** — status, diff, log, show, branch comparison.

It is **not** a coding agent: it does not implement features, fix bugs or review code.

## Installation

```bash
git clone https://github.com/alissonkl20/GRIPPO.git
cd GRIPPO
./install.sh            # no sudo; use --add-to-path if ~/.local/bin is not on your PATH
grippo doctor           # diagnostics: Git, Python, Ollama, GPU, PATH
grippo model pull       # downloads the default model (qwen2.5:3b) into Ollama
```

**Requirements:** Linux, Python ≥ 3.11 with `venv`, Git ≥ 2.23 and, for the local LLM,
[Ollama](https://ollama.com) (~2 GB of free VRAM or RAM for the default model).
Without Ollama GRIPPO still works through its router of known phrases (`--no-llm`).
More in [INSTALL.md](INSTALL.md).

## Usage

```bash
cd ~/projects/any-repo          # works in any subdirectory or worktree
grippo                          # interactive mode
grippo ask "crie uma branch feature/login"
grippo --repo ~/other/repo status
```

If you run it in a folder that **contains** repositories (rather than inside one), GRIPPO
lists the ones it found and tells you which command to use.

Example requests (the router currently understands Portuguese phrases):

| You say | GRIPPO proposes |
|---|---|
| "crie uma branch feature/login" (create a branch) | `git switch -c feature/login` |
| "volte para a main" (go back to main) | `git switch main` |
| "faça commit das alterações" (commit my changes) | `git add` (only what belongs to the change) + `git commit` |
| "envie para o remoto" (push) | `git push --set-upstream origin HEAD:refs/heads/<branch>` |
| "atualize minha branch com a main" (update my branch) | asks: merge or rebase? (recommends one) |
| "reverta o commit 1a2b3c4" (revert a commit) | `git revert --no-edit 1a2b3c4` |
| "desfaça o último commit mantendo as alterações" (undo last commit, keep changes) | `git reset --soft HEAD~1` (risk **high** if already published) |
| "continue o merge" | `git add` of the resolved files + `git merge --continue` |
| "aborte o merge" | `git merge --abort` |
| "como resolvo o conflito?" (how do I resolve the conflict?) | explains why it conflicted (does not pick a side) |
| "liste as branches já mescladas" (list merged branches) | `git branch --merged` |

Maintenance commands:

```bash
grippo doctor        # installation diagnostics
grippo bench         # RAM/VRAM/time/tokens per second
grippo config set llm.model qwen2.5:3b
grippo update        # update with validation and rollback
grippo uninstall     # keeps settings, models, logs and audit trail
```

## Workflow with Claude Code

GRIPPO runs **next to** your coding agent, in another terminal opened on the same
repository — or **integrated** through MCP (next section).

```text
 Terminal A — GRIPPO                    Terminal B — Claude Code
 ───────────────────────                ─────────────────────────
 ❯ create branch feat/export-csv
   ✔ branch created and active
                                        > implement the CSV export
                                          … edits files, runs tests …
 ❯ commit
   ◆ analyzes the diff → feat(export): add CSV export
   [c] confirm
 ❯ push
   ✔ remote updated
```

Full step-by-step guide in [docs/GUIA.md](docs/GUIA.md) (Portuguese).

## Integration with other AIs (MCP)

`grippo mcp` turns GRIPPO into an [MCP](https://modelcontextprotocol.io) server: Claude
Code, Cursor, Claude Desktop and others ask GRIPPO for commits, branches and pushes
**without being able to execute them on their own**.

```bash
claude mcp add grippo -- grippo --repo ~/projects/my-repo mcp   # Claude Code
grippo mcp --print-config                                       # other clients (JSON)
```

```text
AI ──► grippo_propose ──► PROPOSAL p-1a2b3c4d (nothing executed)
you ─► grippo approve p-1a2b3c4d ─► see the impact ─► confirm ─► executes
```

- The AI **reads and proposes**; **only you approve**, in an interactive terminal.
- Destructive targets must appear in your request: the AI cannot "invent" the branch to delete.
- No-ops, protected branches and invalid operations are refused before they become proposals.

Details and limits in [docs/MCP.md](docs/MCP.md).

## Safety

1. **The model interprets; Git is the truth; code executes.** The LLM only proposes
   structured intents (`{"op": "revert", "args": {...}}`) — never commands.
2. **Nothing changes without you.** Every operation that modifies the repository shows
   repository, branch, HEAD, files, commits, consequences and how to recover. **High** risk
   (`reset --hard`, `clean`, force push…) requires typing `confirmar`.
3. **Authorization covers what you saw.** It is single-use, expires, and is bound to the
   operation, its arguments and the repository state: if anything changes, it asks again.
4. **No shell shortcuts.** No `shell=True`, allow-listed subcommands, dangerous flags blocked.
5. **Verified result.** Success is reported only after checking the real state.
6. **Nothing gets lost.** Before `reset`, `rebase`, deletions, force push and `restore`,
   the state (including uncommitted tracked changes) is saved under `refs/grippo/backup/…`.
   Untracked files removed by `clean` are *not* backed up — `clean` always lists exactly what it deletes.
7. **No hallucinated targets.** Targets of destructive operations must be in your request;
   vague requests like *"fix it up"* never turn into `reset --hard`. No-ops are refused.

Details in [docs/PERMISSIONS.md](docs/PERMISSIONS.md) and [SECURITY.md](SECURITY.md).

## How it works

```text
request ─► deterministic router ─┐
                                 ├─► operation plan ─► validation ─► confirmation
request ─► local LLM (Ollama) ───┘                                        │
                                                                          ▼
                                 real state ◄── verification ◄── executor (git, no shell)
```

Skills ([docs/SKILLS.md](docs/SKILLS.md)) describe flows such as *semantic-commit*, *push*
and *stash*, teaching the local model how to decide the kind of change and the commit
message. Full architecture in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Documentation

The guides below are written in Portuguese.

| Document | Contents |
|---|---|
| [INSTALL.md](INSTALL.md) · [UPDATING.md](UPDATING.md) · [UNINSTALL.md](UNINSTALL.md) | install, update, remove |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | modules, execution flow, decisions |
| [docs/GUIA.md](docs/GUIA.md) | **step-by-step** usage: from branch to push, conflicts and undo |
| [docs/MCP.md](docs/MCP.md) | using GRIPPO from Claude Code, Cursor and other AIs |
| [docs/PERMISSIONS.md](docs/PERMISSIONS.md) | risk levels, authorization, guards, backups, audit |
| [docs/LLM.md](docs/LLM.md) | local backend, model, memory, measurements and limitations |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | `config.toml` and `GRIPPO_*` variables |
| [docs/TESTING.md](docs/TESTING.md) | test layers |
| [docs/RELEASING.md](docs/RELEASING.md) | versioning and publishing |
| [docs/GIT.md](docs/GIT.md) · [docs/SKILLS.md](docs/SKILLS.md) | Git glossary and skills |
| [CONTRIBUTING.md](CONTRIBUTING.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) · [CHANGELOG.md](CHANGELOG.md) | contributing and history |

## Known limitations

- Small models (3B) misread ambiguous requests; that is why the deterministic router has
  priority and **every** proposal goes through confirmation. Measurements in [docs/LLM.md](docs/LLM.md).
- Tested on Ubuntu 26.04 / Python 3.14 / Git 2.53 / Ollama 0.40. Other distributions, macOS
  and Windows have not been tested yet.
- Portuguese requests are the focus; the built-in router and the UI are in Portuguese, and
  other languages depend on the LLM.

## Roadmap

- [x] MCP server for Claude Code, Cursor and other AIs
- [x] Conflict explanation (why it happened)
- [ ] English UI and phrase router
- [ ] Claude Code hooks (suggest a commit when a task ends)
- [ ] TUI with a live status panel
- [ ] macOS support
- [ ] PyPI release

## Contributing

Contributions are welcome! Read [CONTRIBUTING.md](CONTRIBUTING.md) and run the suite before
opening a PR:

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest            # ~470 tests, no Ollama needed
```

## License

**To be defined** — see [LICENSE](LICENSE). Until the maintainer picks an open-source
license (MIT, Apache-2.0, GPL-3.0…), the code should not be considered free to redistribute.
