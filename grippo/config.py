"""Configuration: defaults < ~/.config/grippo/config.toml < GRIPPO_* environment.

Deliberately does NOT read a ``.env`` from the current directory: the CWD is the
user's repository, and a repository must never be able to reconfigure GRIPPO.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grippo.errors import ConfigError
from grippo.paths import get_paths


@dataclass
class LLMConfig:
    enabled: bool = True
    backend: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:3b"
    # Optional local GGUF file; `grippo model import` registers it in Ollama.
    model_path: str = ""
    num_ctx: int = 4096
    # -1 = let the backend decide (offload as many layers as fit), 0 = CPU only.
    num_gpu: int = -1
    # 0 = backend default.
    num_thread: int = 0
    keep_alive: str = "10m"
    # 0 = deterministic: the same request on the same state yields the same proposal.
    temperature: float = 0.0
    timeout_sec: float = 120.0
    connect_timeout_sec: float = 3.0
    max_history_turns: int = 6
    max_prompt_chars: int = 12000
    max_diff_chars: int = 6000
    unload_on_exit: bool = False


@dataclass
class GitConfig:
    timeout_sec: float = 60.0
    network_timeout_sec: float = 300.0
    lock_timeout_sec: float = 30.0


@dataclass
class SecurityConfig:
    # 0 analysis only · 1 recommend · 2 mutate with confirmation
    # 3 auto-approve the MODERATE ops listed in auto_approve (HIGH always confirms)
    autonomy_level: int = 2
    auth_ttl_sec: int = 300
    auto_approve: list[str] = field(default_factory=list)
    protected_branches: list[str] = field(default_factory=lambda: ["main", "master", "develop"])
    # Refuse to read a confirmation from a pipe/script: only a person at a terminal can approve.
    # Recommended when an AI agent has shell access to this machine.
    require_tty: bool = False
    # Minutes a proposal made by an external AI (MCP) stays approvable.
    proposal_ttl_min: int = 15


@dataclass
class LoggingConfig:
    level: str = "INFO"
    max_bytes: int = 1_000_000
    backups: int = 5


@dataclass
class UIConfig:
    # Animated cat (blinking eye) when the interactive mode starts (~1 s; skipped off-terminal).
    animation: bool = True
    animation_cycles: int = 2


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    git: GitConfig = field(default_factory=GitConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    ui: UIConfig = field(default_factory=UIConfig)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {name: dataclasses.asdict(getattr(self, name)) for name in _SECTIONS}


_SECTIONS: dict[str, type] = {
    "llm": LLMConfig,
    "git": GitConfig,
    "security": SecurityConfig,
    "logging": LoggingConfig,
    "ui": UIConfig,
}

# Environment names kept for compatibility with the 0.1 `.env.example`.
_LEGACY_ENV = {
    "GRIPPO_OLLAMA_BASE_URL": ("llm", "base_url"),
    "GRIPPO_MODEL": ("llm", "model"),
    "GRIPPO_SHELL_TIMEOUT_SEC": ("git", "timeout_sec"),
}


def _coerce(section: str, key: str, value: Any, target_type: type) -> Any:
    where = f"{section}.{key}"
    try:
        if target_type is bool:
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off"}:
                return False
            raise ValueError(value)
        if target_type is int:
            if isinstance(value, bool):
                raise ValueError(value)
            return int(value)
        if target_type is float:
            return float(value)
        if target_type is list:
            if isinstance(value, list):
                return [str(item) for item in value]
            return [item.strip() for item in str(value).split(",") if item.strip()]
        return str(value)
    except (TypeError, ValueError) as error:
        raise ConfigError(f"valor inválido para {where}: {value!r}") from error


def _field_types(cls: type) -> dict[str, type]:
    defaults = cls()
    return {f.name: type(getattr(defaults, f.name)) for f in dataclasses.fields(cls)}


def _apply(config: Config, section: str, key: str, value: Any) -> None:
    if section not in _SECTIONS:
        raise ConfigError(f"seção desconhecida: [{section}]")
    types = _field_types(_SECTIONS[section])
    if key not in types:
        raise ConfigError(f"chave desconhecida: {section}.{key}")
    setattr(getattr(config, section), key, _coerce(section, key, value, types[key]))


def validate(config: Config) -> None:
    if config.llm.backend != "ollama":
        raise ConfigError("llm.backend suportado: 'ollama'")
    if not 0 <= config.security.autonomy_level <= 3:
        raise ConfigError("security.autonomy_level deve estar entre 0 e 3")
    if config.llm.num_ctx < 512:
        raise ConfigError("llm.num_ctx deve ser >= 512")
    if config.llm.max_history_turns < 0:
        raise ConfigError("llm.max_history_turns deve ser >= 0")
    if config.security.auth_ttl_sec <= 0:
        raise ConfigError("security.auth_ttl_sec deve ser > 0")
    for name in ("timeout_sec", "network_timeout_sec", "lock_timeout_sec"):
        if getattr(config.git, name) <= 0:
            raise ConfigError(f"git.{name} deve ser > 0")
    if not 0 <= config.ui.animation_cycles <= 10:
        raise ConfigError("ui.animation_cycles deve estar entre 0 e 10")
    if config.logging.level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigError("logging.level deve ser DEBUG, INFO, WARNING ou ERROR")


def read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: TOML inválido ({error})") from error


def load_config(path: Path | None = None, env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    config = Config()
    file_path = path or get_paths().config_file
    for section, values in read_toml(file_path).items():
        if not isinstance(values, dict):
            raise ConfigError(f"{file_path}: [{section}] deve ser uma tabela")
        for key, value in values.items():
            _apply(config, section, key, value)

    for env_name, (section, key) in _LEGACY_ENV.items():
        if env.get(env_name):
            _apply(config, section, key, env[env_name])
    for env_name, value in env.items():
        if not env_name.startswith("GRIPPO_") or env_name in _LEGACY_ENV:
            continue
        parts = env_name[len("GRIPPO_") :].lower().split("_", 1)
        known = len(parts) == 2 and parts[0] in _SECTIONS
        if known and parts[1] in _field_types(_SECTIONS[parts[0]]):
            _apply(config, parts[0], parts[1], value)
    validate(config)
    return config


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def dump_toml(data: Mapping[str, Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for section, values in data.items():
        lines.append(f"[{section}]")
        lines.extend(f"{key} = {_toml_value(value)}" for key, value in values.items())
        lines.append("")
    return "\n".join(lines)


def write_default_config(path: Path) -> bool:
    """Create the config file if missing. Never overwrites. Returns True if created."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    header = "# GRIPPO — configuração do usuário. Consulte docs/CONFIGURATION.md.\n\n"
    path.write_text(header + dump_toml(Config().to_dict()), encoding="utf-8")
    return True


def set_value(path: Path, dotted_key: str, raw_value: str) -> Config:
    """Set ``section.key`` in the user's config file (validated before writing)."""
    if "." not in dotted_key:
        raise ConfigError("use o formato secao.chave, por exemplo llm.model")
    section, key = dotted_key.split(".", 1)
    data = read_toml(path)
    probe = load_config(path, env={})
    _apply(probe, section, key, raw_value)
    validate(probe)
    data.setdefault(section, {})[key] = getattr(getattr(probe, section), key)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(dump_toml(data), encoding="utf-8")
    tmp.replace(path)
    return probe
