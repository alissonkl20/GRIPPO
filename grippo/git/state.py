"""Repository state snapshot (``git status --porcelain=v2 -z``) and its fingerprint."""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from grippo.errors import RepositoryError
from grippo.git.repo import Repository
from grippo.git.runner import run_git

_IN_PROGRESS_MARKERS = (
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
    ("BISECT_LOG", "bisect"),
)


@dataclass(frozen=True)
class FileEntry:
    path: str
    index: str  # X column ('.' = unchanged)
    worktree: str  # Y column
    orig_path: str | None = None
    conflicted: bool = False

    @property
    def staged(self) -> bool:
        return not self.conflicted and self.index != "."

    @property
    def unstaged(self) -> bool:
        return not self.conflicted and self.worktree != "."

    @property
    def label(self) -> str:
        if self.conflicted:
            return "conflito"
        code = self.index if self.index != "." else self.worktree
        return {
            "M": "modificado",
            "A": "novo",
            "D": "removido",
            "R": "renomeado",
            "C": "copiado",
            "T": "tipo alterado",
        }.get(code, code)


@dataclass(frozen=True)
class RepoState:
    root: str
    branch: str | None
    head: str | None  # None on an unborn branch
    head_subject: str | None
    upstream: str | None
    ahead: int | None
    behind: int | None
    entries: tuple[FileEntry, ...]
    untracked: tuple[str, ...]
    in_progress: str | None
    stash_count: int
    fingerprint: str
    is_linked_worktree: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def detached(self) -> bool:
        return self.branch is None

    @property
    def staged(self) -> list[str]:
        return [e.path for e in self.entries if e.staged]

    @property
    def unstaged(self) -> list[str]:
        return [e.path for e in self.entries if e.unstaged]

    @property
    def conflicted(self) -> list[str]:
        return [e.path for e in self.entries if e.conflicted]

    @property
    def changed_paths(self) -> list[str]:
        seen: dict[str, None] = {e.path: None for e in self.entries}
        seen.update({p: None for p in self.untracked})
        return list(seen)

    @property
    def clean(self) -> bool:
        return not self.entries and not self.untracked

    @property
    def short_head(self) -> str:
        return self.head[:8] if self.head else "(sem commits)"

    def summary(self, max_files: int = 25) -> str:
        """Compact text used by the UI and the LLM prompt."""
        lines = [
            f"branch: {self.branch or '(HEAD destacado)'}",
            f"HEAD: {self.short_head} {self.head_subject or ''}".rstrip(),
        ]
        if self.upstream:
            lines.append(f"upstream: {self.upstream} (à frente {self.ahead}, atrás {self.behind})")
        else:
            lines.append("upstream: nenhum")
        if self.in_progress:
            lines.append(f"operação em andamento: {self.in_progress}")
        if self.stash_count:
            lines.append(f"stashes: {self.stash_count}")

        def block(title: str, items: list[str]) -> None:
            if items:
                shown = ", ".join(items[:max_files])
                more = f" (+{len(items) - max_files})" if len(items) > max_files else ""
                lines.append(f"{title} ({len(items)}): {shown}{more}")

        block("conflitos", self.conflicted)
        block("staged", self.staged)
        block("não staged", self.unstaged)
        block("não rastreados", list(self.untracked))
        if self.clean:
            lines.append("working tree limpo")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["staged"] = self.staged
        data["unstaged"] = self.unstaged
        data["conflicted"] = self.conflicted
        data["clean"] = self.clean
        return data


def parse_porcelain_v2(raw: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "oid": None,
        "branch": None,
        "upstream": None,
        "ahead": None,
        "behind": None,
        "entries": [],
        "untracked": [],
    }
    tokens = raw.split("\0")
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if not token:
            continue
        kind = token[0]
        if kind == "#":
            parts = token.split(" ", 2)
            key, value = (parts[1], parts[2]) if len(parts) == 3 else (parts[-1], "")
            if key == "branch.oid":
                info["oid"] = None if value == "(initial)" else value
            elif key == "branch.head":
                info["branch"] = None if value == "(detached)" else value
            elif key == "branch.upstream":
                info["upstream"] = value
            elif key == "branch.ab":
                ahead, behind = value.split()
                info["ahead"], info["behind"] = int(ahead), abs(int(behind))
        elif kind == "1":
            fields = token.split(" ", 8)
            info["entries"].append(FileEntry(fields[8], fields[1][0], fields[1][1]))
        elif kind == "2":
            fields = token.split(" ", 9)
            orig = tokens[i] if i < len(tokens) else None
            i += 1
            info["entries"].append(FileEntry(fields[9], fields[1][0], fields[1][1], orig_path=orig))
        elif kind == "u":
            fields = token.split(" ", 10)
            info["entries"].append(
                FileEntry(fields[10], fields[1][0], fields[1][1], conflicted=True)
            )
        elif kind == "?":
            info["untracked"].append(token[2:])
    return info


def _in_progress(repo: Repository) -> str | None:
    for marker, name in _IN_PROGRESS_MARKERS:
        if (repo.git_dir / marker).exists():
            return name
    return None


_MAX_STAT_PATHS = 5000


def _stat_signature(root: str, paths: list[str]) -> str:
    """mtime/size of changed paths, so edits to an already-modified file change the fingerprint.

    Bounded so a huge un-ignored tree (e.g. node_modules) cannot make every snapshot slow;
    the path list itself is always part of the fingerprint via the status output.
    """
    parts = []
    for rel in sorted(paths)[:_MAX_STAT_PATHS]:
        try:
            st = os.lstat(os.path.join(root, rel))
            parts.append(f"{rel}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{rel}:missing")
    return "\n".join(parts)


def snapshot(repo: Repository, *, timeout: float = 30.0) -> RepoState:
    root = repo.root
    status = run_git(
        root,
        ["status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"],
        timeout=timeout,
    )
    if not status.ok:
        raise RepositoryError(f"git status falhou: {status.error_text()}")
    info = parse_porcelain_v2(status.stdout)
    subject = None
    if info["oid"]:
        log = run_git(root, ["log", "-1", "--format=%s"], timeout=timeout)
        subject = log.stdout.strip() if log.ok else None
    stash = run_git(root, ["stash", "list", "--format=%gd"], timeout=timeout)
    stash_count = len(stash.stdout.splitlines()) if stash.ok else 0
    in_progress = _in_progress(repo)
    entries = tuple(info["entries"])
    untracked = tuple(info["untracked"])
    digest = hashlib.sha256()
    for piece in (
        str(root),
        info["oid"] or "",
        info["branch"] or "",
        info["upstream"] or "",
        str(info["ahead"]),
        str(info["behind"]),
        status.stdout,
        in_progress or "",
        str(stash_count),
        _stat_signature(str(root), [e.path for e in entries] + list(untracked)),
    ):
        digest.update(piece.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
    warnings = []
    if repo.is_linked_worktree:
        warnings.append("este diretório é uma worktree vinculada")
    return RepoState(
        root=str(root),
        branch=info["branch"],
        head=info["oid"],
        head_subject=subject,
        upstream=info["upstream"],
        ahead=info["ahead"],
        behind=info["behind"],
        entries=entries,
        untracked=untracked,
        in_progress=in_progress,
        stash_count=stash_count,
        fingerprint=digest.hexdigest(),
        is_linked_worktree=repo.is_linked_worktree,
        warnings=tuple(warnings),
    )
