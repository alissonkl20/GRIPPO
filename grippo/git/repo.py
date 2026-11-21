"""Discover the repository the user is working in (from the CWD or --repo)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grippo.errors import RepositoryError
from grippo.git.runner import run_git
from grippo.paths import AppPaths, get_paths


@dataclass(frozen=True)
class Repository:
    root: Path
    git_dir: Path
    common_dir: Path

    @property
    def is_linked_worktree(self) -> bool:
        return self.git_dir != self.common_dir

    @property
    def name(self) -> str:
        return self.root.name


def discover(start: Path, *, timeout: float = 15.0, paths: AppPaths | None = None) -> Repository:
    start = start.expanduser()
    if not start.exists():
        raise RepositoryError(f"caminho inexistente: {start}")
    if not start.is_dir():
        start = start.parent
    start = start.resolve()
    result = run_git(
        start,
        [
            "rev-parse",
            "--show-toplevel",
            "--absolute-git-dir",
            "--git-common-dir",
            "--is-bare-repository",
        ],
        timeout=timeout,
    )
    if not result.ok:
        stderr = result.stderr.lower()
        if "dubious ownership" in stderr:
            raise RepositoryError(
                f"Git recusou {start}: dono diferente (safe.directory). "
                "Veja `git config --global --add safe.directory <caminho>`."
            )
        if "not a git repository" in stderr or "must be run in a work tree" in stderr:
            raise RepositoryError(f"{start} não está dentro de um repositório Git.")
        raise RepositoryError(f"falha ao identificar repositório: {result.error_text()}")
    lines = result.stdout.splitlines()
    if len(lines) < 4:
        raise RepositoryError("repositório bare ou sem work tree não é suportado")
    toplevel, git_dir, common_dir, is_bare = lines[:4]
    if is_bare.strip() == "true":
        raise RepositoryError("repositório bare não é suportado")
    common = Path(common_dir)
    if not common.is_absolute():
        common = start / common
    repo = Repository(
        root=Path(toplevel).resolve(), git_dir=Path(git_dir).resolve(), common_dir=common.resolve()
    )
    if (paths or get_paths()).is_inside_install(repo.root):
        raise RepositoryError(
            f"{repo.root} pertence à instalação do GRIPPO; recusando operar sobre ela."
        )
    return repo
