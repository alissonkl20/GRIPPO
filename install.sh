#!/usr/bin/env bash
# GRIPPO installer — user level (no sudo), idempotent, with validation and rollback.
#
#   ./install.sh                 install / upgrade from this checkout
#   ./install.sh --clean         reinstall from scratch (keeps config, models, data, logs)
#   ./install.sh --add-to-path   also add ~/.local/bin to PATH in ~/.profile if missing
#   ./install.sh --source URL    install from another git URL or directory
#
# Layout (XDG):
#   ~/.local/share/grippo/versions/<version>-<stamp>/   isolated venv per version
#   ~/.local/share/grippo/current -> versions/...       switched atomically
#   ~/.local/bin/grippo -> ~/.local/share/grippo/current/bin/grippo
#   ~/.config/grippo/config.toml                        created once, never overwritten
#   ~/.local/state/grippo/{logs,audit,cache}
set -euo pipefail

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE="$SCRIPT_DIR"
CLEAN=0
ADD_TO_PATH=0
ALLOW_UNSUPPORTED=0
FORCE_LAUNCHER=0

while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE="${2:?--source requer um valor}"; shift 2 ;;
    --clean) CLEAN=1; shift ;;
    --add-to-path) ADD_TO_PATH=1; shift ;;
    --allow-unsupported-os) ALLOW_UNSUPPORTED=1; shift ;;
    --force-launcher) FORCE_LAUNCHER=1; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) die "opção desconhecida: $1 (veja --help)" ;;
  esac
done

[ "$(id -u)" -ne 0 ] || die "não execute como root: o GRIPPO é instalado por usuário (sem sudo)."

# ---------------------------------------------------------------- platform
OS="$(uname -s)"
case "$OS" in
  Linux)
    DISTRO="desconhecida"
    if [ -r /etc/os-release ]; then DISTRO="$(. /etc/os-release && echo "${PRETTY_NAME:-$ID}")"; fi
    say "Linux detectado: $DISTRO ($(uname -m))" ;;
  Darwin)
    [ "$ALLOW_UNSUPPORTED" -eq 1 ] || die "macOS ainda não é suportado/testado (use --allow-unsupported-os por sua conta)."
    warn "macOS: instalação experimental, não testada." ;;
  *) die "sistema não suportado: $OS" ;;
esac

# ---------------------------------------------------------------- requirements
PYTHON=""
for candidate in "${GRIPPO_PYTHON:-}" python3.14 python3.13 python3.12 python3.11 python3; do
  [ -n "$candidate" ] || continue
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PYTHON="$(command -v "$candidate")"; break
  fi
done
[ -n "$PYTHON" ] || die "Python >= 3.11 não encontrado. Instale (ex.: sudo apt install python3) e rode novamente."
"$PYTHON" -c 'import venv, ensurepip' 2>/dev/null ||
  die "módulo venv/ensurepip ausente para $PYTHON (Debian/Ubuntu: sudo apt install python3-venv)."
say "Python: $("$PYTHON" --version 2>&1) ($PYTHON)"

command -v git >/dev/null 2>&1 || die "git não encontrado (ex.: sudo apt install git)."
GIT_VERSION="$(git --version | awk '{print $3}')"
"$PYTHON" - "$GIT_VERSION" <<'PY' || die "git >= 2.23 é necessário (encontrado $GIT_VERSION)."
import sys
parts = [int(p) for p in sys.argv[1].split(".")[:2] if p.isdigit()]
sys.exit(0 if tuple(parts) >= (2, 23) else 1)
PY
say "Git: $GIT_VERSION"

# ---------------------------------------------------------------- directories
xdg() { local value="${!1:-}"; case "$value" in /*) echo "$value" ;; *) echo "$HOME/$2" ;; esac; }
CONFIG_DIR="$(xdg XDG_CONFIG_HOME .config)/grippo"
DATA_DIR="$(xdg XDG_DATA_HOME .local/share)/grippo"
STATE_DIR="$(xdg XDG_STATE_HOME .local/state)/grippo"
BIN_DIR="${GRIPPO_BIN_DIR:-$HOME/.local/bin}"
VERSIONS="$DATA_DIR/versions"
CURRENT="$DATA_DIR/current"
LAUNCHER="$BIN_DIR/grippo"
mkdir -p "$CONFIG_DIR" "$DATA_DIR/models" "$DATA_DIR/data" "$VERSIONS" \
         "$STATE_DIR/logs" "$STATE_DIR/audit" "$STATE_DIR/cache" "$BIN_DIR"
chmod 700 "$CONFIG_DIR" "$DATA_DIR" "$STATE_DIR" 2>/dev/null || true

if [ -e "$LAUNCHER" ] && [ ! -L "$LAUNCHER" ] && [ "$FORCE_LAUNCHER" -ne 1 ]; then
  die "$LAUNCHER já existe e não é do GRIPPO. Remova-o ou use --force-launcher."
fi

# ---------------------------------------------------------------- source
WORK="$(mktemp -d "${TMPDIR:-/tmp}/grippo-install.XXXXXX")"
NEW_DIR=""
cleanup() {
  status=$?
  if [ $status -ne 0 ] && [ -n "$NEW_DIR" ] && [ -d "$NEW_DIR" ]; then
    rm -rf "$NEW_DIR"
    warn "instalação falhou; nada foi alterado na versão ativa."
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

if [ -d "$SOURCE" ]; then
  SRC_DIR="$(cd "$SOURCE" && pwd -P)"
  RECORDED_SOURCE="$SRC_DIR"
  if git -C "$SRC_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    ORIGIN="$(git -C "$SRC_DIR" remote get-url origin 2>/dev/null || true)"
    [ -n "$ORIGIN" ] && RECORDED_SOURCE="$ORIGIN"
  fi
else
  say "Clonando $SOURCE"
  GIT_TERMINAL_PROMPT=0 git clone --quiet --depth 1 -- "$SOURCE" "$WORK/src" || die "falha ao clonar $SOURCE"
  SRC_DIR="$WORK/src"
  RECORDED_SOURCE="$SOURCE"
fi
[ -f "$SRC_DIR/pyproject.toml" ] && [ -f "$SRC_DIR/grippo/__init__.py" ] || die "$SRC_DIR não é um checkout do GRIPPO."
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC_DIR/grippo/__init__.py")"
[ -n "$VERSION" ] || die "não foi possível ler a versão em grippo/__init__.py"
say "Instalando GRIPPO $VERSION"

if [ "$CLEAN" -eq 1 ]; then
  say "--clean: removendo versões anteriores (configurações, modelos, dados e logs são preservados)"
  rm -rf "$VERSIONS"/* "$CURRENT" "$STATE_DIR/cache"
  mkdir -p "$STATE_DIR/cache"
fi
PREVIOUS="$(readlink "$CURRENT" 2>/dev/null || true)"

# ---------------------------------------------------------------- build
NEW_DIR="$VERSIONS/$VERSION-$(date +%Y%m%d%H%M%S)-$$"
say "Criando ambiente isolado em $NEW_DIR"
"$PYTHON" -m venv "$NEW_DIR"
# shellcheck disable=SC2086
"$NEW_DIR/bin/python" -m pip install --quiet --disable-pip-version-check ${GRIPPO_PIP_ARGS:-} "$SRC_DIR" ||
  die "falha ao instalar dependências (verifique a conexão ou GRIPPO_PIP_ARGS)."

say "Validando a nova instalação"
"$NEW_DIR/bin/grippo" --version | grep -q "$VERSION" || die "validação falhou: --version"
"$NEW_DIR/bin/grippo" doctor --offline --self-test >"$WORK/doctor.txt" 2>&1 || {
  cat "$WORK/doctor.txt" >&2; die "validação falhou: testes essenciais"; }

# ---------------------------------------------------------------- activate
ln -s "$NEW_DIR" "$DATA_DIR/.current.$$"
mv -T "$DATA_DIR/.current.$$" "$CURRENT"
ln -sfn "$CURRENT/bin/grippo" "$LAUNCHER"
"$LAUNCHER" config init >/dev/null

"$NEW_DIR/bin/python" - "$DATA_DIR/install.json" "$VERSION" "$RECORDED_SOURCE" "$PYTHON" "$NEW_DIR" <<'PY'
import json, os, sys, time
path, version, source, python, new_dir = sys.argv[1:]
try:
    data = json.load(open(path))
except (OSError, ValueError):
    data = {}
history = data.get("history", [])
if data.get("version") and data.get("version") != version:
    history.append({"from": data["version"], "to": version, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
data.update(version=version, source=source, python=python, path=new_dir,
            installed_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), history=history[-20:])
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(data, fh, indent=2)
os.replace(tmp, path)
PY
NEW_DIR_DONE="$NEW_DIR"
NEW_DIR=""  # success: do not remove on exit

# keep only the active and the previous version
for dir in "$VERSIONS"/*; do
  [ -d "$dir" ] || continue
  [ "$dir" = "$NEW_DIR_DONE" ] && continue
  [ -n "$PREVIOUS" ] && [ "$dir" = "$PREVIOUS" ] && continue
  rm -rf "$dir"
done

# ---------------------------------------------------------------- PATH
case ":$PATH:" in
  *":$BIN_DIR:"*) PATH_OK=1 ;;
  *) PATH_OK=0 ;;
esac
if [ "$PATH_OK" -eq 0 ]; then
  if [ "$ADD_TO_PATH" -eq 1 ]; then
    PROFILE="$HOME/.profile"
    if ! grep -qs '# added by GRIPPO installer' "$PROFILE"; then
      printf '\n# added by GRIPPO installer\ncase ":$PATH:" in *":%s:"*) ;; *) export PATH="%s:$PATH" ;; esac\n' \
        "$BIN_DIR" "$BIN_DIR" >> "$PROFILE"
    fi
    say "PATH: $BIN_DIR adicionado em $PROFILE (abra um novo terminal ou rode: . $PROFILE)"
  else
    warn "$BIN_DIR não está no PATH. Adicione ao seu shell:"
    warn "  export PATH=\"$BIN_DIR:\$PATH\"   (ou rode ./install.sh --add-to-path)"
  fi
fi

"$LAUNCHER" --version >/dev/null || die "o executável global não respondeu"
say "GRIPPO $VERSION instalado: $LAUNCHER"
say "Configuração: $CONFIG_DIR/config.toml · Logs: $STATE_DIR/logs"
say "Próximos passos: 'grippo doctor' e, para a LLM local, 'grippo model pull'."
