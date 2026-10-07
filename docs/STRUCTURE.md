# Estrutura do repositório (FastAPI)

```
GRIPPO/
├── app/                          # API + harness
│   ├── main.py                   # FastAPI + uvicorn entry
│   ├── config.py                 # GRIPPO_* settings
│   ├── api/
│   │   ├── router.py
│   │   └── routes/
│   │       ├── health.py
│   │       ├── repo.py           # GET /api/v1/repo/snapshot
│   │       ├── skills.py         # list / get SKILL.md
│   │       └── shell.py          # git via subprocess
│   └── harness/
│       ├── agent/                # agent loop (Ollama depois)
│       ├── llm/
│       ├── policy/
│       ├── shell/                # run_shell (git por padrão)
│       ├── skills/               # loader + registry
│       ├── tools/                # git_add, git_commit, …
│       └── worktree/             # RepositorySnapshot
├── skills/                       # skills semânticas (SKILL.md)
├── docs/
│   ├── NEURAL_MAP.md
│   └── diagrams/GRIPPO-neural-map.excalidraw
├── tests/
└── pyproject.toml
```

Futuro: `app/harness/neural/` (`router.py`, `lexicon.yaml`) para camadas A/B do mapa neural — ver [NEURAL_MAP.md](./NEURAL_MAP.md).

## Shell

O harness executa comandos com `subprocess` no `cwd` do repositório:

- Por padrão só aceita `git` em `/api/v1/shell/run`.
- `GRIPPO_ALLOW_ARBITRARY_SHELL=1` libera outros binários (dev apenas).

O LLM **não** chama `/shell` diretamente no desenho final; o agent loop usará `harness/tools/git.py`. A rota shell existe para debug e integração.

## Rodar localmente

```bash
cd GRIPPO
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8765
```

- Swagger: http://127.0.0.1:8765/docs
- Snapshot: `GET /api/v1/repo/snapshot`
- Skills: `GET /api/v1/skills`
- Git rápido: `GET /api/v1/shell/git?args=status%20--short`
