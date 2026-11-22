# Guia passo a passo

Como usar o GRIPPO no dia a dia — da branch ao push — e como ele protege seu repositório.

## Como funciona (em 30 segundos)

1. Você escreve o que quer (*"faça commit das alterações do formulário"*).
2. O GRIPPO lê o **estado real** do repositório e entende o pedido (regras determinísticas
   primeiro; LLM local só se precisar).
3. Monta um **plano** de operações Git, valida os argumentos e checa **guardas**: o alvo foi
   mesmo citado por você? a operação faria algo (não é no-op)? a branch é protegida?
4. Mostra o **impacto** (arquivos, commits, risco, como desfazer) e pede **confirmação**.
5. Cria um **backup** se a operação é destrutiva, executa e **verifica** o resultado.

## Passo a passo: uma tarefa completa

```bash
cd ~/projetos/meu-repo
grippo
```

| # | Você diz | O que acontece |
|---|---|---|
| 1 | `crie a branch feat/export-csv` | cria e troca para a branch (confirmar com `c`) |
| 2 | *(implemente a tarefa — você, o Claude Code, o Cursor…)* | o GRIPPO não escreve código |
| 3 | `mostre o que foi alterado` | status + diff resumido |
| 4 | `faça commit` | escolhe os arquivos da entrega, avisa o que deixou de fora, propõe `feat(export): …`; `e` edita a mensagem |
| 5 | `envie para o remoto` | `push` criando o upstream se preciso |
| 6 | `atualize minha branch com a main` | pergunta *merge ou rebase* e recomenda um |
| 7 | `volte para a main` | troca de branch |

### Se der conflito

```text
❯ como resolvo o conflito?        # explica POR QUE: quem mudou o quê, em quais commits
❯ (edite os arquivos — você ou sua IA de código)
❯ continue o merge                # confere marcadores, faz add e --continue
❯ aborte o merge                  # volta ao estado anterior
```

O GRIPPO **só explica** o conflito; escolher o lado é com você.

### Se algo deu errado (operações críticas)

| Quero… | Digite | Proteção |
|---|---|---|
| desfazer o último commit mantendo o trabalho | `desfaça o último commit mantendo as alterações` | risco **alto** se já publicado |
| reverter um commit sem reescrever histórico | `reverta o commit 1a2b3c4` | cria um commit novo |
| excluir uma branch | `exclua a branch feature/antiga` | recusa branches protegidas/atual; backup da ponta |
| descartar alterações locais | `descarte minhas alterações com reset hard` | exige `confirmar` + backup do trabalho |
| refazer a base | `faça rebase com a develop` | recusa no-op; backup do HEAD |

**Recuperar do backup:** `grippo backups` lista; `git reset --hard <ref>` volta o HEAD;
`git checkout <ref>-wip -- .` recupera trabalho não commitado.

### Pedidos vagos são recusados (de propósito)

`arruma aí` não vira `reset --hard`; `limpe minhas branches` não exclui nada: alvos
destrutivos precisam estar **no seu pedido**.

## Com Claude Code, Cursor e outras IAs

Duas formas:

- **Dois terminais** (simples): GRIPPO num, agente de código no outro.
- **Integrado via MCP**: a IA pede commits/branches ao GRIPPO e você aprova com
  `grippo approve`. Guia completo: [MCP.md](MCP.md).

## Comandos úteis

```bash
grippo doctor        # diagnóstico
grippo backups       # backups de segurança
grippo proposals     # propostas feitas por IAs
grippo config show   # configuração efetiva
```

Mais: [PERMISSIONS.md](PERMISSIONS.md) · [CONFIGURATION.md](CONFIGURATION.md) ·
[ARCHITECTURE.md](ARCHITECTURE.md).
