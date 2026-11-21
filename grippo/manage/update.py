"""`grippo update`: install a stable tagged release side by side, validate, switch, roll back.

Never touches the repository the user is managing: the source is mirrored into
~/.local/state/grippo/cache/source.git and exported with `git archive`.
Configuration, models, data, logs and audit live outside the version directories and
are never modified.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grippo import __version__
from grippo.errors import GrippoError
from grippo.manage.install_layout import (
    current_target,
    read_manifest,
    switch_current,
    write_manifest,
)
from grippo.paths import AppPaths, get_paths

_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


class UpdateError(GrippoError):
    pass


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: tuple[tuple[int, int | str], ...] = ()
    text: str = ""

    @property
    def stable(self) -> bool:
        return not self.pre

    def sort_key(self) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
        # SemVer: a pre-release sorts before the release.
        return (self.major, self.minor, self.patch, 0 if self.pre else 1, self.pre)


def parse_version(text: str) -> Version | None:
    match = _TAG.match(text.strip())
    if not match:
        return None
    pre: tuple[tuple[int, int | str], ...] = ()
    if match.group(4):
        pre = tuple((0, int(p)) if p.isdigit() else (1, p) for p in match.group(4).split("."))
    return Version(
        int(match.group(1)), int(match.group(2)), int(match.group(3)), pre, text.strip().lstrip("v")
    )


def _git(
    args: list[str], *, cwd: Path | None = None, timeout: float = 120, binary: bool = False
) -> subprocess.CompletedProcess[Any]:
    git = shutil.which("git")
    if not git:
        raise UpdateError("git não encontrado")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    result = subprocess.run(
        [git, *args],
        cwd=cwd,
        capture_output=True,
        text=not binary,
        timeout=timeout,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr if isinstance(result.stderr, str) else result.stderr.decode()
        raise UpdateError(f"git {args[0]} falhou: {stderr.strip()}")
    return result


def remote_versions(source: str, include_pre: bool) -> list[Version]:
    out = _git(["ls-remote", "--tags", "--refs", source]).stdout
    versions = []
    for line in out.splitlines():
        ref = line.split("\t")[-1]
        version = parse_version(ref.removeprefix("refs/tags/"))
        if version and (include_pre or version.stable):
            versions.append(version)
    return sorted(set(versions), key=Version.sort_key)


def _mirror(paths: AppPaths, source: str) -> Path:
    mirror = paths.cache_dir / "source.git"
    if mirror.exists():
        _git(
            [
                "--git-dir",
                str(mirror),
                "fetch",
                "--force",
                "--tags",
                "--prune",
                source,
                "+refs/tags/*:refs/tags/*",
            ]
        )
    else:
        paths.cache_dir.mkdir(parents=True, exist_ok=True)
        _git(["clone", "--bare", "--quiet", source, str(mirror)], timeout=600)
    return mirror


def _tag_name(mirror: Path, version: Version) -> str:
    for candidate in (f"v{version.text}", version.text):
        try:
            _git(
                [
                    "--git-dir",
                    str(mirror),
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    f"refs/tags/{candidate}",
                ]
            )
            return candidate
        except UpdateError:
            continue
    raise UpdateError(f"tag da versão {version.text} não encontrada")


def changelog_between(text: str, current: Version, target: Version) -> str:
    sections = re.split(r"(?m)^## ", text)
    picked = []
    for section in sections[1:]:
        heading = section.splitlines()[0]
        match = re.search(r"\[?v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\]?", heading)
        version = parse_version(match.group(1)) if match else None
        if version and current.sort_key() < version.sort_key() <= target.sort_key():
            picked.append("## " + section.strip())
    return "\n\n".join(picked) or "(sem entradas no CHANGELOG para estas versões)"


def python_compatible(spec: str, version: tuple[int, int, int] | None = None) -> bool:
    current = version or sys.version_info[:3]
    for clause in (c.strip() for c in spec.split(",") if c.strip()):
        match = re.match(r"^(>=|<=|==|!=|>|<|~=)\s*(\d+(?:\.\d+)*)$", clause)
        if not match:
            return False
        op, raw = match.groups()
        wanted = tuple(int(p) for p in raw.split("."))
        have = tuple(current[: len(wanted)])
        ok = {
            ">=": have >= wanted,
            "<=": have <= wanted,
            "==": have == wanted,
            "!=": have != wanted,
            ">": have > wanted,
            "<": have < wanted,
            "~=": have >= wanted and have[: len(wanted) - 1] == wanted[:-1],
        }[op]
        if not ok:
            return False
    return True


def _export(mirror: Path, tag: str, dest: Path) -> None:
    data = _git(["--git-dir", str(mirror), "archive", "--format=tar", tag], binary=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(dest, filter="data")


def _run(argv: list[str], timeout: float = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def install_version(paths: AppPaths, source_dir: Path, version: Version, python: str) -> Path:
    stamp = time.strftime("%Y%m%d%H%M%S")
    target = paths.versions_dir / f"{version.text}-{stamp}"
    paths.versions_dir.mkdir(parents=True, exist_ok=True)
    try:
        step = _run([python, "-m", "venv", str(target)])
        if step.returncode != 0:
            raise UpdateError(f"falha ao criar venv: {step.stderr.strip()}")
        pip = [
            str(target / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            *os.environ.get("GRIPPO_PIP_ARGS", "").split(),
            str(source_dir),
        ]
        step = _run(pip)
        if step.returncode != 0:
            raise UpdateError(f"falha ao instalar dependências: {step.stderr.strip()[-800:]}")
        binary = target / "bin" / "grippo"
        step = _run([str(binary), "--version"], timeout=60)
        if step.returncode != 0 or version.text not in step.stdout:
            raise UpdateError(f"validação falhou: --version retornou {step.stdout.strip()!r}")
        step = _run([str(binary), "doctor", "--self-test", "--offline"], timeout=300)
        if step.returncode != 0:
            raise UpdateError("testes essenciais falharam:\n" + step.stdout[-1500:])
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def _prune(paths: AppPaths, keep: list[Path]) -> None:
    """Keep only the active and the previous version (rollback target)."""
    keep_resolved = {k.resolve() for k in keep}
    for entry in paths.versions_dir.iterdir():
        if entry.resolve() not in keep_resolved:
            shutil.rmtree(entry, ignore_errors=True)


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in {"s", "sim", "y", "yes"}
    except (EOFError, KeyboardInterrupt):
        return False


def run_update(
    *,
    target: str | None,
    check_only: bool,
    include_pre: bool,
    assume_yes: bool,
    paths: AppPaths | None = None,
) -> int:
    paths = paths or get_paths()
    manifest = read_manifest(paths)
    if manifest is None:
        print(
            "grippo update: esta instalação não foi feita pelo install.sh "
            "(modo desenvolvimento ou pipx). Use git pull / pipx upgrade.",
            file=sys.stderr,
        )
        return 2
    installed = parse_version(str(manifest.get("version") or __version__))
    if installed is None:
        raise UpdateError(f"versão instalada inválida: {manifest.get('version')!r}")
    source = str(manifest.get("source") or "")
    if not source:
        raise UpdateError("manifesto sem 'source'; reinstale com install.sh")
    print(f"versão instalada: {installed.text}\nfonte: {source}")
    versions = remote_versions(source, include_pre or bool(target and "-" in target))
    if not versions:
        print("nenhuma versão publicada (tags vX.Y.Z) encontrada na fonte.")
        return 0
    if target:
        wanted = parse_version(target)
        chosen = next((v for v in versions if wanted and v.sort_key() == wanted.sort_key()), None)
        if chosen is None:
            print(
                f"versão {target} não encontrada. Disponíveis: "
                + ", ".join(v.text for v in versions),
                file=sys.stderr,
            )
            return 2
    else:
        chosen = versions[-1]
        if chosen.sort_key() <= installed.sort_key():
            print(f"já está na versão mais recente ({installed.text}).")
            return 0
    print(
        f"nova versão: {chosen.text}"
        + (" (DOWNGRADE)" if chosen.sort_key() < installed.sort_key() else "")
    )
    mirror = _mirror(paths, source)
    tag = _tag_name(mirror, chosen)
    try:
        changelog = _git(["--git-dir", str(mirror), "show", f"{tag}:CHANGELOG.md"]).stdout
        print("\n" + changelog_between(changelog, installed, chosen) + "\n")
    except UpdateError:
        print("(CHANGELOG.md indisponível nesta versão)")
    pyproject = tomllib.loads(
        _git(["--git-dir", str(mirror), "show", f"{tag}:pyproject.toml"]).stdout
    )
    requires = str(pyproject.get("project", {}).get("requires-python", ""))
    python = str(manifest.get("python") or sys.executable)
    py_version = _run([python, "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"])
    py_tuple = tuple(int(p) for p in py_version.stdout.strip().split("."))
    if requires and not python_compatible(requires, py_tuple):  # type: ignore[arg-type]
        print(
            f"incompatível: {chosen.text} requer Python {requires}; instalado "
            f"{py_version.stdout.strip()}",
            file=sys.stderr,
        )
        return 2
    if check_only:
        return 0
    if not assume_yes and not _confirm(f"Atualizar {installed.text} → {chosen.text}? [s/N] "):
        print("atualização cancelada; nada foi alterado.")
        return 3
    previous = current_target(paths)
    with tempfile.TemporaryDirectory(prefix="grippo-update-") as tmp:
        source_dir = Path(tmp) / "src"
        source_dir.mkdir()
        _export(mirror, tag, source_dir)
        print("instalando e validando em ambiente isolado…")
        try:
            new_dir = install_version(paths, source_dir, chosen, python)
        except UpdateError as error:
            print(
                f"falha: {error}\nrollback: a versão {installed.text} continua ativa.",
                file=sys.stderr,
            )
            return 1
    switch_current(paths, new_dir)
    check = _run([str(paths.launcher), "--version"], timeout=60)
    if check.returncode != 0 or chosen.text not in check.stdout:
        if previous is not None:
            switch_current(paths, previous)
        shutil.rmtree(new_dir, ignore_errors=True)
        print(
            "falha na validação pós-troca; rollback para a versão anterior concluído.",
            file=sys.stderr,
        )
        return 1
    history = list(manifest.get("history") or [])
    history.append(
        {"from": installed.text, "to": chosen.text, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    )
    manifest.update(
        version=chosen.text,
        path=str(new_dir),
        history=history[-20:],
        previous=str(previous) if previous else None,
    )
    write_manifest(paths, manifest)
    _prune(paths, [new_dir] + ([previous] if previous else []))
    print(f"GRIPPO atualizado para {chosen.text} e validado.")
    return 0
