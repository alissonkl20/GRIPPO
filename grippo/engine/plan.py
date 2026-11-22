"""Plans: ordered, validated steps; and their prepared (state-bound) form."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from grippo.engine.operations import Impact, Risk, Verification
from grippo.git.state import RepoState


@dataclass(frozen=True)
class Step:
    op: str
    args: dict[str, Any]

    def canonical(self) -> str:
        return json.dumps({"op": self.op, "args": self.args}, sort_keys=True, ensure_ascii=False)


@dataclass(frozen=True)
class Plan:
    steps: tuple[Step, ...]
    explanation: str = ""
    warnings: tuple[str, ...] = ()

    def digest(self) -> str:
        payload = "\n".join(step.canonical() for step in self.steps)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class StepPreview:
    step: Step
    argv: list[str]
    risk: Risk
    mutating: bool
    impact: Impact
    errors: list[str]


@dataclass
class PreparedPlan:
    plan: Plan
    repo_root: str
    state: RepoState
    previews: list[StepPreview]
    risk: Risk
    mutating: bool
    needs_confirmation: bool
    allowed: bool
    denial_reason: str | None
    # Extra facts bound into the authorization (e.g. the file list `clean` would delete).
    binding_extra: str = ""

    @property
    def errors(self) -> list[str]:
        return self.previews[0].errors if self.previews else []

    def binding(self) -> str:
        parts = [
            self.plan.digest(),
            self.repo_root,
            self.state.branch or "",
            self.state.head or "",
            self.state.fingerprint,
            self.binding_extra,
        ]
        return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


class StepStatus(StrEnum):
    DONE = "done"
    FAILED = "failed"
    CONFLICT = "conflict"
    INTERRUPTED = "interrupted"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    REJECTED = "rejected"


@dataclass
class StepResult:
    step: Step
    status: StepStatus
    command: str = ""
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    duration_sec: float = 0.0
    verification: Verification | None = None
    hint: str = ""


@dataclass
class PlanResult:
    status: StepStatus
    results: list[StepResult] = field(default_factory=list)
    final_state: RepoState | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status is StepStatus.DONE


MAX_OUTPUT_CHARS = 20_000


def format_result(result: PlanResult) -> str:
    """Plain-text account of a run (commands, output, hints, summary), size-capped."""
    parts: list[str] = []
    for step in result.results:
        header = f"$ {step.command}" if step.command else f"({step.step.op}: {step.status.value})"
        body = (step.stdout or step.stderr).strip()
        parts.append(f"{header}\n{body}" if body else header)
        if step.hint:
            parts.append(f"dica: {step.hint}")
    parts.append(result.message)
    text = "\n".join(parts)
    return (
        text if len(text) <= MAX_OUTPUT_CHARS else text[:MAX_OUTPUT_CHARS] + "\n… (saída truncada)"
    )
