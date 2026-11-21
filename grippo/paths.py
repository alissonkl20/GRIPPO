"""XDG directory layout.

The installation directory, the user's configuration/state and the Git repository
being managed are three different things and must never be confused.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "grippo"


def _xdg_base(var: str, fallback: str) -> Path:
    value = os.environ.get(var, "").strip()
    # The XDG spec says relative paths must be ignored.
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / fallback


@dataclass(frozen=True)
class AppPaths:
    config_dir: Path
    data_dir: Path
    state_dir: Path
    bin_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def user_data_dir(self) -> Path:
        return self.data_dir / "data"

    @property
    def versions_dir(self) -> Path:
        return self.data_dir / "versions"

    @property
    def current_link(self) -> Path:
        return self.data_dir / "current"

    @property
    def install_manifest(self) -> Path:
        return self.data_dir / "install.json"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def audit_dir(self) -> Path:
        return self.state_dir / "audit"

    @property
    def cache_dir(self) -> Path:
        return self.state_dir / "cache"

    @property
    def locks_dir(self) -> Path:
        return self.state_dir / "locks"

    @property
    def history_file(self) -> Path:
        return self.state_dir / "history"

    @property
    def launcher(self) -> Path:
        return self.bin_dir / APP_NAME

    def ensure(self) -> None:
        for directory in (
            self.config_dir,
            self.models_dir,
            self.user_data_dir,
            self.logs_dir,
            self.audit_dir,
            self.cache_dir,
            self.locks_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def is_inside_install(self, path: Path) -> bool:
        """True when ``path`` lies inside GRIPPO's own installation tree."""
        resolved = path.resolve()
        for root in (self.versions_dir, self.current_link, self.cache_dir):
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False


def get_paths() -> AppPaths:
    """Compute paths from the current environment (cheap; not cached so tests can patch env)."""
    bin_override = os.environ.get("GRIPPO_BIN_DIR", "").strip()
    return AppPaths(
        config_dir=_xdg_base("XDG_CONFIG_HOME", ".config") / APP_NAME,
        data_dir=_xdg_base("XDG_DATA_HOME", ".local/share") / APP_NAME,
        state_dir=_xdg_base("XDG_STATE_HOME", ".local/state") / APP_NAME,
        bin_dir=Path(bin_override) if bin_override else Path.home() / ".local" / "bin",
    )
