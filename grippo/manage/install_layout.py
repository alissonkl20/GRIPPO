"""Installation layout shared by install.sh, `grippo update` and `grippo uninstall`.

    ~/.local/share/grippo/versions/<version>-<stamp>/   one venv per installed version
    ~/.local/share/grippo/current -> versions/<…>         atomically switched symlink
    ~/.local/share/grippo/install.json                    manifest (version, source, history)
    ~/.local/bin/grippo -> ~/.local/share/grippo/current/bin/grippo

Venvs are never moved (their scripts embed absolute paths); switching versions is a
single rename(2) of the ``current`` symlink, which makes rollback trivial.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from grippo.paths import AppPaths


def read_manifest(paths: AppPaths) -> dict[str, Any] | None:
    try:
        data = json.loads(paths.install_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_manifest(paths: AppPaths, data: dict[str, Any]) -> None:
    tmp = paths.install_manifest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, paths.install_manifest)


def current_target(paths: AppPaths) -> Path | None:
    try:
        return Path(os.readlink(paths.current_link))
    except OSError:
        return None


def switch_current(paths: AppPaths, target: Path) -> None:
    """Atomically point ``current`` at ``target``."""
    tmp = paths.data_dir / f".current.{os.getpid()}"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(target, tmp)
    os.replace(tmp, paths.current_link)


def launcher_is_ours(paths: AppPaths) -> bool:
    launcher = paths.launcher
    if not launcher.is_symlink():
        return False
    try:
        return Path(os.readlink(launcher)).resolve().is_relative_to(
            paths.data_dir.resolve()
        ) or os.readlink(launcher) == str(paths.current_link / "bin" / "grippo")
    except OSError:
        return False
