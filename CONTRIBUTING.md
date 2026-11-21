# Contribuindo

Obrigado pelo interesse! O GRIPPO é um assistente **exclusivamente Git**: contribuições
que o transformem em agente de código geral estão fora do escopo.

## Ambiente

```bash
git clone <URL_DO_REPOSITORIO> && cd GRIPPO
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## Regras do projeto

- **Segurança primeiro.** Nenhuma operação Git nova sem: parâmetros tipados em
  `grippo/engine/operations.py`, classificação de risco (considerando estado e
  argumentos), impacto/recuperação, pré-condições, verificação posterior e testes em
  repositório descartável (incluindo o teste de "negado sem autorização").
- Nunca `shell=True`; nunca executar `git` fora de `grippo/git/runner.py`.
- A LLM só propõe; validação e autorização ficam no código.
- Testes destrutivos apenas em diretórios temporários (veja `tests/conftest.py`).
- Não declare compatibilidade ou desempenho sem evidência (`grippo bench`, CI).

## Antes do pull request

```bash
ruff check grippo tests && ruff format --check grippo && mypy grippo
bandit -c pyproject.toml -r grippo
pytest                                   # + GRIPPO_TEST_LLM=1 pytest -m llm se mexeu em prompts
```

- Commits no padrão [Conventional Commits](https://www.conventionalcommits.org/pt-br/).
- Atualize `CHANGELOG.md` (seção `Unreleased`) e a documentação afetada.
- Uma mudança por PR, com descrição do problema, da solução e de como foi testada.

## Skills

Novos guias em `grippo/skills/data/<id>/SKILL.md` (frontmatter YAML; `tools:` deve citar
operações existentes do catálogo). Veja `docs/SKILLS.md`.
