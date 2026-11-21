"""`grippo uninstall`: remove the program; keep user data unless --purge is confirmed."""

from __future__ import annotations

import shutil
import sys

from grippo.manage.install_layout import launcher_is_ours, read_manifest
from grippo.paths import AppPaths, get_paths

PURGE_PHRASE = "remover tudo"


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return ""


def run_uninstall(*, purge: bool, assume_yes: bool, paths: AppPaths | None = None) -> int:
    paths = paths or get_paths()
    manifest = read_manifest(paths)
    program = [
        p
        for p in (paths.versions_dir, paths.current_link, paths.install_manifest, paths.cache_dir)
        if p.exists() or p.is_symlink()
    ]
    launcher = paths.launcher if launcher_is_ours(paths) else None
    if not program and launcher is None:
        print("nenhuma instalação do GRIPPO (install.sh) encontrada.")
        if not purge:
            return 0
    preserved = [
        paths.config_dir,
        paths.models_dir,
        paths.user_data_dir,
        paths.logs_dir,
        paths.audit_dir,
    ]
    print("Será removido:")
    for item in ([launcher] if launcher else []) + program:
        print(f"  - {item}")
    if purge:
        print("Com --purge também serão APAGADOS (configurações, modelos, dados, logs, auditoria):")
        for item in (paths.config_dir, paths.data_dir, paths.state_dir):
            print(f"  - {item}")
    else:
        print("Preservado (remova depois com `grippo uninstall --purge` ou manualmente):")
        for item in preserved:
            print(f"  · {item}")
    if not assume_yes:
        if purge:
            if _ask(f"Digite '{PURGE_PHRASE}' para confirmar: ") != PURGE_PHRASE:
                print("cancelado; nada foi removido.")
                return 3
        elif _ask("Confirmar desinstalação? [s/N] ") not in {"s", "sim", "y", "yes"}:
            print("cancelado; nada foi removido.")
            return 3
    if launcher is not None:
        launcher.unlink()
    for item in program:
        if item.is_symlink() or item.is_file():
            item.unlink()
        else:
            shutil.rmtree(item, ignore_errors=True)
    if purge:
        for item in (paths.config_dir, paths.data_dir, paths.state_dir):
            shutil.rmtree(item, ignore_errors=True)
    version = manifest.get("version") if manifest else None
    print(f"GRIPPO {version or ''} removido.".replace("  ", " "))
    if not purge:
        print("Para reinstalar: ./install.sh (suas configurações serão reaproveitadas).")
    sys.stdout.flush()
    return 0
