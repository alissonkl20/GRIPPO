# Usando o GRIPPO a partir de outras IAs (MCP)

O GRIPPO fala o [Model Context Protocol](https://modelcontextprotocol.io). Qualquer
ferramenta de IA compatível (Claude Code, Claude Desktop, Cursor, Windsurf…) pode usá-lo
como camada de Git: a IA implementa a tarefa e **pede** ao GRIPPO as operações de
versionamento — mas **quem aprova é você**.

```text
IA (Claude Code, Cursor…) ──MCP──► grippo mcp ──► proposta (nada executado)
                                                        │
você, no seu terminal:  grippo approve <id> ◄───────────┘  vê o impacto → confirma → executa
```

## 1. Instalar e configurar

```bash
./install.sh && grippo doctor
grippo --repo /caminho/do/repo mcp --print-config    # imprime os comandos prontos
```

**Claude Code**

```bash
claude mcp add grippo -- grippo --repo /caminho/do/repo mcp
```

**Claude Desktop / Cursor / Windsurf** — adicione ao JSON de servidores MCP:

```json
{
  "mcpServers": {
    "grippo": { "command": "grippo", "args": ["--repo", "/caminho/do/repo", "mcp"] }
  }
}
```

Sem `--repo`, o servidor opera no diretório em que o cliente foi aberto.

## 2. Ferramentas que a IA recebe

| Ferramenta | O que faz |
|---|---|
| `grippo_repo_state` | branch, HEAD, upstream, arquivos, conflitos, operação em andamento |
| `grippo_list_operations` | catálogo de operações, parâmetros e se são só de leitura |
| `grippo_explain_conflict` | fatos do conflito atual para **explicar** por que ocorreu |
| `grippo_propose` | executa leituras na hora; para mudanças, **cria uma proposta** |
| `grippo_proposal_status` | consulta o estado/resultado de uma proposta |

Não existe ferramenta de aprovar nem de executar mudanças: a IA **não consegue** aprovar
sozinha.

## 3. Fluxo de uso

1. Você diz à IA: *"crie a branch feat/export-csv e implemente a exportação"*.
2. A IA propõe a branch; o GRIPPO responde `PROPOSTA p-1a2b3c4d criada — NADA foi executado`.
3. No **seu** terminal: `grippo approve p-1a2b3c4d` → você vê repositório, branch, arquivos,
   consequências, risco e como recuperar → `c` (ou digite `confirmar` se o risco for alto).
4. A IA consulta `grippo_proposal_status` e segue.
5. Ao terminar: *"faça o commit"* → proposta de `add` + `commit` com mensagem Conventional
   Commit → você aprova.

```bash
grippo approve            # a única proposta pendente
grippo approve p-1a2b3c4d # uma específica
grippo proposals          # histórico (pending/done/failed/cancelled/expired)
```

## 4. Por que a IA não consegue fazer besteira

- **Aterramento:** em `grippo_propose` a IA envia `user_request` (suas palavras). Alvos de
  operações destrutivas (branch, commit, tag, caminho) que **não** estejam nele são
  recusados: não dá para "inventar" `branch_delete feature/antiga`.
- **No-op e branches protegidas** são recusados antes de virar proposta.
- **Aprovação fora da IA:** `grippo approve` exige terminal interativo (TTY); `echo
  confirmar | grippo approve` é recusado. Para reforçar também o modo conversacional:
  `grippo config set security.require_tty true`.
- **Confirmação vinculada ao estado:** se o repositório mudou desde a proposta, o impacto
  mostrado é o **atual** e há um aviso.
- **Backup automático** antes de operações destrutivas (veja [PERMISSIONS.md](PERMISSIONS.md)).
- **Validade:** propostas expiram (`security.proposal_ttl_min`, padrão 15 min) e são de uso único.
- **Auditoria:** tudo é registrado em `grippo config path` → auditoria.

> **Limite honesto:** uma IA com acesso a um terminal *com TTY* (pseudo-terminal) na sua
> máquina poderia, em tese, rodar `grippo approve` e digitar a confirmação. Mantenha a
> aprovação no **seu** terminal e não conceda a agentes permissão de executar `grippo approve`.

## 5. Testando sem cliente

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | grippo --repo . mcp
```
