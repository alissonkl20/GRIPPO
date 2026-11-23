<div align="center">

**🌐 [English](README.md) · Português (Brasil)**

<img src="docs/img/grippo-cat.jpeg" alt="GRIPPO" width="220" />

# GRIPPO

**Seu assistente Git local, em linguagem natural — com a LLM rodando na sua máquina.**

Diga o que quer fazer. O GRIPPO lê o estado real do repositório, mostra o impacto,
pede sua confirmação e executa por uma camada determinística e auditada.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Git](https://img.shields.io/badge/git-%E2%89%A5%202.23-orange)
![LLM](https://img.shields.io/badge/LLM-100%25%20local%20(Ollama)-brightgreen)
![Status](https://img.shields.io/badge/status-pr%C3%A9--1.0-yellow)
![Platform](https://img.shields.io/badge/plataforma-Linux-lightgrey)

[Instalação](#instalação) · [Uso](#uso) · [Integração com IAs (MCP)](#integração-com-outras-ias-mcp) ·
[Segurança](#segurança) · [Documentação](#documentação) · [Contribuir](CONTRIBUTING.md)

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

**Conteúdo:** [Por que o GRIPPO?](#por-que-o-grippo) · [Funcionalidades](#funcionalidades) ·
[Instalação](#instalação) · [Uso](#uso) · [Fluxo de trabalho com Claude Code](#fluxo-de-trabalho-com-claude-code) ·
[Integração com IAs (MCP)](#integração-com-outras-ias-mcp) · [Segurança](#segurança) ·
[Como funciona](#como-funciona) · [Documentação](#documentação) · [Limitações conhecidas](#limitações-conhecidas) ·
[Roadmap](#roadmap) · [Contribuindo](#contribuindo) · [Licença](#licença)

## Por que o GRIPPO?

Você já usa um agente de código (Claude Code, Cursor, Copilot…) para **implementar** a
tarefa. Falta alguém para cuidar do **versionamento**: criar a branch certa, separar o que
entra no commit, escrever uma mensagem decente, publicar sem estragar o histórico e te
tirar de um conflito. É isso que o GRIPPO faz — e só isso.

|  | Git puro | Agentes de código | **GRIPPO** |
|---|:-:|:-:|:-:|
| Linguagem natural | ✗ | ✓ | ✓ |
| Roda 100% local (sem enviar código para a nuvem) | ✓ | ✗ | ✓ |
| Mostra impacto e como desfazer antes de agir | ✗ | varia | ✓ |
| Confirmação vinculada ao estado do repositório | ✗ | ✗ | ✓ |
| Auditoria de cada operação | ✗ | ✗ | ✓ |
| Funciona sem LLM (roteador determinístico) | ✓ | ✗ | ✓ |

## Funcionalidades

- **Branches** — criar, trocar, renomear, excluir, listar (inclusive mescladas), definir upstream.
- **Commits** — escolhe os arquivos que pertencem à entrega, classifica o tipo
  (`feat`, `fix`, `docs`, `refactor`…) e propõe uma mensagem em
  [Conventional Commits](https://www.conventionalcommits.org). Você pode editá-la.
- **Sincronização** — fetch, pull, push (cria o upstream sozinho), tags, remotos.
- **Integração** — merge, rebase, cherry-pick, com detecção de conflito.
- **Conflitos** — *"como resolvo o conflito?"* explica **por que** deu conflito (quem mudou
  o quê, em quais commits, os trechos que colidem). Ao pedir *"continue o merge"* ele confere
  se ainda restam marcadores `<<<<<<<` e só então faz `add` + `--continue`. Também
  oferece `abort` e `skip`. Ele explica; **você** escolhe o lado.
- **Desfazer** — `revert`, `reset` (soft/mixed/hard), `restore`, stash, `reflog` — sempre
  informando o comando de recuperação, com **backup automático** antes das destrutivas.
- **Inspeção** — status, diff, log, show, comparar branches.

Ele **não** é um agente de código: não implementa funcionalidades, não corrige bugs e não
faz code review.

## Instalação

```bash
git clone https://github.com/alissonkl20/GRIPPO.git
cd GRIPPO
./install.sh            # sem sudo; use --add-to-path se ~/.local/bin não estiver no PATH
grippo doctor           # diagnóstico: Git, Python, Ollama, GPU, PATH
grippo model pull       # baixa o modelo padrão (qwen2.5:3b) no Ollama
```

**Requisitos:** Linux, Python ≥ 3.11 com `venv`, Git ≥ 2.23 e, para a LLM local,
[Ollama](https://ollama.com) (~2 GB de VRAM ou RAM livres para o modelo padrão).
Sem Ollama o GRIPPO continua funcionando com o roteador de frases conhecidas
(`--no-llm`). Mais detalhes em [INSTALL.md](INSTALL.md).

## Uso

```bash
cd ~/projetos/qualquer-repo     # funciona em qualquer subpasta ou worktree
grippo                          # modo interativo
grippo ask "crie uma branch feature/login"
grippo --repo ~/outro/repo status
```

Se você estiver numa pasta que **contém** repositórios (e não dentro de um), o GRIPPO
lista os que encontrou e diz qual comando usar.

Exemplos de pedidos:

| Você diz | O GRIPPO propõe |
|---|---|
| "crie uma branch feature/login" | `git switch -c feature/login` |
| "volte para a main" | `git switch main` |
| "faça commit das alterações" | `git add` (só o que pertence à entrega) + `git commit` |
| "envie para o remoto" | `git push --set-upstream origin HEAD:refs/heads/<branch>` |
| "atualize minha branch com a main" | pergunta: merge ou rebase? (recomenda um) |
| "reverta o commit 1a2b3c4" | `git revert --no-edit 1a2b3c4` |
| "desfaça o último commit mantendo as alterações" | `git reset --soft HEAD~1` (risco **alto** se já publicado) |
| "continue o merge" | `git add` dos arquivos resolvidos + `git merge --continue` |
| "aborte o merge" | `git merge --abort` |
| "como resolvo o conflito?" | explica por que deu conflito (não escolhe o lado) |
| "liste as branches já mescladas" | `git branch --merged` |

Comandos de manutenção:

```bash
grippo doctor        # diagnóstico da instalação
grippo bench         # RAM/VRAM/tempo/tokens por segundo
grippo config set llm.model qwen2.5:3b
grippo update        # atualização com validação e rollback
grippo uninstall     # preserva configurações, modelos, logs e auditoria
```

## Fluxo de trabalho com Claude Code

O GRIPPO roda **ao lado** do seu agente de código, em outro terminal aberto no mesmo
repositório — ou **integrado**, via MCP (próxima seção).

```text
 Terminal A — GRIPPO                    Terminal B — Claude Code
 ───────────────────────                ─────────────────────────
 ❯ crie a branch feat/export-csv
   ✔ branch criada e ativa
                                        > implemente a exportação CSV
                                          … edita arquivos, roda testes …
 ❯ faça commit
   ◆ analisa o diff → feat(export): add CSV export
   [c] confirmar
 ❯ envie para o remoto
   ✔ remoto atualizado
```

Passo a passo completo em [docs/GUIA.md](docs/GUIA.md).

## Integração com outras IAs (MCP)

`grippo mcp` transforma o GRIPPO em um servidor [MCP](https://modelcontextprotocol.io):
Claude Code, Cursor, Claude Desktop e afins pedem commits, branches e push ao GRIPPO,
**sem poder executá-los sozinhos**.

```bash
claude mcp add grippo -- grippo --repo ~/projetos/meu-repo mcp   # Claude Code
grippo mcp --print-config                                        # outros clientes (JSON)
```

```text
IA ──► grippo_propose ──► PROPOSTA p-1a2b3c4d (nada executado)
você ► grippo approve p-1a2b3c4d ► vê o impacto ► confirma ► executa
```

- A IA **consulta e propõe**; **só você aprova**, num terminal interativo.
- Alvos destrutivos precisam constar no seu pedido: a IA não "inventa" a branch a excluir.
- No-ops, branches protegidas e operações inválidas são recusados antes de virarem proposta.

Detalhes e limites em [docs/MCP.md](docs/MCP.md).

## Segurança

1. **O modelo interpreta; o Git é a verdade; o código executa.** A LLM só propõe intenções
   estruturadas (`{"op": "revert", "args": {...}}`) — nunca comandos.
2. **Nada muda sem você.** Toda operação que altera o repositório mostra repositório,
   branch, HEAD, arquivos, commits, consequências e como recuperar. Risco **alto**
   (`reset --hard`, `clean`, force push…) exige digitar `confirmar`.
3. **A autorização vale para o que você viu.** É de uso único, expira e fica vinculada à
   operação, aos argumentos e ao estado do repositório: se algo mudar, ele pergunta de novo.
4. **Sem atalhos para o shell.** Sem `shell=True`, subcomandos em allowlist, flags
   perigosas bloqueadas.
5. **Resultado verificado.** Sucesso só é informado depois de conferir o estado real.
6. **Nada se perde.** Antes de `reset`, `rebase`, exclusões, force push, `clean` e `restore`,
   o estado (inclusive alterações não commitadas) é salvo em `refs/grippo/backup/…`.
7. **Sem alucinação.** Alvos de operações destrutivas precisam estar no seu pedido; pedidos
   vagos como *"arruma aí"* nunca viram `reset --hard`. No-ops são recusados.

Detalhes em [docs/PERMISSIONS.md](docs/PERMISSIONS.md) e [SECURITY.md](SECURITY.md).

## Como funciona

```text
pedido ─► roteador determinístico ─┐
                                   ├─► plano de operações ─► validação ─► confirmação
pedido ─► LLM local (Ollama) ──────┘                                          │
                                                                              ▼
                                   estado real ◄── verificação ◄── executor (git, sem shell)
```

Skills ([docs/SKILLS.md](docs/SKILLS.md)) descrevem fluxos como *semantic-commit*, *push* e
*stash*, ensinando o modelo local a decidir o tipo da entrega e a mensagem do commit.
Arquitetura completa em [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Documentação

| Documento | Conteúdo |
|---|---|
| [INSTALL.md](INSTALL.md) · [UPDATING.md](UPDATING.md) · [UNINSTALL.md](UNINSTALL.md) | instalação, atualização, remoção |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | módulos, fluxo de execução, decisões |
| [docs/GUIA.md](docs/GUIA.md) | **passo a passo** de uso: da branch ao push, conflitos e desfazer |
| [docs/MCP.md](docs/MCP.md) | usar o GRIPPO a partir de Claude Code, Cursor e outras IAs |
| [docs/PERMISSIONS.md](docs/PERMISSIONS.md) | níveis de risco, autorização, guardas, backups, auditoria |
| [docs/LLM.md](docs/LLM.md) | backend local, modelo, memória, medições e limitações |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | `config.toml` e variáveis `GRIPPO_*` |
| [docs/TESTING.md](docs/TESTING.md) | camadas de teste |
| [docs/RELEASING.md](docs/RELEASING.md) | versionamento e publicação |
| [docs/GIT.md](docs/GIT.md) · [docs/SKILLS.md](docs/SKILLS.md) | léxico Git e skills |
| [CONTRIBUTING.md](CONTRIBUTING.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) · [CHANGELOG.md](CHANGELOG.md) | contribuição e histórico |

## Limitações conhecidas

- Modelos pequenos (3B) erram interpretações ambíguas; por isso o roteador determinístico
  tem prioridade e **toda** proposta passa pela confirmação. Medições em [docs/LLM.md](docs/LLM.md).
- Testado em Ubuntu 26.04 / Python 3.14 / Git 2.53 / Ollama 0.40. Outras distribuições,
  macOS e Windows ainda não foram testados.
- Pedidos em português são o foco; outros idiomas dependem da LLM.

## Roadmap

- [x] Servidor MCP para Claude Code, Cursor e outras IAs
- [x] Explicação de conflitos (por que ocorreram)
- [ ] Hooks do Claude Code (sugerir commit ao fim de uma tarefa)
- [ ] Interface TUI com painel de estado em tempo real
- [ ] Suporte a macOS
- [ ] Publicação no PyPI

## Contribuindo

Contribuições são bem-vindas! Leia [CONTRIBUTING.md](CONTRIBUTING.md) e rode a suíte antes
de abrir um PR:

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest            # ~400 testes, sem precisar de Ollama
```

## Licença

**A definir** — veja [LICENSE](LICENSE). Até que o mantenedor escolha uma licença open
source (MIT, Apache-2.0, GPL-3.0…), o código não deve ser considerado livre para
redistribuição.
