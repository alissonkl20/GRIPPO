"""One conversational turn: interpret → plan → prepare → (confirm) → execute → verify.

UI-agnostic: the CLI and the HTTP API both drive this class.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from grippo.agent.planner import Planner, PlanOutcome
from grippo.agent.router import Interpretation, fold
from grippo.agent.session import Session
from grippo.engine.executor import GitExecutor
from grippo.engine.permissions import Decision
from grippo.engine.plan import PlanResult, PreparedPlan
from grippo.errors import GrippoError
from grippo.llm.client import LLMMetrics
from grippo.llm.interpreter import Interpreter

log = logging.getLogger(__name__)


@dataclass
class TurnResult:
    kind: str  # answer | clarify | noop | confirm | result | denied | error
    message: str = ""
    prepared: PreparedPlan | None = None
    result: PlanResult | None = None
    outcome: PlanOutcome | None = None
    interpretation: Interpretation | None = None
    metrics: list[LLMMetrics] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Assistant:
    def __init__(
        self, executor: GitExecutor, interpreter: Interpreter, planner: Planner, session: Session
    ) -> None:
        self.executor = executor
        self.interpreter = interpreter
        self.planner = planner
        self.session = session

    def _resolve_pending(self, message: str) -> Interpretation | None:
        pending = self.session.pending
        if pending is None:
            return None
        self.session.pending = None
        answer = fold(message).strip(" .!")
        for choice in pending.choices:
            if answer == choice or answer.endswith(" " + choice) or answer.startswith(choice):
                args = dict(pending.args)
                args[pending.field] = choice
                return Interpretation("execute", [(pending.op, args)], reply="", source="session")
        return None

    def handle(self, message: str) -> TurnResult:
        try:
            return self._handle(message)
        except GrippoError as error:
            log.warning("turno falhou: %s", error)
            self.session.add(message, f"erro: {error}")
            return TurnResult("error", str(error))

    def _handle(self, message: str) -> TurnResult:
        state = self.executor.state()
        interpretation = self._resolve_pending(message)
        if interpretation is None:
            interpretation = self.interpreter.interpret(
                message,
                state,
                self.session,
                self.executor.reader.local_branches(),
                self.executor.reader.remotes(),
            )
        metrics = [self.interpreter.last_metrics] if self.interpreter.last_metrics else []
        outcome = self.planner.plan(interpretation, state)
        if outcome.draft and outcome.draft.metrics:
            metrics.append(outcome.draft.metrics)
        notes = outcome.notes
        if outcome.kind != "plan" or outcome.plan is None:
            self.session.pending = outcome.pending
            self.session.add(message, outcome.message)
            return TurnResult(
                outcome.kind,
                outcome.message,
                outcome=outcome,
                interpretation=interpretation,
                metrics=metrics,
                notes=notes,
            )
        prepared = self.executor.prepare(outcome.plan)
        base = TurnResult(
            "confirm",
            outcome.message,
            prepared=prepared,
            outcome=outcome,
            interpretation=interpretation,
            metrics=metrics,
            notes=notes,
        )
        if not prepared.allowed:
            base.kind, base.message = "denied", prepared.denial_reason or "não permitido"
            self.session.add(message, base.message)
            return base
        if prepared.errors:
            base.kind = "error"
            base.message = "A operação não pode ser executada: " + "; ".join(prepared.errors)
            self.session.add(message, base.message)
            return base
        if not prepared.needs_confirmation:
            base.result = self.executor.execute(prepared)
            base.kind = "result"
            self.session.add(message, f"{_ops(prepared)} → {base.result.status.value}")
            return base
        self.session.add(message, f"propôs {_ops(prepared)} (aguardando confirmação)")
        return base

    def decide(self, prepared: PreparedPlan, decision: Decision) -> PlanResult | None:
        """Apply the user's decision. Returns None if cancelled."""
        authorization = self.executor.authorize(prepared, decision)
        if authorization is None:
            self.session.add("(decisão)", "usuário cancelou")
            return None
        result = self.executor.execute(prepared, authorization)
        self.session.add("(confirmou)", f"{_ops(prepared)} → {result.status.value}")
        return result

    def refresh(self, prepared: PreparedPlan) -> PreparedPlan:
        """Re-prepare the same plan against the current state (after a StateChangedError)."""
        return self.executor.prepare(prepared.plan)


def _ops(prepared: PreparedPlan) -> str:
    return ", ".join(step.op for step in prepared.plan.steps)
