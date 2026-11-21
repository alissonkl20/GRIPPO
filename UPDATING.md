# Atualização

```bash
grippo update --check            # mostra versão instalada, a mais nova e se é compatível
grippo update                    # atualiza para a versão estável mais recente (pede confirmação)
grippo update --version 0.2.0    # versão específica (também permite downgrade)
grippo update --pre              # considera pré-releases (ex.: 0.3.0-rc.1)
```

## Como funciona

1. Lê `~/.local/share/grippo/install.json` (versão instalada e fonte usada pelo `install.sh`).
2. Lista as tags `vMAJOR.MINOR.PATCH` da fonte (`git ls-remote --tags`); pré-releases
   (`-rc.1`, `-beta`) são ignoradas sem `--pre`.
3. Mostra versão atual × nova e o trecho do `CHANGELOG.md` entre elas.
4. Verifica compatibilidade (`requires-python` da nova versão × Python instalado).
5. **Pede confirmação** (ou `--yes`, que é consentimento explícito). Nunca atualiza sozinho.
6. Espelha a fonte em `~/.local/state/grippo/cache/source.git` e exporta a tag com
   `git archive` — o repositório que você está administrando **não é tocado**.
7. Cria um ambiente virtual novo **ao lado** do atual, instala dependências e roda os
   testes essenciais (`grippo --version`, `grippo doctor --self-test`).
8. Só então troca o symlink `current` (operação atômica) e valida o executável global.
9. Se qualquer etapa falhar: a nova versão é descartada e a anterior continua ativa
   (rollback). A versão anterior é mantida em disco para permitir voltar com
   `grippo update --version <anterior>`.

Configurações, modelos, dados, logs e auditoria ficam fora dos diretórios de versão e
nunca são alterados. O `config.toml` não é sobrescrito; chaves novas usam padrões até
você defini-las (`grippo config show` mostra a configuração efetiva).

## Instalações sem `install.sh`

- **Modo desenvolvimento** (`pip install -e .`): use `git pull` e reinstale.
- **pipx**: `pipx upgrade grippo` (ou `pipx install --force .`).

## Atualização manual equivalente

```bash
cd GRIPPO && git fetch --tags && git checkout v0.2.0 && ./install.sh
```
