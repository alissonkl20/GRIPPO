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


_SKIP_DIRS = frozenset(
    {"node_modules", "vendor", ".venv", "venv", "__pycache__", "dist", "build", "target", ".cache"}
)


def _subdirs(directory: Path) -> list[Path]:
    """Visible, non-vendored child directories (empty when unreadable)."""
    try:
        children = sorted(p for p in directory.iterdir() if p.is_dir())
    except OSError:
        return []
    return [c for c in children if c.name not in _SKIP_DIRS and not c.name.startswith(".")]


def find_nearby_repositories(start: Path, max_depth: int = 2, limit: int = 8) -> list[Path]:
    """Repositories in subdirectories (bounded, cheap scan) to suggest to the user."""
    found: list[Path] = []
    frontier = [start]
    for _ in range(max_depth):
        next_level: list[Path] = []
        for directory in frontier:
            for child in _subdirs(directory):
                (found if (child / ".git").exists() else next_level).append(child)
            if len(found) >= limit:
                return found[:limit]
        frontier = next_level[:200]
    return found


def _not_a_repo_message(start: Path) -> str:
    message = f"{start} não está dentro de um repositório Git."
    nearby = find_nearby_repositories(start)
    if nearby:
        listing = "\n".join(f"  cd {p}" for p in nearby)
        example = nearby[0].relative_to(start)
        message += (
            f"\nRepositórios encontrados nas subpastas:\n{listing}\n"
            f"Ou, sem mudar de pasta: grippo --repo {example}"
        )
    return message


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
            raise RepositoryError(_not_a_repo_message(start))
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
