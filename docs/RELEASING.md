# Versionamento e releases

## Política

- [Semantic Versioning](https://semver.org/lang/pt-BR/): `MAJOR.MINOR.PATCH`, tags `vX.Y.Z`.
- Antes de 1.0.0, mudanças incompatíveis sobem o MINOR (`0.2.x → 0.3.0`).
- **Estável**: tag sem sufixo (`v0.2.1`). É o que `grippo update` instala por padrão.
- **Pré-release**: `vX.Y.Z-rc.N`, `-beta.N`, `-alpha.N`. Só com `grippo update --pre`
  ou `--version` explícito. Publicadas no GitHub como *pre-release*.
- Compatibilidade entre versões: o formato de `config.toml`, `install.json` e
  `audit.jsonl` é estendido de forma compatível; chaves removidas são ignoradas com
  aviso em uma versão MINOR antes de virarem erro.

## Checklist de release

1. Atualize `grippo/__init__.py` (`__version__`) e mova as entradas de
   `## [Unreleased]` do `CHANGELOG.md` para `## [X.Y.Z] - AAAA-MM-DD`.
2. Rode localmente: `pytest`, `GRIPPO_TEST_LLM=1 pytest -m llm tests/llm_real`,
   `pytest -m install tests/install`, `ruff`, `mypy`, `bandit`.
3. Commit `chore(release): vX.Y.Z`, tag anotada `git tag -a vX.Y.Z -m "vX.Y.Z"`, push da tag.
4. O workflow `release.yml` reexecuta testes, constrói wheel e sdist, valida os artefatos
   (`twine check`, instalação do wheel em venv limpo, `grippo doctor --self-test`) e cria
   um *draft* de release no GitHub com as notas do CHANGELOG.
5. Revise o draft e publique. A publicação no PyPI (Trusted Publishing) só ocorre se a
   variável do repositório `PYPI_PUBLISH` for `true` e o ambiente `pypi` estiver
   configurado no PyPI para este repositório — nunca sem validação.

Nada é publicado automaticamente sem as etapas de validação acima passarem.
