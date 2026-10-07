# GRIPPO — Architecture

This document defines runtime contracts for the **MVP** (daily Git: add, commit, push, stash, branch checkout) and extension points for later phases. **Implementation:** Python 3.11+, FastAPI (`app/`), harness under `app/harness/`, Git via subprocess — see [STRUCTURE.md](./STRUCTURE.md).

**Design mantra:** the LLM proposes; the harness validates against Git; only then does deterministic execution run.

**MVP scope (product):** help users via semantic **skills** mapped to `git add`, `git commit`, `git push`, `git stash`, `git checkout -b`, and `git checkout <branch>`. See [SKILLS.md](./SKILLS.md) for skill IDs and authoring rules.

---

## 1. System context

```
┌─────────────────────────────────────────────────────────────────┐
│                           CLI / TUI                              │
│  grippo, grippo status, grippo commit, grippo "natural lang"    │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                      Session / Orchestrator                      │
│  picks skill, wires context, drives Agent Loop                   │
└────────────────────────────┬────────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  Agent Loop   │   │ Skill System  │   │ Policy Engine │
│ planner+loop  │   │ load+execute  │   │ allow/deny/   │
│               │   │               │   │ confirm       │
└───────┬───────┘   └───────┬───────┘   └───────┬───────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │    Tool Dispatcher   │
                 │  (structured Git API) │
                 └──────────┬──────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
     ┌─────────────────┐         ┌─────────────────┐
     │ RepositoryState │         │   Git Executor    │
     │  (read model)   │◄────────│  git binary / lib │
     └─────────────────┘  refresh└─────────────────┘
                            │
                            ▼
                      ┌──────────┐
                      │  .git/   │
                      └──────────┘

        ┌─────────────────────────────────┐
        │ LLM Provider (Ollama, HTTP, …)  │
        │  chat + tool schemas only       │
        └─────────────────────────────────┘
```

**Boundaries**

| Component | May do | Must not do |
|-----------|--------|-------------|
| LLM | Interpret user intent, choose tools, draft messages | Execute shell, mutate repo without tools |
| Tool layer | Run whitelisted Git operations with fixed argv | Arbitrary `exec` |
| RepositoryState | Aggregate `git` output into structs | Trust model assertions |
| Policy | Block or require approval | Replace Git as truth |
| Skills | Inject instructions + tool subsets + workflows | Bypass policy |

---

## 2. RepositoryState

RepositoryState is the **canonical read model** exposed to the agent and to CLI commands like `grippo status`. It is always built by the harness from Git commands, never from model text.

### 2.1 Refresh strategy

1. **Full snapshot** — on session start, after any mutating tool, on user `status`/`doctor`, and when entering conflict/merge modes.
2. **Partial invalidation** (Phase 2+) — after `git_stage`, refresh `worktree` + `index` only; after `git_commit`, refresh `head` + branch tracking.
3. **Stale guard** — every mutating tool call receives `state_version` (monotonic int or content hash of snapshot). If the model’s plan references paths from an old version, the harness rejects and returns fresh state.

### 2.2 Schema (v1)

```json
{
  "version": 1,
  "repository": {
    "root": "/abs/path",
    "is_bare": false,
    "git_dir": "/abs/path/.git"
  },
  "branch": {
    "name": "feature/auth",
    "upstream": "origin/feature/auth",
    "ahead": 2,
    "behind": 0,
    "detached": false
  },
  "head": {
    "sha": "abc123…",
    "short_sha": "abc123",
    "message": "feat(auth): add login flow"
  },
  "worktree": {
    "modified": ["src/auth.ts"],
    "added": [],
    "deleted": [],
    "renamed": [{ "from": "a", "to": "b" }],
    "untracked": ["docs/auth.md"],
    "intent_to_add": []
  },
  "index": {
    "staged": ["tests/auth.test.ts"]
  },
  "conflicts": {
    "paths": [],
    "unmerged": []
  },
  "operation": {
    "kind": null,
    "detail": null
  },
  "remote": {
    "default_push": "origin",
    "fetch_ok": true
  },
  "flags": {
    "clean": false,
    "diverged": false,
    "shallow": false
  },
  "risks": {
    "secrets_suspected": [],
    "large_files": []
  }
}
```

`operation.kind` enum (Git state machine):

| Value | Meaning |
|-------|---------|
| `null` | Normal worktree |
| `merging` | Merge in progress |
| `rebasing` | Rebase in progress |
| `cherry_picking` | Cherry-pick in progress |
| `reverting` | Revert in progress |
| `bisect` | Bisect in progress |

Derived **repo phase** (for prompts and skill routing):

```
CLEAN | MODIFIED | STAGED | COMMITTED | AHEAD | PUSHED
      | CONFLICT | MERGING | REBASING | DETACHED | DIVERGED
```

Phase is computed deterministically from fields above (not LLM-labeled).

### 2.3 Go package

- `internal/worktree/state` — types, `Snapshot`, `Phase()`, JSON serialization for LLM context.
- `internal/git/status` — parsers for `git status --porcelain=v2`, branch `-vv`, etc.
- `internal/git/...` — focused packages per concern (diff, branch, …).

### 2.4 Context sizing (small models)

Default LLM payload:

- Always: `branch`, `head.short_sha`, `operation`, `phase`, counts, `conflicts.paths`.
- On demand via `git_diff` / `git_log` tools: full diffs and history slices.
- Never dump entire worktree file contents in the base snapshot.

---

## 3. Tool API

Tools are the **only** mutation and inspection surface for the model. Each tool maps to a fixed implementation that constructs argv explicitly (no model-supplied flags).

### 3.1 Tool envelope

**Request (from model):**

```json
{
  "name": "git_stage",
  "arguments": {
    "files": ["src/auth.ts", "tests/auth.test.ts"]
  }
}
```

**Response (from harness):**

```json
{
  "ok": true,
  "tool": "git_stage",
  "executed": ["git", "add", "--", "src/auth.ts", "tests/auth.test.ts"],
  "stdout": "",
  "stderr": "",
  "state": { /* RepositoryState snapshot */ },
  "policy": { "tier": "mutable", "approved": true }
}
```

On policy block:

```json
{
  "ok": false,
  "error": "policy_denied",
  "message": "git_reset_hard requires explicit user approval",
  "required_approval": "dangerous_reset_hard"
}
```

### 3.2 Tool catalog

| Tool | Tier | Ship | Notes |
|------|------|------|-------|
| `git_status` | safe | MVP | Returns snapshot (may skip redundant git if cache fresh) |
| `git_diff` | safe | MVP | `paths[]`, `staged`, `context_lines` |
| `git_diff_cached` | safe | MVP | Staged diff only |
| `git_log` | safe | MVP | `max_count`, `path`, `oneline` |
| `git_show` | safe | MVP | `rev`, optional `path` |
| `git_stage` | mutable | MVP | `files[]` — maps to `git add` |
| `git_unstage` | mutable | MVP | `files[]` or `all` |
| `git_commit` | mutable | MVP | `message`, optional `amend` (policy-gated) |
| `git_branch_create` | mutable | MVP | `name` — `git checkout -b` |
| `git_checkout` | mutable | MVP | `branch` — switch branch (not path discard in MVP) |
| `git_push` | mutable/dangerous | MVP | `remote`, `branch`; force → dangerous |
| `git_stash_push` | mutable | MVP | optional `message`, `paths[]` |
| `git_stash_list` | safe | MVP | list stash entries |
| `git_stash_pop` | mutable | MVP | `index` default 0 |
| `git_stash_apply` | mutable | MVP | `index` default 0 |
| `git_branch` | safe/mutable | later | list branches (read-only list in MVP via snapshot if enough) |
| `git_pull` | mutable | later | |
| `git_merge` | mutable | later | |
| `git_rebase` | dangerous | later | |
| `git_resolve_conflict` | mutable | later | structured hunks, not freeform patch |

**Foundation (read-only):** `git_status`, `git_diff`, `git_diff_cached`, `git_log`, `git_show`.

**MVP mutations:** `git_stage`, `git_unstage`, `git_commit`, `git_push`, `git_stash_*`, `git_branch_create`, `git_checkout`.

### 3.3 Validation rules (all tools)

1. **Path safety** — resolve to absolute path; must be inside worktree; reject `.git` internals and path traversal.
2. **Existence** — `git_stage` files must appear in current snapshot as modified/untracked/renamed unless `intent_to_add` flow is explicit.
3. **Operation lock** — while `operation.kind != null`, only tools allowed for that mode (e.g. no `git_checkout` during rebase without abort skill).
4. **No free text argv** — message bodies and branch names are validated (length, charset, no `-` prefix where ambiguous).

### 3.4 Registration

```go
type Tool struct {
    Name        string
    Tier        policy.Tier
    Schema      JSONSchema   // for LLM tool calling
    Run         func(ctx ToolContext, args map[string]any) (ToolResult, error)
    AllowedWhen func(s state.Snapshot) bool
}
```

`internal/git` registers tools; `internal/agent` exposes the union to the provider.

---

## 4. Agent Loop

The loop turns **user goal + RepositoryState + optional Skill** into a bounded sequence of tool calls and user-visible messages.

### 4.1 States

```
IDLE → OBSERVE → PLAN → [PROPOSE_TOOL]* → POLICY → EXECUTE → VERIFY → FEEDBACK
                      ↘ ASK_USER (clarify / approve) ↗
                      ↘ DONE / FAILED
```

| Step | Harness responsibility |
|------|------------------------|
| **OBSERVE** | `RepositoryState` full refresh; attach skill preamble if any |
| **PLAN** | LLM turn with system + skill + compact state; model may emit text only or tool calls |
| **PROPOSE_TOOL** | Parse tool calls; validate schema; do not execute yet |
| **POLICY** | Tier check, approval queue, secret heuristics (Phase 5) |
| **EXECUTE** | Run tool; capture stdout/stderr; refresh state |
| **VERIFY** | Re-query Git for facts the model claimed (e.g. “clean” → `git_status`) |
| **FEEDBACK** | Append tool results to conversation; loop until `done` or max steps |

### 4.2 Limits (local / 4B-friendly)

| Limit | Default | Purpose |
|-------|---------|---------|
| `max_steps` | 12 | Prevent runaway loops |
| `max_tool_calls_per_turn` | 3 | Keep context small |
| `max_diff_bytes` | 32 KiB per `git_diff` | Truncate with `truncated: true` |
| `timeout_per_git` | 30s | Hung hooks |

### 4.3 Message roles

- **system** — harness identity, safety rules, tool list summary.
- **developer** — skill body (when active).
- **user** — CLI input.
- **assistant** — model text (shown to user).
- **tool** — structured tool results (not shown raw unless `--verbose`).

### 4.4 Planner vs executor split

- **Planner (LLM):** chooses next tool(s) or asks user; may draft commit messages (proposals only).
- **Executor (harness):** policy, argv construction, Git run, snapshot update.

Commit messages and file groupings are **proposals** until `git_commit` passes policy and optional user approval (`grippo commit` flow).

### 4.5 Package layout

- `internal/agent/loop` — step machine, limits, verification hooks.
- `internal/agent/planner` — prompt templates, tool-call parsing.
- `internal/agent/context` — build prompts from `Snapshot` + history.

---

## 5. Skill System

Skills package **task-specific** behavior without forking the core loop.

### 5.1 Skill unit

Directory per skill (repo-root `skills/` and built-in `internal/skills/embed`):

```
skills/semantic-commit/
├── SKILL.md          # required: frontmatter + instructions
├── tools.yaml        # optional: subset or extra read-only helpers
└── policy.yaml       # optional: stricter overrides
```

**SKILL.md frontmatter (YAML):**

```yaml
---
id: semantic-commit
version: 1
description: Analyze diffs and propose Conventional Commits
phase: [MODIFIED, STAGED]
tools:
  - git_status
  - git_diff
  - git_diff_cached
  - git_stage
  - git_commit
requires_clean_conflicts: true
---
```

Body: markdown instructions for the model (commit types, grouping rules, when to ask user).

### 5.2 Selection

| Trigger | Skill |
|---------|--------|
| `grippo` (default) | `inspect-worktree` unless subcommand/intent |
| `grippo add …` | `stage-changes` |
| `grippo commit` | `semantic-commit` (may chain `stage-changes` if nothing staged) |
| `grippo push` | `push` |
| `grippo stash` (save) | `stash-save` |
| `grippo stash` (pop/apply) | `stash-restore` |
| `grippo branch create …` | `branch-create` |
| `grippo branch …` (switch) | `branch-switch` |
| User phrase / router LLM | `registry.Match(intent)` (post-MVP) |
| `operation.kind == merging` | auto `resolve-conflicts` (later) |

MVP: CLI subcommands map 1:1 to skills; optional phrase routing later.

### 5.3 Execution model

1. Load skill → merge `tools` into session allowlist.
2. Inject skill text into **developer** message slot.
3. Run Agent Loop with skill-specific `max_steps` override if set.
4. Skill **cannot** disable global policy tiers (only add restrictions).

### 5.4 Packages

- `internal/skills/loader` — discover `skills/`, parse frontmatter.
- `internal/skills/registry` — id → metadata, CLI bindings.
- `internal/skills/executor` — attach skill to session.

---

## 6. Policy Engine

Policies sit **between** proposed tool calls and Git execution.

### 6.1 Tiers

| Tier | Behavior |
|------|----------|
| **safe** | Auto-execute |
| **mutable** | Auto-execute in trusted CLI flags; else record in session log; optional confirm config |
| **dangerous** | Always require explicit user approval token in session |

### 6.2 Approval flow

```
Proposed: git_reset_hard
  → policy classifies dangerous_reset_hard
  → UI shows affected paths from RepositoryState (not from model)
  → user: grippo approve <token> or interactive [y/N]
  → token bound to argv hash + state_version, TTL 5 min
```

### 6.3 Rules (examples)

```yaml
# internal/policy/rules/default.yaml
tools:
  git_reset_hard:
    tier: dangerous
    approval: dangerous_reset_hard
  git_push:
    tier: mutable
    when:
      force: true
    tier_override: dangerous
  git_clean:
    tier: dangerous
```

### 6.4 Secret / hygiene heuristics (Phase 5)

Before `git_stage` / `git_commit`, scan paths against:

- `.env`, `*.pem`, `id_rsa`, `credentials.json`, user config patterns.

Block with `policy_denied` unless `--allow-secrets` (CLI) or explicit approval.

### 6.5 Packages

- `internal/policy/safe`, `dangerous`, `approval`
- Single entry: `policy.Evaluate(ctx, tool, args, snapshot) → Decision`

---

## 7. LLM provider

Model-agnostic interface; GRIPPO ships harness, not weights.

```go
type Provider interface {
    Name() string
    Chat(ctx context.Context, req ChatRequest) (ChatResponse, error)
    SupportsTools() bool
}

type ChatRequest struct {
    Messages []Message
    Tools    []ToolSchema
    Options  ModelOptions // temperature, max_tokens
}
```

**MVP providers:**

1. **Ollama** — local HTTP, tool calling if model supports it.
2. **OpenAI-compatible HTTP** — optional for dev.

Fallback without native tools: JSON-only mode with grammar/constrained decoding in prompt (slower, less reliable — documented as degraded mode).

Config: `grippo --model qwen2.5:3b`, env `GRIPPO_MODEL`, `GRIPPO_PROVIDER=ollama`.

Package: `internal/runtime/llm`.

---

## 8. CLI integration

| Command | Flow |
|---------|------|
| `grippo` | REPL → `inspect-worktree` or skill from intent |
| `grippo status` | No LLM: print `Snapshot` + `Phase()` |
| `grippo add` | `stage-changes` |
| `grippo commit` | `semantic-commit` + user approval before `git_commit` |
| `grippo push` | `push` |
| `grippo stash` | `stash-save` or `stash-restore` |
| `grippo branch` | `branch-create` or `branch-switch` |
| `grippo diff` | Deterministic diff; optional LLM summary via `inspect-worktree` |
| `grippo doctor` | Rules on snapshot (diverged, detached, conflict, secret paths) |

Entry: `cmd/grippo/main.go` → `internal/cli`.

---

## 9. Verification principle

Whenever the model states a **fact** about repo state in assistant text, the harness may run cheap checks before trusting downstream plans:

| Claim pattern | Verify via |
|---------------|------------|
| clean worktree | snapshot `flags.clean` |
| no conflicts | `conflicts.paths` empty |
| branch name | `branch.name` |
| ahead/behind | `branch.ahead/behind` |

Mismatch → inject **tool** message correcting state before next LLM turn.

---

## 10. MVP deliverables (implementation checklist)

1. **Repo detection** — walk up to `.git`, support worktrees (`git rev-parse --git-dir`).
2. **Snapshot builder** — porcelain v2 + branch tracking + `Phase()`.
3. **Tools** — foundation read tools + MVP mutations (stage, commit, push, stash, branch create/switch).
4. **Policy** — safe vs mutable; confirm before `git_commit` and `git_push`; stub dangerous tiers.
5. **Agent loop** — OBSERVE → PLAN → POLICY → EXECUTE → VERIFY → FEEDBACK with `max_steps`.
6. **Ollama provider** — tool calling or documented JSON degraded mode.
7. **Skills** — load `skills/*/SKILL.md`; wire CLI to [SKILLS.md](./SKILLS.md) catalog.
8. **Tests** — golden status parser; integration tests with temp repos (add/commit/stash/branch).

No shell escape hatch in MVP.

---

## 11. Extension points (later phases)

| Feature | Touch points |
|---------|----------------|
| Semantic commit (MVP) | `semantic-commit` skill + `internal/agent/planner` prompts |
| Pull / diverged | `git_pull`, snapshot `ahead`/`behind` UX |
| Conflict mode | `operation.kind`, `git_resolve_conflict`, UI mode flag |
| Custom skills | extra dirs via `GRIPPO_SKILLS_PATH` |
| Plugins | register `Tool` + `Skill` at runtime (later) |

---

## 12. Related documents

- [README](../README.md) — MVP scope and CLI vision
- [SKILLS.md](./SKILLS.md) — semantic skills for add/commit/push/stash/branch
- (future) `docs/GIT.md` — Git concepts for contributors (not full prompt dumps)
- (future) `docs/POLICY.md` — operator guide for enterprise rules

---

*Revision: 0.2 — MVP focused on daily Git workflows and semantic skills; update when types land in `internal/worktree/state`.*
