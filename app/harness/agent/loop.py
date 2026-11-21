from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from app.harness.llm.ollama import generate_commit_message
from app.harness.neural.router import route_intent
from app.harness.policy.engine import evaluate
from app.harness.skills.loader import SkillDefinition
from app.harness.tools.dispatch import pending_files, run_git_tool
from app.harness.worktree.snapshot import RepositorySnapshot, build_snapshot


class LoopStep(str, Enum):
    OBSERVE = "observe"
    PLAN = "plan"
    EXECUTE = "execute"
    FEEDBACK = "feedback"
    DONE = "done"


@dataclass
class SessionContext:
    repo_root: Path
    skill: SkillDefinition | None
    user_message: str
    snapshot: RepositorySnapshot | None = None
    step: LoopStep = LoopStep.OBSERVE


@dataclass
class AgentStep:
    tool: str
    ok: bool
    argv: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


@dataclass
class AgentResult:
    status: str
    intent: str
    message: str
    proposal: dict | None
    steps: list[AgentStep]
    snapshot: RepositorySnapshot


def observe(ctx: SessionContext) -> SessionContext:
    ctx.snapshot = build_snapshot(ctx.repo_root)
    ctx.step = LoopStep.PLAN
    return ctx


def _heuristic_commit_message(files: list[str]) -> str:
    if files and all(
        f.endswith(".md") or f.startswith("docs/") or f == "README.md" for f in files
    ):
        return "docs: add Git lexicon and product vision"
    if any(f.startswith("app/") and f.endswith(".py") for f in files):
        return "feat: add commit and push agent flow"
    if any(f.endswith(".py") for f in files):
        return "feat: update harness"
    return "chore: update repository"


def _proposal_id(head: str | None, files: list[str], message: str, also_push: bool) -> str:
    raw = f"{head or ''}|{also_push}|{message}|{','.join(files)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _commit_message(repo_root: Path, files: list[str]) -> str:
    diff = run_git_tool("git_diff", repo_root, {"paths": files})
    drafted = generate_commit_message(diff.stdout, files)
    if drafted:
        return drafted
    return _heuristic_commit_message(files)


def _failed(intent: str, message: str, snapshot: RepositorySnapshot, steps: list[AgentStep]) -> AgentResult:
    return AgentResult(
        status="failed",
        intent=intent,
        message=message,
        proposal=None,
        steps=steps,
        snapshot=snapshot,
    )


def _execute_tool(
    name: str,
    repo_root: Path,
    args: dict,
    *,
    confirmed: bool,
    snapshot: RepositorySnapshot | None,
    steps: list[AgentStep],
) -> tuple[bool, RepositorySnapshot]:
    decision = evaluate(name, confirmed=confirmed)
    if not decision.allowed:
        steps.append(
            AgentStep(tool=name, ok=False, stderr=decision.reason or "policy_denied")
        )
        return False, snapshot or build_snapshot(repo_root)
    result = run_git_tool(name, repo_root, args, snapshot=snapshot)
    steps.append(
        AgentStep(
            tool=name,
            ok=result.ok,
            argv=result.argv,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    )
    return result.ok, build_snapshot(repo_root)


def _commit_push_flow(
    repo_root: Path,
    snapshot: RepositorySnapshot,
    *,
    confirm: bool,
    also_push: bool,
    intent: str,
) -> AgentResult:
    files = pending_files(snapshot.worktree)
    steps: list[AgentStep] = []
    branch = snapshot.branch.name or "HEAD"

    if not files:
        if also_push:
            return _push_only(repo_root, snapshot, confirm=confirm, intent=intent)
        return AgentResult(
            status="noop",
            intent=intent,
            message="Nada para commitar: worktree limpa.",
            proposal=None,
            steps=steps,
            snapshot=snapshot,
        )

    message = _commit_message(repo_root, files)
    proposal = {
        "id": _proposal_id(snapshot.head.sha, files, message, also_push),
        "files": files,
        "commit_message": message,
        "push": {"remote": "origin", "branch": branch} if also_push else None,
    }

    if not confirm:
        return AgentResult(
            status="needs_confirmation",
            intent=intent,
            message="Confirme para executar git add, commit"
            + (" e push." if also_push else "."),
            proposal=proposal,
            steps=steps,
            snapshot=snapshot,
        )

    ok, snapshot = _execute_tool(
        "git_stage",
        repo_root,
        {"files": files},
        confirmed=True,
        snapshot=snapshot,
        steps=steps,
    )
    if not ok:
        return _failed(intent, "git add falhou.", snapshot, steps)

    ok, snapshot = _execute_tool(
        "git_commit",
        repo_root,
        {"message": message},
        confirmed=True,
        snapshot=snapshot,
        steps=steps,
    )
    if not ok:
        return _failed(intent, "git commit falhou.", snapshot, steps)

    if also_push:
        ok, snapshot = _execute_tool(
            "git_push",
            repo_root,
            {"remote": "origin", "branch": branch},
            confirmed=True,
            snapshot=snapshot,
            steps=steps,
        )
        if not ok:
            return _failed(intent, "Commit criado, mas git push falhou.", snapshot, steps)

    return AgentResult(
        status="done",
        intent=intent,
        message="Commit" + (" e push" if also_push else "") + " concluídos.",
        proposal=proposal,
        steps=steps,
        snapshot=snapshot,
    )


def _push_only(
    repo_root: Path,
    snapshot: RepositorySnapshot,
    *,
    confirm: bool,
    intent: str,
) -> AgentResult:
    steps: list[AgentStep] = []
    branch = snapshot.branch.name or "HEAD"
    proposal = {
        "id": _proposal_id(snapshot.head.sha, [], "", True),
        "files": [],
        "commit_message": None,
        "push": {"remote": "origin", "branch": branch},
    }
    if not confirm:
        return AgentResult(
            status="needs_confirmation",
            intent=intent,
            message="Confirme para executar git push.",
            proposal=proposal,
            steps=steps,
            snapshot=snapshot,
        )
    ok, snapshot = _execute_tool(
        "git_push",
        repo_root,
        {"remote": "origin", "branch": branch},
        confirmed=True,
        snapshot=snapshot,
        steps=steps,
    )
    if not ok:
        return _failed(intent, "git push falhou.", snapshot, steps)
    return AgentResult(
        status="done",
        intent=intent,
        message="Push concluído.",
        proposal=proposal,
        steps=steps,
        snapshot=snapshot,
    )


def run_turn(repo_root: Path, user_message: str, *, confirm: bool = False) -> AgentResult:
    ctx = observe(SessionContext(repo_root=repo_root, skill=None, user_message=user_message))
    snapshot = ctx.snapshot
    assert snapshot is not None
    intent = route_intent(user_message)
    if intent == "commit_push":
        return _commit_push_flow(repo_root, snapshot, confirm=confirm, also_push=True, intent=intent)
    if intent == "commit":
        return _commit_push_flow(repo_root, snapshot, confirm=confirm, also_push=False, intent=intent)
    if intent == "push":
        return _push_only(repo_root, snapshot, confirm=confirm, intent=intent)
    return AgentResult(
        status="done",
        intent="inspect",
        message="Snapshot do repositório (sem mutação).",
        proposal=None,
        steps=[],
        snapshot=snapshot,
    )
