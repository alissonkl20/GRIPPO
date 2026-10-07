from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.harness.shell.runner import run_shell


class BranchInfo(BaseModel):
    name: str | None = None
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    detached: bool = False


class HeadInfo(BaseModel):
    sha: str | None = None
    short_sha: str | None = None
    message: str | None = None


class WorktreeFiles(BaseModel):
    modified: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    untracked: list[str] = Field(default_factory=list)


class RepositorySnapshot(BaseModel):
    version: int = 1
    repository_root: str
    phase: str
    branch: BranchInfo
    head: HeadInfo
    worktree: WorktreeFiles
    clean: bool
    raw_status_short: str


def _parse_status_short(text: str) -> WorktreeFiles:
    modified: list[str] = []
    added: list[str] = []
    deleted: list[str] = []
    untracked: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        xy = line[:2]
        path = line[3:].strip() if len(line) > 3 else ""
        if xy == "??":
            untracked.append(path)
        elif "M" in xy:
            modified.append(path)
        elif "A" in xy:
            added.append(path)
        elif "D" in xy:
            deleted.append(path)
        elif path:
            modified.append(path)
    return WorktreeFiles(
        modified=modified,
        added=added,
        deleted=deleted,
        untracked=untracked,
    )


def _compute_phase(wt: WorktreeFiles, has_staged: bool) -> str:
    if has_staged:
        return "STAGED"
    if wt.modified or wt.added or wt.deleted or wt.untracked:
        return "MODIFIED"
    return "CLEAN"


def build_snapshot(repo_root: Path) -> RepositorySnapshot:
    root = repo_root.resolve()
    status = run_shell(["git", "status", "--short"], cwd=root)
    branch_r = run_shell(["git", "branch", "--show-current"], cwd=root)
    head_r = run_shell(["git", "rev-parse", "HEAD"], cwd=root)
    msg_r = run_shell(["git", "log", "-1", "--pretty=%s"], cwd=root)

    branch_name = branch_r.stdout.strip() or None
    detached = branch_r.returncode != 0 or not branch_name
    wt = _parse_status_short(status.stdout)
    has_staged = any(line.startswith(("M ", "A ", "D ", "R ", "C ")) for line in status.stdout.splitlines())
    phase = _compute_phase(wt, has_staged)
    clean = not status.stdout.strip()

    return RepositorySnapshot(
        repository_root=str(root),
        phase=phase,
        branch=BranchInfo(name=branch_name, detached=detached),
        head=HeadInfo(
            sha=head_r.stdout.strip() or None,
            short_sha=(head_r.stdout.strip()[:7] if head_r.stdout.strip() else None),
            message=msg_r.stdout.strip() or None,
        ),
        worktree=wt,
        clean=clean,
        raw_status_short=status.stdout,
    )
