# Instalação

## Requisitos

| Item | Mínimo | Observação |
|---|---|---|
| Sistema | Linux x86_64 | macOS/Windows: ainda não suportados (veja Compatibilidade) |
| Python | 3.11 com `venv` e `ensurepip` | Debian/Ubuntu: `sudo apt install python3 python3-venv` |
| Git | 2.23 | usa `git switch` / `git restore` |
| Ollama | opcional | necessário para a LLM local; sem ele há o roteador determinístico |
| Rede | durante a instalação | o `pip` baixa as dependências (httpx, rich, prompt_toolkit, PyYAML) |

## Instalar

```bash
git clone <URL_DO_REPOSITORIO> GRIPPO
cd GRIPPO
./install.sh
```

O instalador:

1. detecta o sistema e a distribuição;
2. procura Python ≥ 3.11 (`GRIPPO_PYTHON=/caminho/python` força um interpretador) e Git ≥ 2.23;
3. cria um **ambiente virtual isolado** em `~/.local/share/grippo/versions/<versão>-<data>/`;
4. instala o GRIPPO e valida (`grippo --version` + `grippo doctor --self-test`) **antes**
   de ativar; se falhar, a versão anterior continua ativa;
5. troca atomicamente `~/.local/share/grippo/current` e cria `~/.local/bin/grippo`;
6. cria `~/.config/grippo/config.toml` apenas se não existir (nunca sobrescreve);
7. verifica se `~/.local/bin` está no `PATH`.

Nunca usa `sudo`. Executá-lo de novo é seguro (idempotente): reinstala a versão do
checkout, mantém configuração/modelos/dados/logs e guarda só a versão ativa e a anterior.

Opções:

| Opção | Efeito |
|---|---|
| `--add-to-path` | acrescenta `~/.local/bin` ao `PATH` em `~/.profile` (uma única vez) |
| `--clean` | reinstalação limpa (remove versões instaladas; preserva dados do usuário) |
| `--source URL\|DIR` | instala de outro clone/URL Git |
| `--force-launcher` | substitui um `~/.local/bin/grippo` que não pertence ao GRIPPO |

Variáveis: `GRIPPO_PYTHON`, `GRIPPO_BIN_DIR`, `GRIPPO_PIP_ARGS` (ex.: `--index-url …`
ou `--no-index --find-links ./wheels` para instalação offline), `XDG_*_HOME`.

## PATH e persistência

O comando é um symlink em `~/.local/bin/grippo`. Ele continua funcionando após fechar o
terminal ou reiniciar desde que `~/.local/bin` esteja no `PATH`:

- Ubuntu/Debian já incluem `~/.local/bin` no `PATH` pelo `~/.profile` padrão quando o
  diretório existe no login;
- caso contrário use `./install.sh --add-to-path` ou adicione ao seu shell:
  `export PATH="$HOME/.local/bin:$PATH"`.

Teste: abra um novo terminal e rode `command -v grippo && grippo --version`.

## Diretórios

| Caminho | Conteúdo | Atualização | Desinstalação |
|---|---|---|---|
| `~/.local/bin/grippo` | executável (symlink) | trocado | removido |
| `~/.local/share/grippo/versions/`, `current`, `install.json` | ambientes virtuais | substituídos | removidos |
| `~/.config/grippo/config.toml` | configuração | preservado | preservado (exceto `--purge`) |
| `~/.local/share/grippo/models/`, `data/` | modelos e dados | preservados | preservados (exceto `--purge`) |
| `~/.local/state/grippo/logs/`, `audit/` | logs e auditoria | preservados | preservados (exceto `--purge`) |
| `~/.local/state/grippo/cache/` | cache da atualização | reutilizado | removido |

O repositório que você administra é descoberto a partir do diretório atual
(`git rev-parse --show-toplevel`) ou de `--repo`; o GRIPPO nunca opera sobre a própria
pasta de instalação e não grava nada dentro dos seus repositórios.

## LLM local

```bash
grippo model pull               # baixa llm.model (padrão qwen2.5:3b, ~1,9 GB)
grippo model list               # instalados e carregados (VRAM/RAM)
grippo doctor                   # verifica Ollama, modelo, GPU e memória
```

Configuração de GPU/CPU/contexto: [docs/LLM.md](docs/LLM.md).

## Alternativa: pipx

```bash
pipx install .                  # ambiente isolado gerenciado pelo pipx
```

Funciona, mas `grippo update` e `grippo uninstall` só gerenciam instalações feitas pelo
`install.sh`; com pipx use `pipx upgrade grippo` / `pipx uninstall grippo`.

## Compatibilidade

Testado de fato (execução real do instalador, do ciclo atualização/rollback/remoção e
da suíte de testes):

| Ambiente | Resultado |
|---|---|
| Ubuntu 26.04 LTS, x86_64, Python 3.14.4, Git 2.53.0 | ✔ |

Não testado ainda (sem evidência, portanto **não declarado como compatível**): Debian,
Fedora, Arch, Linux Mint, outras versões do Ubuntu, macOS, Windows/WSL. O código evita
dependências específicas de distribuição (somente Python, Git, `bash`, coreutils), e o
CI executa a suíte em Ubuntu com Python 3.11–3.14 — relate resultados em outras
distribuições abrindo uma issue.

## Problemas comuns

| Sintoma | Solução |
|---|---|
| `módulo venv/ensurepip ausente` | `sudo apt install python3-venv` |
| `grippo: command not found` | `~/.local/bin` fora do `PATH` (veja acima) |
| `Ollama indisponível` | inicie o serviço (`ollama serve` ou `systemctl start ollama`) |
| `modelo … não está instalado` | `grippo model pull` |
| falha de autenticação no push | configure `ssh-agent` ou um credential helper (o GRIPPO não abre prompts de senha) |
