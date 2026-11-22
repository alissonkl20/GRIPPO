# Testes

Todos os testes usam repositórios descartáveis em diretórios temporários, remotos *bare*
locais, `HOME`/XDG isolados e `GIT_CONFIG_GLOBAL` próprio. Nenhum teste toca o
repositório do GRIPPO ou repositórios reais do usuário.

```bash
pip install -e '.[dev]'
pytest                                   # padrão: unitários, Git real, segurança, concorrência,
                                         # recuperação, e2e e API (sem LLM/instalação/desempenho)
GRIPPO_TEST_LLM=1 pytest -m llm tests/llm_real      # modelo real no Ollama
pytest -m install tests/install          # install.sh/update/rollback/uninstall (lento, rede)
pytest -m perf -s tests/perf             # estabilidade e memória
pytest --cov=grippo --cov-report=term-missing
ruff check grippo tests && ruff format --check grippo && mypy grippo
bandit -c pyproject.toml -r grippo
```

| Pasta | Tipo | Prova o quê | Não prova |
|---|---|---|---|
| `tests/unit` | mocks (inclui transporte HTTP falso para o Ollama) | validação, parsing, política, mapeamento de erros, fallback | comportamento real do modelo |
| `tests/git` | Git real | cada operação + verificação posterior, conflitos, remotos bare | — |
| `tests/safety` | Git real | no-ops, branch protegida, aterramento no pedido, backups (inclusive trabalho não commitado) | — |
| `tests/mcp` | Git real + JSON-RPC em memória | protocolo, propostas, ausência de ferramenta de aprovar, aprovação humana, guarda anti-alucinação | clientes MCP reais |
| `tests/security` | Git real | operações protegidas bloqueadas, bypass, injeção, auditoria | — |
| `tests/concurrency` | Git real, threads e processos | exclusão mútua, isolamento, timeouts de lock | — |
| `tests/recovery` | Git real | timeout com kill do grupo, git morto, Ctrl-C, repositório corrompido | — |
| `tests/e2e` | CLI real em subprocesso + Assistant | cenários de ponta a ponta, subdiretórios, espaços, worktrees | LLM real (usa `--no-llm` ou modelo roteirizado) |
| `tests/api` | FastAPI TestClient | token, ausência de shell, fluxo confirmar | — |
| `tests/llm_real` | **Ollama real** (opt-in) | acurácia de intenções e seleção de arquivos do modelo configurado | outros modelos/hardware |
| `tests/install` | **install.sh real** em HOME sandbox (opt-in) | instalação, uso global, idempotência, update, rollback, uninstall, purge, PATH em shell de login | outras distribuições |
| `tests/perf` | medições (opt-in) | ausência de crescimento de memória, fds, threads e processos | — |

`GRIPPO_LLM_REPORT=arquivo.jsonl` grava os resultados por caso dos testes de LLM real.
