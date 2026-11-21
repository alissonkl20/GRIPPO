"""Read-only queries used to compute impacts, risks and verifications."""

from __future__ import annotations

from dataclasses import dataclass

from grippo.git.repo import Repository
from grippo.git.runner import GitResult, run_git


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str

    def __str__(self) -> str:
        return f"{self.sha[:8]} {self.subject}"


class GitReader:
    def __init__(self, repo: Repository, timeout: float = 30.0) -> None:
        self.repo = repo
        self.timeout = timeout

    def run(self, args: list[str]) -> GitResult:
        return run_git(self.repo.root, args, timeout=self.timeout)

    def resolve(self, rev: str) -> str | None:
        result = self.run(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"])
        return result.stdout.strip() or None if result.ok else None

    def commits(self, *revs: str, limit: int = 50) -> list[Commit]:
        result = self.run(["log", f"-n{limit}", "--format=%H%x1f%s", *revs, "--"])
        if not result.ok:
            return []
        out = []
        for line in result.stdout.splitlines():
            sha, _, subject = line.partition("\x1f")
            out.append(Commit(sha, subject))
        return out

    def parent_count(self, rev: str) -> int:
        result = self.run(["rev-list", "--parents", "-n1", rev, "--"])
        return max(len(result.stdout.split()) - 1, 0) if result.ok else 0

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return self.run(["merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0

    def _refs(self, pattern: str) -> list[str]:
        result = self.run(["for-each-ref", "--format=%(refname:short)", pattern])
        return result.stdout.split() if result.ok else []

    def local_branches(self) -> list[str]:
        return self._refs("refs/heads")

    def remote_branches(self) -> list[str]:
        return [r for r in self._refs("refs/remotes") if not r.endswith("/HEAD") and "/" in r]

    def branch_exists(self, name: str) -> bool:
        return self.run(["show-ref", "--verify", "--quiet", f"refs/heads/{name}"]).ok

    def tag_exists(self, name: str) -> bool:
        return self.run(["show-ref", "--verify", "--quiet", f"refs/tags/{name}"]).ok

    def remotes(self) -> list[str]:
        result = self.run(["remote"])
        return result.stdout.split() if result.ok else []

    def remote_url(self, name: str) -> str | None:
        result = self.run(["remote", "get-url", name])
        return result.stdout.strip() if result.ok else None

    def published_commits(self, commits: list[Commit]) -> list[Commit]:
        """Commits already reachable from some remote-tracking branch."""
        if not commits or not self.remote_branches():
            return []
        result = self.run(["rev-list", *[c.sha for c in commits], "--not", "--remotes"])
        if not result.ok:
            return list(commits)  # unknown → assume published (safer: raises the risk)
        unpublished = set(result.stdout.split())
        return [c for c in commits if c.sha not in unpublished]

    def unmerged_commits(self, branch: str, limit: int = 20) -> list[Commit]:
        """Commits only reachable from ``branch`` (lost if it is force-deleted)."""
        return self.commits(
            branch,
            "--not",
            f"--exclude={branch}",
            "--branches",
            "--remotes",
            limit=limit,
        )

    def clean_preview(self, directories: bool, ignored: bool, paths: list[str]) -> list[str]:
        args = ["clean", "-n"]
        if directories:
            args.append("-d")
        if ignored:
            args.append("-x")
        args.append("--")
        args.extend(paths)
        result = self.run(args)
        prefix = "Would remove "
        return [ln[len(prefix) :] for ln in result.stdout.splitlines() if ln.startswith(prefix)]

    def diff_stat(self, *args: str) -> str:
        result = self.run(["diff", "--stat", *args])
        return result.stdout.strip() if result.ok else ""

    def numstat(self, staged: bool) -> dict[str, tuple[int, int]]:
        args = ["diff", "--numstat"] + (["--cached"] if staged else [])
        result = self.run(args)
        out: dict[str, tuple[int, int]] = {}
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                added = int(parts[0]) if parts[0].isdigit() else 0
                removed = int(parts[1]) if parts[1].isdigit() else 0
                out[parts[2]] = (added, removed)
        return out

    def diff_text(self, paths: list[str], *, staged: bool, max_chars: int) -> str:
        args = ["diff", "--no-color", "--unified=2"] + (["--cached"] if staged else [])
        result = self.run([*args, "--", *paths])
        text = result.stdout if result.ok else ""
        return text[:max_chars]
