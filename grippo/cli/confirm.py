"""Interactive confirmation loop. Only explicit words authorize; everything else cancels
or re-asks. HIGH risk requires typing the full word ``confirmar``."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from grippo.agent.assistant import Assistant, TurnResult
from grippo.cli import renderer
from grippo.cli.theme import ansi
from grippo.engine.operations import Risk
from grippo.engine.permissions import Decision
from grippo.engine.plan import PlanResult, PreparedPlan
from grippo.errors import GrippoError, StateChangedError

Reader = Callable[[str], str]  # raises EOFError / KeyboardInterrupt


class Choice(Enum):
    CONFIRM = "confirm"
    CANCEL = "cancel"
    DETAILS = "details"
    EDIT = "edit"
    INVALID = "invalid"


_YES = {"c", "confirmar", "confirm", "s", "sim", "y", "yes"}
_YES_HIGH = {"confirmar", "confirm"}
_NO = {"n", "nao", "não", "cancelar", "cancel", "q", "sair", "no"}
_DETAILS = {"d", "detalhes", "details"}
_EDIT = {"e", "editar", "edit"}
MAX_ATTEMPTS = 5


def parse_choice(answer: str, risk: Risk, can_edit: bool) -> Choice:
    text = answer.strip().lower()
    if text in _NO:
        return Choice.CANCEL
    if text in _DETAILS:
        return Choice.DETAILS
    if can_edit and text in _EDIT:
        return Choice.EDIT
    if text in (_YES_HIGH if risk is Risk.HIGH else _YES):
        return Choice.CONFIRM
    return Choice.INVALID


def _key(key: str, label: str, style: str) -> str:
    return f"{ansi('[' + key + ']', style)} {label}"


def _prompt(risk: Risk, can_edit: bool) -> str:
    edit = "  " + _key("e", "editar mensagem", "edit") if can_edit else ""
    if risk is Risk.HIGH:
        return (
            f"{ansi(' RISCO ALTO ', 'high')} digite {ansi('confirmar', 'ok')} para executar, "
            f"{_key('n', 'cancelar', 'no')}  {_key('d', 'detalhes', 'info')}{edit} "
            f"{ansi('❯', 'brand')} "
        )
    return (
        f"{_key('c', 'confirmar', 'ok')}  {_key('n', 'cancelar', 'no')}  "
        f"{_key('d', 'detalhes', 'info')}{edit} {ansi('❯', 'brand')} "
    )


def _commit_message(prepared: PreparedPlan) -> str | None:
    for step in prepared.plan.steps:
        if step.op == "commit":
            return str(step.args.get("message"))
    return None


def _with_message(assistant: Assistant, prepared: PreparedPlan, message: str) -> PreparedPlan:
    steps = [
        (s.op, {**s.args, "message": message} if s.op == "commit" else s.args)
        for s in prepared.plan.steps
    ]
    plan = assistant.executor.build_plan(steps, prepared.plan.explanation, prepared.plan.warnings)
    return assistant.executor.prepare(plan)


def confirm_and_run(assistant: Assistant, turn: TurnResult, read: Reader) -> PlanResult | None:
    """Show the proposal and drive the decision. Returns None when cancelled."""
    prepared = turn.prepared
    if prepared is None:
        raise RuntimeError("prepared ausente")
    warnings: list[str] = []
    details = False
    attempts = 0
    while True:
        renderer.show_prepared(prepared, details=details, warnings=warnings)
        can_edit = _commit_message(prepared) is not None
        try:
            answer = read(_prompt(prepared.risk, can_edit))
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        choice = parse_choice(answer, prepared.risk, can_edit)
        if choice is Choice.CANCEL:
            assistant.decide(prepared, Decision.CANCEL)
            renderer.show_info("Cancelado. Nada foi alterado.", "warn")
            return None
        if choice is Choice.DETAILS:
            details = True
            continue
        if choice is Choice.EDIT:
            try:
                new = read(f"nova mensagem [{_commit_message(prepared)}]: ").strip()
            except (EOFError, KeyboardInterrupt):
                new = ""
            if new:
                try:
                    prepared = _with_message(assistant, prepared, new)
                except GrippoError as error:
                    renderer.show_error(str(error))
            continue
        if choice is Choice.INVALID:
            attempts += 1
            if attempts >= MAX_ATTEMPTS:
                assistant.decide(prepared, Decision.CANCEL)
                renderer.show_info("Resposta não reconhecida; operação cancelada.", "warn")
                return None
            hint = "digite exatamente 'confirmar'" if prepared.risk is Risk.HIGH else "use c ou n"
            renderer.show_info(f"Resposta ambígua — {hint}.", "warn")
            continue
        try:
            return assistant.decide(prepared, Decision.CONFIRM)
        except StateChangedError as error:
            renderer.show_info(f"{error}", "warn")
            prepared = assistant.refresh(prepared)
            warnings = ["proposta atualizada para o estado atual do repositório"]
            details = False
            if prepared.errors:
                renderer.show_prepared(prepared)
                renderer.show_error("A operação não é mais válida: " + "; ".join(prepared.errors))
                return None
