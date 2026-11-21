# Léxico Git para o GRIPPO (L1)

Resumo **curto** para roteamento e micro-plano (modelo Q4/Q2, ~4 GB). Não substitui o Git. O harness **lê o estado real**; este arquivo só define *o que cada coisa faz*.

**Fontes (Git 2.53):** man pages (`git help <cmd>`), [gitglossary](https://git-scm.com/docs/gitglossary), [giteveryday](https://git-scm.com/docs/giteveryday).

Este documento é o léxico de referência; as skills (`SKILL.md`) descrevem fluxos e as operações ficam no catálogo `grippo/engine/operations.py` (ver [ARCHITECTURE.md](./ARCHITECTURE.md)).

---

## 1. Três árvores (sempre)

| Nome | Onde | Papel |
|------|------|--------|
| **Working tree** (worktree) | arquivos no disco | o que você edita |
| **Index** (staging area, “cache”) | `.git/index` | o que **entra no próximo commit** |
| **HEAD** | ref da branch atual (ou commit solto) | último commit gravado nesta linha |

Fluxo do dia a dia (giteveryday, desenvolvedor sozinho):

```
editar worktree → git add (index) → git commit (HEAD avança) → git push (remoto)
```

- **Clean:** worktree = HEAD (nada pendente).
- **Dirty:** há mudanças não commitadas.
- **Staged:** no index, prontas para `commit`.
- **Unstaged:** no worktree, ainda não no index.
- **Untracked:** arquivo que o Git ainda não segue (`git add` para passar a seguir).

Se o pedido for “commit e push” e houver unstaged/untracked relevantes, o plano típico é **status → add (paths) → commit → push**, não pular o add.

---

## 2. Glossário mínimo

| Termo | Significado |
|-------|-------------|
| **Commit** (substantivo) | Snapshot na história; tem pais, mensagem, tree. |
| **Commit** (verbo) | Gravar o index como novo commit e avançar HEAD. |
| **Branch** | Linha de desenvolvimento. O *tip* é o último commit; HEAD aponta para a branch atual. |
| **Checkout / switch** | Atualizar worktree + index para outra branch (ou commit). |
| **Detached HEAD** | HEAD aponta para um commit, não para um nome de branch. Commit novo não move nenhuma branch. |
| **Remote / origin** | Outro repositório. `origin` é o upstream padrão. |
| **Fetch** | Baixa objetos e atualiza *remote-tracking* (`origin/main`). **Não** muda sua branch. |
| **Pull** | Fetch **e** integrar na branch atual (merge ou rebase). |
| **Push** | Envia commits locais e atualiza refs no remoto. |
| **Fast-forward** | Merge em que o outro lado só avançou; HEAD só anda, sem merge commit. |
| **Merge** | Junta histórias; pode criar merge commit; conflito se as duas linhas tocaram o mesmo trecho. |
| **Rebase** | Reaplica seus commits **em cima** de outra base (reescrita de história). |
| **Revert** | Novo commit que **desfaz** o efeito de um commit antigo (não apaga o passado). |
| **Reset** | Move HEAD e/ou index para um estado conhecido (perigoso se `--hard` e já publicado). |
| **Stash** | Guarda worktree+index sujos e deixa a árvore limpa como HEAD. |
| **Worktree** (`git worktree`) | Outra pasta ligada ao **mesmo** `.git`; outra branch checkout ao mesmo tempo. |
| **Conflict / unmerged** | Index com várias versões do mesmo path; merge/rebase/cherry-pick incompleto. |
| **Reflog** | Diário local de onde HEAD esteve; útil para recuperar reset/rebase. |
| **Upstream** | Branch remota que a local “segue” (`ahead` / `behind`). |

---

## 3. Comandos — o que cada um faz

Agrupados pelo que o GRIPPO precisa, não pela lista completa do Git.

### Leitura (barato; snapshot e ajuda)

| Comando | Faz | Usar quando o usuário pede |
|---------|-----|----------------------------|
| `git status` | Diff index↔HEAD, worktree↔index, untracked | “em que estado estou?”, pré-add/commit |
| `git diff` | Mudanças worktree vs index, ou entre commits | “o que mudou?”, comparar branches |
| `git diff --cached` | O que **já está staged** | mensagem de commit |
| `git log` | História de commits | “commits passados”, comparar ranges |
| `git show` | Um objeto (commit + patch, blob, tag) | “mostra esse commit” |
| `git blame` | Quem/qual commit tocou cada linha | investigação pontual |
| `git range-diff` | Diff entre **dois ranges** de commits | comparar duas versões de uma série |

### Gravando trabalho (MVP)

| Comando | Faz | Cuidado |
|---------|-----|---------|
| `git add` | Copia conteúdo do worktree **para o index** | Paths do snapshot; não inventar arquivos |
| `git commit` | Cria commit a partir do **index** | Sem index staged = nada a commitar (ou `-a` só em tracked) |
| `git push` | Atualiza ref remota + envia objetos | Precisa upstream; `behind` = não forçar sem pedido |
| `git stash` | Empilha sujeira local, worktree = HEAD | `pop`/`apply` para voltar |
| `git branch` | Listar / criar / apagar **nome** de branch | Não troca worktree sozinho |
| `git switch` / `checkout -b` | Trocar ou criar+entrar na branch | Worktree dirty pode bloquear |
| `git restore` | Desfazer arquivos no worktree (ou index) | Não é revert de commit publicado |

`checkout` antigo mistura **trocar branch** e **restaurar arquivos**. Preferir `switch` (branch) e `restore` (arquivos).

### Integração e recovery (fase 2)

| Comando | Faz | vs. o outro |
|---------|-----|-------------|
| `git fetch` | Só baixa | Seguro; não mistura código |
| `git pull` | Fetch + merge/rebase na atual | Pode gerar conflito |
| `git merge` | Junta outra branch **nesta** | Merge commit se não for FF |
| `git rebase` | Reescreve commits atuais sobre nova base | Não rebase o que já foi push (salvo acordo) |
| `git revert` | Commit novo que anula outro | Preferível em história publicada |
| `git reset` | Move HEAD/index/worktree | `--soft` / `--mixed` / `--hard`; `--hard` apaga uncommitted |
| `git cherry-pick` | Copia o *patch* de um commit para a branch atual | Worktree precisa estar limpa |
| `git mergetool` | Abre ferramenta para conflitos | Depois de merge/rebase parado |

### Worktree e repo

| Comando | Faz |
|---------|-----|
| `git worktree` | Várias working trees no mesmo repo (ex.: `develop` numa pasta, feature em outra) |
| `git remote` | Cadastrar/listar `origin` etc. |
| `git clone` / `git init` | Copiar repo / criar vazio |
| `git rm` / `git mv` | Remover/renomear **no index e** (em geral) no worktree |
| `git clean` | Apaga **untracked** (destrutivo; policy) |
| `git tag` | Marca um ponto da história |
| `git reflog` | Recuperar HEAD “perdido” localmente |
| `git config` | Opções do repo/usuário |
| `git bisect` | Busca binária do commit que introduziu um bug |

Fora do MVP: `am`, `format-patch`, `submodule`, `sparse-checkout`, `gc`, etc. — não carregar no prompt.

---

## 4. Decisão rápida (intenção → Git)

O modelo **não** escolhe flags livres. Só o fluxo; o harness monta a tool.

| Pedido | Checar no snapshot | Sequência típica |
|--------|--------------------|------------------|
| “commit” | Há staged? Unstaged? Conflito? | Se conflito: parar. Se só unstaged: `add` (paths) → mensagem → `commit` (confirmar). |
| “commit e push na develop” | Branch atual, dirty, upstream | `switch develop` se necessário → add se preciso → commit → push (confirmar). |
| “push” | Ahead/behind, upstream | Se behind: avisar (pull/rebase primeiro). Se ahead: `push`. |
| “diff desta com a antiga” | Nomes de branch/HEAD | `diff` ou `log A..B` entre refs reais. |
| “stash” | Dirty? | `stash push`; não stash se clean. |
| “volta pra main” | Dirty? | `switch main`; se dirty, stash ou recusar. |
| “revert isso” | SHA existe? Publicado? | `revert` (novo commit), não `reset --hard` em branch compartilhada. |
| “rebase” | Dirty? Já pushed? | Worktree limpa; confirmar reescrita. |
| “conflito” | `conflicts.paths` | Não commit/push; listar arquivos; mergetool/edição; depois `--continue`. |

---

## 5. Worktrees (`git worktree`)

Um repositório, **várias pastas**. Cada worktree tem **uma** branch checkout (a mesma branch não pode estar em duas trees).

O GRIPPO opera na worktree **atual** (`repository.root`). Analisar outra tree = snapshot daquele path, não misturar index das duas.

---

## 6. Diff entre commits / branches

- Worktree vs index: `git diff`
- Index vs HEAD: `git diff --cached`
- Branch atual vs outra: `git diff develop...HEAD` ou `git log develop..HEAD` (commits que estão aqui e não lá)
- Um commit: `git show <sha>`

`A..B` = alcançável de B e não de A. O snapshot traz `ahead`/`behind` vs upstream; para “branch antiga” o usuário precisa nomear a ref (ou o harness lista branches).

---

## 7. Conflito, rebase, revert (regras de ouro)

1. **Git manda.** Se `operation` no snapshot é `merge`/`rebase`/`cherry-pick`, só skills de recovery.
2. **Revert** desfaz com história nova; **reset --hard** apaga. Publicado → revert.
3. **Rebase** de commits já no remoto reescreve SHA → precisa force-push; default: recusar.
4. **Não** `commit`/`push` com `conflicts.paths` não vazio.
5. Depois de resolver: `add` dos paths → `merge --continue` ou `rebase --continue` (skill própria).

---

## 8. O que **não** entra no prompt do Ollama

- Manual completo, lista de flags, `git help -a`.
- Diffs gigantes (truncar; pedir escopo).
- Comandos que o harness não tem como tool.

O modelo Q2/Q4 só precisa: **este léxico em fatias**, **uma skill**, **snapshot pequeno**.
