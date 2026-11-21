from __future__ import annotations

from pathlib import Path

from app.harness.shell.runner import ShellResult
from app.harness.tools import git as git_tools
from app.harness.worktree.snapshot import RepositorySnapshot, WorktreeFiles


_SECRET_NAMES = {".env", "credentials.json", "id_rsa"}
_SECRET_SUFFIXES = {".pem", ".key"}


def pending_files(worktree: WorktreeFiles) -> list[str]:
    seen: list[str] = []
    for path in [*worktree.modified, *worktree.added, *worktree.deleted, *worktree.untracked]:
        if path not in seen:
            seen.append(path)
    return seen


def assert_paths_allowed(repo_root: Path, files: list[str], snapshot_files: list[str] | None = None) -> None:
    root = repo_root.resolve()
    allowed = set(snapshot_files) if snapshot_files is not None else None
    for rel in files:
        if rel in _SECRET_NAMES or Path(rel).name in _SECRET_NAMES:
            raise ValueError(f"refusing to stage secret path: {rel}")
        if Path(rel).suffix in _SECRET_SUFFIXES:
            raise ValueError(f"refusing to stage secret path: {rel}")
        if allowed is not None and rel not in allowed:
            raise ValueError(f"path not in snapshot: {rel}")
        resolved = (root / rel).resolve()
        if not str(resolved).startswith(str(root)):
            raise ValueError(f"path escapes repository: {rel}")
        if ".git" in resolved.relative_to(root).parts:
            raise ValueError(f"path inside .git: {rel}")


def run_git_tool(
    name: str,
    repo_root: Path,
    args: dict,
    *,
    snapshot: RepositorySnapshot | None = None,
) -> ShellResult:
    if name == "git_status":
        return git_tools.git_status(repo_root)
    if name == "git_diff":
        return git_tools.git_diff(
            repo_root,
            staged=bool(args.get("staged")),
            paths=args.get("paths"),
        )
    if name == "git_stage":
        files = list(args.get("files") or [])
        snap_files = pending_files(snapshot.worktree) if snapshot else None
        assert_paths_allowed(repo_root, files, snap_files)
        return git_tools.git_add(repo_root, files)
    if name == "git_commit":
        return git_tools.git_commit(repo_root, str(args.get("message") or ""))
    if name == "git_push":
        return git_tools.git_push(
            repo_root,
            remote=str(args.get("remote") or "origin"),
            branch=args.get("branch"),
        )
    raise ValueError(f"unknown tool: {name}")
