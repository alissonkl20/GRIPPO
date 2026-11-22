"""Safety backups: before a destructive step, pin the current state under ``refs/grippo/backup``.

* history-rewriting ops (reset, rebase, branch deletion, force push…) pin HEAD — and the tip
  of a branch, the commit of a tag or the stash being deleted — so those commits stay
  reachable even after ``git gc``;
* any of those ops over uncommitted *tracked* changes also snapshot them with
  ``git stash create`` (an unreferenced commit; working tree and stash list are untouched),
  so even "irrecoverable" work can be brought back. Untracked files are not covered, which is
  why ``clean`` is *not* a backed-up operation.

If a backup is needed and cannot be created, the operation is aborted (fail closed).
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from grippo.engine.plan import Plan, Step
from grippo.errors import GrippoError
from grippo.git.runner import BACKUP_REF_PREFIX, _issue_ticket, run_git
from grippo.git.state import RepoState

BACKUP_OPS = frozenset(
    {
        "reset",
        "rebase",
        "branch_delete",
        "push_delete",
        "push",
        "restore",
        "stash_drop",
        "tag_delete",
        "tag_delete_remote",
        "rm",
    }
)


class BackupError(GrippoError):
    pass


@dataclass(frozen=True)
class Backup:
    ref: str
    sha: str
    what: str

    def recovery(self) -> str:
        return f"git reset --hard {self.ref}   (ou: git branch recuperada {self.ref})"


MAX_SLUG = 24


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")[:MAX_SLUG].strip("-")
    return cleaned or "x"


def needs_backup(plan: Plan, state: RepoState) -> bool:
    for step in plan.steps:
        if step.op == "push" and not (step.args.get("force") or step.args.get("force_with_lease")):
            continue
        if step.op in BACKUP_OPS:
            return True
    return False


def _update_ref(root: Path, ref: str, sha: str, timeout: float) -> None:
    argv = ["update-ref", ref, sha]
    result = run_git(root, argv, timeout=timeout, ticket=_issue_ticket(argv))
    if not result.ok:
        raise BackupError(f"não foi possível criar o backup {ref}: {result.error_text()}")


def _pinned_object(root: Path, step: Step, timeout: float) -> tuple[str, str] | None:
    """The object a deleting step would orphan: (description, sha), if it can be found."""
    if step.op == "branch_delete":
        rev, label = f"refs/heads/{step.args['name']}", f"ponta de {step.args['name']}"
    elif step.op in {"tag_delete", "tag_delete_remote"}:
        name = step.args.get("name") or step.args.get("tag")
        rev, label = f"refs/tags/{name}", f"tag {name}"
    elif step.op == "stash_drop":
        index = step.args.get("index", 0)
        rev, label = f"stash@{{{index}}}", f"stash {index}"
    else:
        return None
    found = run_git(root, ["rev-parse", "--verify", "--quiet", rev], timeout=timeout)
    sha = found.stdout.strip()
    return (label, sha) if found.ok and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def create_backups(
    root: Path, plan: Plan, state: RepoState, *, timeout: float = 30.0
) -> list[Backup]:
    """Create the backups the plan calls for. Raises :class:`BackupError` on any failure."""
    if not needs_backup(plan, state) or not state.head:
        return []
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    unique = secrets.token_hex(2)  # two backups in the same second must not overwrite each other
    base = f"{stamp}-{_slug(state.branch or 'detached')}-{_slug(plan.steps[0].op)}-{unique}"
    backups: list[Backup] = []

    def add(suffix: str, sha: str, what: str) -> None:
        ref = f"{BACKUP_REF_PREFIX}{base}{suffix}"
        _update_ref(root, ref, sha, timeout)
        backups.append(Backup(ref, sha, what))

    add("", state.head, f"HEAD de {state.branch or 'HEAD destacado'}")
    for step in plan.steps:
        pinned = _pinned_object(root, step, timeout)
        if pinned:
            label, sha = pinned
            add(f"-{_slug(label)}", sha, label)
    if state.staged or state.unstaged:
        argv = ["stash", "create"]
        snap = run_git(root, argv, timeout=timeout, ticket=_issue_ticket(argv))
        sha = snap.stdout.strip()
        if snap.ok and re.fullmatch(r"[0-9a-f]{40}", sha):
            add("-wip", sha, "alterações não commitadas (tracked)")
        elif not snap.ok:
            raise BackupError(f"não foi possível salvar as alterações locais: {snap.error_text()}")
    return backups


def list_backups(root: Path, limit: int = 30) -> list[tuple[str, str, str]]:
    result = run_git(
        root,
        [
            "for-each-ref",
            "--sort=-refname",
            f"--count={limit}",
            "--format=%(refname)%09%(objectname:short)%09%(subject)",
            BACKUP_REF_PREFIX,
        ],
        timeout=15.0,
    )
    rows = []
    for line in result.stdout.splitlines():
        ref, _, rest = line.partition("\t")
        sha, _, subject = rest.partition("\t")
        rows.append((ref, sha, subject))
    return rows
