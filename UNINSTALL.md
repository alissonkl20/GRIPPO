# Desinstalação

```bash
grippo uninstall            # remove o programa; preserva seus dados
grippo uninstall --purge    # remove TAMBÉM configurações, modelos, dados, logs e auditoria
```

## O que é removido

Por padrão:

- `~/.local/bin/grippo` (somente se for o symlink criado pelo instalador);
- `~/.local/share/grippo/versions/`, `current`, `install.json`;
- `~/.local/state/grippo/cache/`.

Preservado (para uma reinstalação aproveitar):

- `~/.config/grippo/` (configuração);
- `~/.local/share/grippo/models/` e `data/`;
- `~/.local/state/grippo/logs/` e `audit/`.

A desinstalação pede confirmação (`s`). Com `--purge`, é preciso digitar exatamente
`remover tudo`; qualquer outra resposta cancela. `--yes` pula a pergunta (uso em scripts).

Modelos baixados pelo Ollama ficam no armazenamento do próprio Ollama e **não** são
removidos pelo GRIPPO (`ollama rm qwen2.5:3b` se quiser liberar espaço).

## Reinstalação limpa

```bash
grippo uninstall
cd GRIPPO && ./install.sh --clean
```

## Remoção manual (se o executável estiver quebrado)

```bash
rm -f ~/.local/bin/grippo
rm -rf ~/.local/share/grippo/versions ~/.local/share/grippo/current \
       ~/.local/share/grippo/install.json ~/.local/state/grippo/cache
# dados do usuário (opcional):
# rm -rf ~/.config/grippo ~/.local/share/grippo ~/.local/state/grippo
```
