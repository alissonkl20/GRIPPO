from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.harness.skills.loader import SkillDefinition
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
    skill: SkillDefinition
    user_message: str
    snapshot: RepositorySnapshot | None = None
    step: LoopStep = LoopStep.OBSERVE


def observe(ctx: SessionContext) -> SessionContext:
    ctx.snapshot = build_snapshot(ctx.repo_root)
    ctx.step = LoopStep.PLAN
    return ctx
