# GRIPPO — Skills semânticas

Skills ensinam o harness (e o modelo local) **como executar um fluxo Git** com pouco contexto. Elas não substituem o Git: definem pré-condições, tools permitidas, regras de mensagem e quando pedir confirmação ao usuário.

No [**mapa neural simbólico**](./NEURAL_MAP.md), cada skill é a camada **C** (recuperação rápida / “L2”): só o `SKILL.md` relevante entra no prompt, em vez do manual completo do Git.

## Onde ficam

```
skills/
├── stage-changes/
│   └── SKILL.md
├── semantic-commit/
│   └── SKILL.md
├── push/
│   └── SKILL.md
├── stash-save/
│   └── SKILL.md
├── stash-restore/
│   └── SKILL.md
├── branch-create/
│   └── SKILL.md
├── branch-switch/
│   └── SKILL.md
└── inspect-worktree/
    └── SKILL.md
```

Skills extras: diretório configurável via `GRIPPO_SKILLS_PATH` (futuro).

## Formato `SKILL.md`

Frontmatter YAML + corpo em Markdown (instruções para o slot **developer** do agent loop).

```yaml
---
id: semantic-commit
version: 1
description: Propor mensagem Conventional Commit e executar git commit após aprovação
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
```

### Campos comuns

| Campo | Uso |
|-------|-----|
| `id` | Identificador estável; bate com nome da pasta |
| `phase` | Fases do repo em que a skill pode rodar (ver ARCHITECTURE) |
| `tools` | Subconjunto de tools; o harness não expõe o resto à sessão |
| `requires_clean_conflicts` | Se `true`, bloqueia quando há conflitos não resolvidos |
| `max_steps` | Override do limite de passos do agent loop |

## Catálogo MVP (operações Git)

Use estes IDs ao criar os arquivos. O corpo de cada skill deve ser **curto** (regras + exemplos de tool args), não um tutorial completo de Git.

### `stage-changes` → `git add`

- **Objetivo:** mapear intenção (“só auth”, “tudo menos X”) para `files[]` no snapshot.
- **Tools:** `git_status`, `git_diff` (opcional), `git_stage`.
- **Regras:** só paths que existem no worktree; nunca `.env` / chaves (delegar à policy quando existir).

### `semantic-commit` → `git commit`

- **Objetivo:** ler diff (staged ou stage antes), propor mensagem Conventional Commits, **não** chamar `git_commit` sem confirmação explícita na UI.
- **Tools:** `git_status`, `git_diff`, `git_diff_cached`, `git_stage`, `git_commit`.
- **Regras:** um assunto por commit; separar feat/fix/docs quando o diff misturar; truncar diff grande e pedir escopo menor.

### `push` → `git push`

- **Objetivo:** push da branch atual com upstream configurado; avisar se ahead/behind ou sem remote.
- **Tools:** `git_status` (snapshot), `git_push`.
- **Regras:** `force` só com approval (policy); mostrar branch e remote antes de executar.

### `stash-save` → `git stash push`

- **Objetivo:** guardar mudanças locais com mensagem opcional; opcionalmente só alguns paths.
- **Tools:** `git_status`, `git_stash_push` (nome da tool no harness).
- **Regras:** explicar o que vai entrar no stash (lista de paths do snapshot).

### `stash-restore` → `git stash pop` / `apply`

- **Objetivo:** recuperar stash; preferir `pop` quando o usuário disser “último stash”.
- **Tools:** `git_stash_list`, `git_stash_pop` ou `git_stash_apply`.
- **Regras:** avisar se o apply pode gerar conflitos (repo não limpo).

### `branch-create` → `git checkout -b`

- **Objetivo:** criar branch a partir de HEAD (ou base explícita no futuro).
- **Tools:** `git_branch_create` (wrapper: `checkout -b`), `git_status`.
- **Regras:** validar nome de branch (sem espaços, sem ambiguidade com flags).

### `branch-switch` → `git checkout <branch>`

- **Objetivo:** trocar de branch quando o worktree permitir.
- **Tools:** `git_checkout`, `git_status`.
- **Regras:** se há mudanças não commitadas, sugerir stash ou commit antes (não forçar checkout destrutivo).

### `inspect-worktree` → leitura

- **Objetivo:** explicar status e diff; sem mutations.
- **Tools:** `git_status`, `git_diff`, `git_log` (leitura).
- **Default** da sessão `grippo` quando não há intenção clara de mutação.

## Metodologia no prompt (treinar o harness)

Em cada skill, deixe explícito o ciclo que o modelo deve seguir:

1. **Input** — pedido do usuário + `phase` + trecho relevante do snapshot.
2. **Estruturar** — listar paths/commits/branches candidatos; pedir `git_diff` só se necessário.
3. **Operar** — uma ou poucas tool calls com JSON válido.
4. **Executar** — harness roda Git, devolve novo snapshot; commit/push só após OK do usuário.

Isso alinha com o agent loop: OBSERVE → PLAN → POLICY → EXECUTE → VERIFY → FEEDBACK (ver [ARCHITECTURE.md](./ARCHITECTURE.md)).

## Ligação com a CLI

| Comando / intenção | Skill |
|--------------------|--------|
| `grippo add …` | `stage-changes` |
| `grippo commit` | `semantic-commit` |
| `grippo push` | `push` |
| `grippo stash …` | `stash-save` ou `stash-restore` |
| `grippo branch create …` | `branch-create` |
| `grippo branch …` (troca) | `branch-switch` |
| `grippo`, `grippo status`, explicar mudanças | `inspect-worktree` |

Fase inicial: subcomandos mapeiam 1:1 para skills; roteador por frase natural pode vir depois.

## Contribuindo uma skill

1. Criar pasta `skills/<id>/SKILL.md` com frontmatter válido.
2. Manter o corpo focado em decisões e exemplos de arguments — linkar conceitos Git longos para um futuro `docs/GIT.md` se precisar.
3. Listar `phase` e `tools` de forma restrita (menos contexto = melhor em 4 GB RAM).
