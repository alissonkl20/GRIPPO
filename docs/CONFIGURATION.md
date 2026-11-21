# Configuração

Precedência: padrões < `~/.config/grippo/config.toml` < variáveis `GRIPPO_<SEÇÃO>_<CHAVE>`.
O GRIPPO **não** lê `.env` do diretório atual (que é o repositório do usuário).

```bash
grippo config init                      # cria o arquivo com os padrões (não sobrescreve)
grippo config show                      # configuração efetiva
grippo config set llm.num_ctx 8192      # valida antes de gravar
grippo config path                      # onde fica cada coisa
```

## `[llm]`

| Chave | Padrão | Descrição |
|---|---|---|
| `enabled` | `true` | `false` usa só o roteador determinístico |
| `backend` | `"ollama"` | único backend suportado |
| `base_url` | `"http://127.0.0.1:11434"` | endereço do Ollama |
| `model` | `"qwen2.5:3b"` | modelo (nome no Ollama) |
| `model_path` | `""` | GGUF local de referência; registre com `grippo model import CAMINHO --name NOME` |
| `num_ctx` | `4096` | janela de contexto (afeta a memória do KV cache) |
| `num_gpu` | `-1` | camadas na GPU: `-1` automático (máximo que couber), `0` só CPU, `N` offload parcial |
| `num_thread` | `0` | threads de CPU (`0` = padrão do backend) |
| `keep_alive` | `"10m"` | quanto tempo o modelo fica carregado após o uso |
| `temperature` | `0.0` | `0` = propostas reprodutíveis |
| `timeout_sec` | `120` | limite por requisição (inclui carregar o modelo a frio) |
| `connect_timeout_sec` | `3` | conexão com o Ollama |
| `max_history_turns` | `6` | turnos de conversa lembrados |
| `max_prompt_chars` | `12000` | limite do contexto montado para o modelo |
| `max_diff_chars` | `6000` | quanto de diff entra na redação do commit |
| `unload_on_exit` | `false` | descarregar o modelo ao sair |

## `[git]`

| Chave | Padrão | Descrição |
|---|---|---|
| `timeout_sec` | `60` | operações locais |
| `network_timeout_sec` | `300` | fetch/pull/push |
| `lock_timeout_sec` | `30` | espera por outra operação GRIPPO no mesmo repositório |

## `[security]`

| Chave | Padrão | Descrição |
|---|---|---|
| `autonomy_level` | `2` | 0–3, veja [PERMISSIONS.md](PERMISSIONS.md) |
| `auth_ttl_sec` | `300` | validade de uma confirmação |
| `auto_approve` | `[]` | operações moderadas aprovadas automaticamente no nível 3 |
| `protected_branches` | `["main","master","develop"]` | aviso extra em force push |

## `[logging]`

| Chave | Padrão | Descrição |
|---|---|---|
| `level` | `"INFO"` | DEBUG, INFO, WARNING, ERROR |
| `max_bytes` | `1000000` | rotação de `grippo.log` / `errors.log` |
| `backups` | `5` | arquivos rotacionados mantidos |

Logs: `~/.local/state/grippo/logs/grippo.log` (geral) e `errors.log` (erros com
traceback). Cada linha leva o id da sessão; segredos são mascarados.

## Variáveis de ambiente

`GRIPPO_LLM_MODEL`, `GRIPPO_LLM_NUM_GPU`, `GRIPPO_SECURITY_AUTONOMY_LEVEL`, … (qualquer
chave acima). Compatibilidade com 0.1: `GRIPPO_MODEL`, `GRIPPO_OLLAMA_BASE_URL`,
`GRIPPO_SHELL_TIMEOUT_SEC`. Instalação: `GRIPPO_PYTHON`, `GRIPPO_BIN_DIR`,
`GRIPPO_PIP_ARGS`; diretórios: `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`.
