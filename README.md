# GRIPPO

**Assistente Git local para o terminal.** Você fala em linguagem natural; o GRIPPO lê o
estado real do repositório, propõe a operação Git adequada, mostra o impacto, pede sua
confirmação e executa por uma camada determinística — com um modelo de linguagem rodando
**na sua máquina** (Ollama, ~2 GB de VRAM ou RAM).

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

## O que ele é — e o que não é

O GRIPPO opera **exclusivamente Git**: status, diff, commits, branches, merge, rebase,
cherry-pick, revert, reset, stash, push/pull, tags e remotos. Ele analisa diffs e
arquivos **apenas para decidir operações Git** (por exemplo, quais arquivos pertencem a um
commit e qual mensagem usar).

Ele **não** é um agente de código: não implementa funcionalidades, não corrige bugs, não
faz code review e não substitui sua IDE.

## Princípios

1. **O modelo interpreta; o Git é a verdade; o código executa.** A LLM só propõe
   intenções estruturadas (`{"op": "revert", "args": {...}}`). Quem valida, autoriza,
   executa e verifica é código determinístico.
2. **Nada muda sem você.** Toda operação que altera o repositório mostra repositório,
   branch, HEAD, arquivos, commits, consequências e forma de recuperação, e exige
   confirmação. Risco alto (reset --hard, clean, force push, exclusão forçada…) exige
   digitar `confirmar`.
3. **A autorização vale para aquilo que você viu.** Ela é de uso único, expira e fica
   vinculada à operação, aos argumentos e ao estado do repositório: se algo mudar depois
   da confirmação, o GRIPPO pede de novo.
4. **Sem atalhos para o shell.** Nada de `shell=True`; subcomandos em allowlist; flags
   perigosas bloqueadas; o executor recusa mutações sem ticket emitido após a autorização.
5. **Resultado verificado.** Sucesso só é informado depois de conferir o estado real; em
   conflito ou falha, o estado intermediário é mostrado claramente.

## Instalação rápida (Linux)

```bash
git clone <URL_DO_REPOSITORIO> GRIPPO
cd GRIPPO
./install.sh            # sem sudo; use --add-to-path se ~/.local/bin não estiver no PATH
grippo doctor
grippo model pull       # baixa o modelo padrão (qwen2.5:3b) no Ollama
```

Requisitos: Linux, Python ≥ 3.11 com `venv`, Git ≥ 2.23 e, para a LLM local,
[Ollama](https://ollama.com). Sem Ollama, o GRIPPO continua funcionando com o roteador
determinístico (frases conhecidas). Detalhes: [INSTALL.md](INSTALL.md).

## Uso

```bash
cd ~/projetos/qualquer-repo     # funciona em qualquer diretório/subdiretório/worktree
grippo                          # modo interativo
grippo ask "mostre o que foi alterado"
grippo ask "desfaça o último commit mantendo minhas alterações"
grippo --repo ~/outro/repo status
grippo doctor                   # diagnóstico (Git, Python, Ollama, GPU, PATH)
grippo bench                    # mede RAM/VRAM/tempo/tokens por segundo
grippo config set llm.model qwen2.5:3b
grippo update                   # atualização com validação e rollback
grippo uninstall                # preserva configurações, modelos, logs e auditoria
```

Exemplos de pedidos: "crie uma branch fix/login", "faça cherry-pick do commit abc1234",
"quero desfazer as alterações desse commit sem apagar o histórico", "atualize minha
branch com develop", "envie minhas alterações para o remoto", "compare minha branch com
main", "guarde minhas alterações", "liste as branches já mescladas".

## Documentação

| Documento | Conteúdo |
|---|---|
| [INSTALL.md](INSTALL.md) · [UPDATING.md](UPDATING.md) · [UNINSTALL.md](UNINSTALL.md) | instalação, atualização, remoção |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | módulos, fluxo de execução, decisões |
| [docs/PERMISSIONS.md](docs/PERMISSIONS.md) | níveis de risco, autorização, autonomia, auditoria |
| [docs/LLM.md](docs/LLM.md) | backend local, modelo, memória, medições reais e limitações |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | `config.toml` e variáveis `GRIPPO_*` |
| [docs/TESTING.md](docs/TESTING.md) | camadas de teste e como executá-las |
| [docs/RELEASING.md](docs/RELEASING.md) | versionamento e publicação |
| [docs/GIT.md](docs/GIT.md) · [docs/SKILLS.md](docs/SKILLS.md) · [docs/NEURAL_MAP.md](docs/NEURAL_MAP.md) | léxico Git, skills, roteamento |
| [CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md) | contribuição, segurança, histórico |

## Status

Versão **0.2.0** (pré-1.0). Testado em Ubuntu 26.04 / Python 3.14 / Git 2.53 / Ollama
0.40 / RTX 3060. Outras distribuições ainda não foram testadas — veja
[INSTALL.md](INSTALL.md#compatibilidade).

## Licença

**A definir** — veja [LICENSE](LICENSE). Até que uma licença open source seja escolhida,
o código não deve ser considerado livre para redistribuição.
