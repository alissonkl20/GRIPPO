# Permissões e segurança Git

A segurança é imposta pelo código (`grippo/engine/permissions.py`,
`grippo/engine/executor.py`, `grippo/git/runner.py`), nunca pela LLM.

## Níveis de risco

| Risco | Operações | Confirmação |
|---|---|---|
| **Baixo** | status, diff, log, show, branch_list, remote_list, rev_parse, ls_files, reflog, tag_list, stash_list/show, compare; `fetch` (só referências remotas) | não |
| **Moderado** | add, unstage, commit, branch_create/rename/delete (`-d`), switch, checkout, merge, cherry_pick, revert, rebase (de commits não publicados), reset soft/mixed (de commits não publicados), stash push/pop/apply, pull, push, push_tag, rm, mv, tag_create/delete, remotos, *_abort/*_continue | `c` / `confirmar` |
| **Alto** | reset `--hard`; clean; restore (descarta alterações); push `--force`/`--force-with-lease`; push_delete; tag_delete_remote; branch_delete `--force`; stash_drop; rebase_skip/cherry_pick_skip; rebase ou reset que reescreve commits **já publicados**; commit `--amend` de commit publicado; add de arquivo sensível com opt-in explícito | digitar `confirmar` |

O risco considera argumentos **e** estado: `reset --soft HEAD~1` é moderado, mas vira
alto se o commit removido já está no remoto; `rebase` idem.

## Confirmação contextual

Antes de qualquer operação protegida o GRIPPO mostra: operação e argumentos, repositório,
branch, HEAD, estado do working tree, arquivos afetados, commits envolvidos,
consequências, risco e forma de recuperação (com o SHA para voltar). Exemplos:

- **reset --hard**: lista os arquivos com alterações não commitadas que serão perdidas.
  Antes de executar, o GRIPPO salva essas alterações em um backup (veja
  [Proteções contra erros irreversíveis](#proteções-contra-erros-irreversíveis)).
- **rebase**: lista os commits que serão reaplicados; avisa que os hashes mudam e, se
  publicados, que será preciso force push.
- **revert**: explica que um novo commit desfaz o anterior sem reescrever o histórico.
- **force push**: avisa que o histórico remoto será sobrescrito (e destaca branches
  protegidas: `security.protected_branches`).
- **clean**: lista exatamente os arquivos que serão apagados.

Respostas aceitas: `c`/`confirmar`/`s`/`sim` (moderado) e somente `confirmar` (alto);
`n`/`cancelar` cancela; `d` mostra detalhes e o comando exato; `e` edita a mensagem de
commit (gera um novo plano e uma nova confirmação). Resposta ambígua → pergunta de novo
(5 tentativas → cancela). EOF, Ctrl-C, silêncio e timeout **nunca** autorizam.

## Autorização

`GitPermissionManager.grant(prepared, Decision.CONFIRM)` só aceita o enum (strings,
`True` ou `1` são recusados) e devolve uma `Authorization`:

- assinada com HMAC-SHA256 por uma chave aleatória do processo;
- **uso único** (nonce consumido na execução) e com validade (`security.auth_ttl_sec`);
- vinculada a: digest do plano (operações + argumentos normalizados), repositório,
  branch, HEAD, fingerprint do working tree e vínculos extras (ex.: lista do `clean`).

Na execução, sob lock exclusivo, o executor tira um novo snapshot e recalcula o vínculo.
Se o plano mudou → "concedida para outra operação"; se o estado mudou →
`StateChangedError` e a CLI mostra a proposta atualizada para nova confirmação. Se o
risco recalculado for maior que o confirmado, também recusa.

## Proteção contra bypass (testada em `tests/security/`)

| Vetor | Proteção |
|---|---|
| chamar o executor sem autorização | `AuthorizationError` + evento `denied` na auditoria |
| chamar o runner diretamente com comando mutável | exige `MutationTicket` (não forjável fora do executor; uso único; argv exato) |
| aliases (`git st`, `alias.status=!sh`) | subcomando precisa estar na allowlist de builtins; Git não deixa alias sobrescrever builtin |
| opções globais (`-c core.pager=…`, `-C`, `--git-dir`) | não há como passá-las: o argv começa pelo subcomando validado |
| flags alternativas / perigosas | `--upload-pack`, `--receive-pack`, `--exec`, `-x`, `--output`, `--ext-diff`, `-i`, `-p`, `-e`… rejeitadas |
| comandos compostos / shell injection | sem shell; `;`, `$( )`, crases e `&&` chegam ao Git como texto literal ou são rejeitados pela validação de refs |
| argumentos maliciosos | refs com `-`, `..`, `:`, espaços; caminhos fora do repositório ou em `.git`; URLs `ext::`; parâmetros desconhecidos → `ValidationError` |
| plano montado à mão | o executor re-normaliza os argumentos e recusa diferenças |
| `.env` do repositório reconfigurando o GRIPPO | o GRIPPO não lê `.env` do diretório atual |

Limite honesto: código Python malicioso rodando **dentro** do mesmo processo poderia
importar os internos; o modelo de ameaça cobre a LLM, o usuário e a entrada externa.

## Autonomia

`security.autonomy_level`:

| Nível | Comportamento |
|---|---|
| 0 | análise somente: leituras; nenhuma operação mutável é permitida |
| 1 | consultas e recomendações: mostra o plano, mas não executa mutações |
| **2 (padrão)** | operações mutáveis com confirmação explícita |
| 3 | operações **moderadas** listadas em `security.auto_approve` rodam sem perguntar; risco alto continua exigindo `confirmar` |

## Auditoria

`~/.local/state/grippo/audit/audit.jsonl` (rotativo). Um JSON por evento:
`decision` (confirm/cancel), `denied` (motivo), `execute` (operação, argumentos,
comando, status, código de saída, duração, verificação, erro), sempre com timestamp UTC,
sessão, repositório, branch, HEAD e risco. Tokens, senhas e credenciais em URLs são
mascarados (`***`).

## Proteções contra erros irreversíveis

Além da confirmação, quatro camadas impedem que uma LLM (ou outra IA) faça algo que você
não pediu. Todas rodam em código determinístico, **antes** de qualquer comando Git.

1. **Aterramento no pedido** (`grippo/engine/guard.py`). Para operações destrutivas
   (`rebase`, `revert`, `reset`, `branch_delete`, `push_delete`, `tag_delete*`,
   `cherry_pick`, `clean`, `restore`, `rm`, `stash_drop`) todo alvo — branch, commit, tag,
   caminho — precisa **aparecer no texto do seu pedido**. `HEAD`/`HEAD~N` (N ≤ 5) só valem
   se você disse "último/anterior commit". Opções agressivas (`reset --hard`, `-D`,
   force push) exigem palavras explícitas (*hard*, *descarte*, *force*…). Caso contrário a
   proposta é recusada **sem nem chegar à tela de confirmação**:
   `"arruma aí"` → `reset --hard` é bloqueado.
2. **Guarda de no-op.** `rebase` da branch sobre ela mesma, sobre algo que ela já contém,
   `merge` de branch já incorporada ou de si mesma e `reset --soft` para o próprio HEAD são
   recusados com *"nada a fazer"* em vez de executar. `faça rebase com main` estando na
   `main` explica o porquê.
3. **Branches protegidas** (`security.protected_branches`) nunca são excluídas pelo GRIPPO.
4. **Backup automático** (`grippo/engine/backup.py`). Antes de `reset`, `rebase`,
   exclusão de branch/tag remota, force push, `clean`, `restore`, `rm` e `stash drop`, o
   GRIPPO fixa o estado atual em `refs/grippo/backup/<data>-<branch>-<op>`:
   - o HEAD (e a ponta da branch que será excluída) continuam alcançáveis, mesmo após `git gc`;
   - alterações **não commitadas** de arquivos rastreados são salvas como um commit de
     backup (`...-wip`) — nem o `reset --hard` as perde;
   - se o backup falhar, **a operação é abortada** (falha fechada).

   Liste e recupere:

   ```bash
   grippo backups                               # lista
   git reset --hard refs/grippo/backup/<nome>   # volta o HEAD
   git checkout refs/grippo/backup/<nome>-wip -- .   # recupera o trabalho não commitado
   ```

   O `runner` só aceita `update-ref` no formato exato `refs/grippo/backup/<nome> <sha>`.

### IAs externas (MCP)

Ferramentas de IA que usam o GRIPPO via [MCP](MCP.md) **só propõem**: não existe
ferramenta de aprovar ou executar. A mudança só acontece quando **você** roda
`grippo approve <id>` num terminal interativo, onde vê o impacto real e confirma.
