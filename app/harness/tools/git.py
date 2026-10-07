from __future__ import annotations

from pathlib import Path

from app.harness.shell.runner import ShellResult, run_shell


def git_status(repo_root: Path) -> ShellResult:
    return run_shell(["git", "status"], cwd=repo_root)


def git_add(repo_root: Path, files: list[str]) -> ShellResult:
    if not files:
        raise ValueError("files must not be empty")
    return run_shell(["git", "add", "--", *files], cwd=repo_root)


def git_commit(repo_root: Path, message: str) -> ShellResult:
    return run_shell(["git", "commit", "-m", message], cwd=repo_root)


def git_push(repo_root: Path, remote: str = "origin", branch: str | None = None) -> ShellResult:
    argv = ["git", "push", remote]
    if branch:
        argv.append(branch)
    return run_shell(argv, cwd=repo_root)


def git_stash_push(repo_root: Path, message: str | None = None) -> ShellResult:
    argv = ["git", "stash", "push"]
    if message:
        argv.extend(["-m", message])
    return run_shell(argv, cwd=repo_root)


def git_checkout_new_branch(repo_root: Path, name: str) -> ShellResult:
    return run_shell(["git", "checkout", "-b", name], cwd=repo_root)


def git_checkout_branch(repo_root: Path, branch: str) -> ShellResult:
    return run_shell(["git", "checkout", branch], cwd=repo_root)
