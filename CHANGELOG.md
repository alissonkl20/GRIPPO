# Changelog

Todas as mudanças relevantes do GRIPPO. Formato: [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
versionamento: [Semantic Versioning](https://semver.org/lang/pt-BR/).

## [Unreleased]

## [0.2.0] - 2026-11-22

### Adicionado
- **Integração com outras IAs (MCP):** `grippo mcp` expõe o GRIPPO a Claude Code, Cursor,
  Claude Desktop e qualquer cliente MCP. A IA consulta e **propõe**; só você aprova, com
  `grippo approve <id>` num terminal interativo (nenhuma ferramenta aprova ou executa).
  `grippo mcp --print-config` mostra a configuração pronta; `grippo proposals` lista o histórico.
- **Explicação de conflitos:** "como resolvo o conflito?" / "por que deu conflito?" mostra
  quem mudou o quê, em quais commits e os trechos conflitantes, com um resumo opcional do
  modelo local. Só explica; não escolhe o lado.
- **Guardas contra erros irreversíveis:** aterramento no pedido (alvos destrutivos precisam
  ter sido citados), recusa de no-op (`rebase` sobre a própria branch, merge já incorporado),
  branches protegidas não são excluídas e **backup automático** em `refs/grippo/backup`
  (inclui alterações não commitadas) antes de reset/rebase/exclusões/force push/clean/restore.
  `grippo backups` lista os backups.
- Opções `security.require_tty` e `security.proposal_ttl_min`.
- "volte para a main"; "continue o merge" faz `add` dos arquivos resolvidos (e avisa se
  ainda restam marcadores de conflito); sugestão de repositórios em subpastas.
- Guias: `docs/GUIA.md` (passo a passo) e `docs/MCP.md`.

### Alterado
- README em inglês (`README.md`) com a versão em português em `README.pt-BR.md`.
- Interface colorida: paleta única (âmbar), prompt com a branch atual
  (`grippo develop* ❯`), painéis de boas-vindas/estado/ajuda, etiquetas de risco,
  teclas de confirmação coloridas e destaque de diff/log. `NO_COLOR` respeitado.
- Sair com `exit`/`sair`/`quit` (sem barra) ou Ctrl-C duas vezes.
- Cabeçalho do modo interativo: guepardo ASCII animado original (substitui a barata),
  ~0,6 s, sem dependências nem arquivos de imagem; configurável em `[ui]`.
- Commit/push: aviso quando a branch está atrás da `main`; prévia do push correta após
  um commit no mesmo plano; mensagens do modelo sem tipo recebem o prefixo
  Conventional Commit; tipo escolhido pela maioria dos arquivos; corte em fim de palavra.
- "merge da main" sem branch local usa `origin/main` (com aviso).
- Pedidos de commit/push com branch diferente da atual pedem para trocar de branch antes.

### Removido
- `grippo/cli/assets/cockroach.png`, `doc.md`, `finale.md`, `docs/NEURAL_MAP.md` e o
  diagrama Excalidraw (substituídos por README e `docs/ARCHITECTURE.md`).
- Código sem uso (`GitReader.tags/upstream_of/config_get`, `OllamaClient.warm_up`,
  `GitPermissionManager.revoke_all`).

### Reestruturação inicial

Reestruturação completa para ferramenta CLI instalável, com execução Git segura.

### Adicionado
- CLI `grippo` (modo interativo, `ask`, `status`, `doctor`, `config`, `model`, `bench`,
  `update`, `uninstall`), funcionando a partir de qualquer diretório, `--repo`, worktrees.
- Catálogo de ~57 operações Git tipadas (consulta, branches, commits, histórico, stash,
  sincronização, tags, remotos) com validação, classificação de risco, impacto e verificação
  posterior do resultado real.
- `GitPermissionManager`: autorização HMAC de uso único, com validade, vinculada a
  operação + argumentos normalizados + repositório + branch + HEAD + estado do working tree.
- Executor determinístico com lock por repositório (threads e processos), timeouts com
  encerramento do grupo de processos, auditoria JSONL e logs rotativos (XDG).
- Integração Ollama: saída JSON estruturada, controle de contexto/GPU/threads/keep_alive,
  uma inferência por vez, tratamento de indisponibilidade/timeout/falta de memória e
  fallback para roteador determinístico.
- Seleção de arquivos para commit híbrida (palavras-chave + decisão por arquivo pela LLM +
  agrupamento por nome) e mensagens Conventional Commit.
- `install.sh` idempotente (sem sudo), atualização com validação e rollback,
  desinstalação que preserva dados do usuário.
- Suíte de testes por camada (unitários, Git real, segurança, concorrência, recuperação,
  end-to-end, LLM real, instalação, desempenho) e CI.

### Alterado
- Pacote renomeado de `app` para `grippo`.
- API HTTP agora é opcional (`grippo[api]`), exige token e não expõe execução de shell.
- Configuração em `~/.config/grippo/config.toml` + variáveis `GRIPPO_*`.

### Removido
- Endpoints `/api/v1/shell/run` e `/api/v1/shell/git` (permitiam qualquer comando git,
  inclusive destrutivos e `--upload-pack`, sem autorização).
- Leitura de `.env` do diretório atual (um repositório podia reconfigurar o GRIPPO).
- Dependências `pydantic-settings` e `typer`.

### Segurança
- Commit não adiciona mais todos os arquivos pendentes automaticamente; arquivos sensíveis
  (`.env`, chaves, credenciais) nunca são selecionados.
- Confirmação agora é vinculada ao plano exibido (antes, a confirmação recalculava arquivos
  e mensagem).

## [0.1.0]

- MVP: harness FastAPI, skills em Markdown, fluxo de commit e push.
