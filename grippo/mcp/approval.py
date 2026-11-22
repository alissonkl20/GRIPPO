"""``grippo approve``: the human side of an AI proposal."""

from __future__ import annotations

import sys
from pathlib import Path

from grippo.agent.assistant import TurnResult
from grippo.cli import renderer
from grippo.cli.confirm import confirm_and_run
from grippo.cli.interface import is_interactive, make_reader
from grippo.engine.guard import grounding_error
from grippo.engine.plan import PreparedPlan, format_result
from grippo.errors import GrippoError
from grippo.mcp.proposals import Proposal, ProposalStore, Status
from grippo.paths import get_paths
from grippo.runtime import Runtime, build_runtime

EXIT_FAILED, EXIT_USAGE, EXIT_DECLINED = 1, 2, 3


def _pick(store: ProposalStore, proposal_id: str | None) -> Proposal | None:
    if proposal_id:
        proposal = store.get(proposal_id)
        if proposal is None:
            renderer.show_error(f"proposta não encontrada: {proposal_id}")
        return proposal
    pending = store.list(only_pending=True)
    if len(pending) == 1:
        return pending[0]
    if not pending:
        renderer.show_info("Nenhuma proposta pendente.", "muted")
        return None
    renderer.show_error("Há mais de uma proposta pendente; informe o id:")
    for item in pending:
        renderer.show_info(f"  {item.id}  {item.repo}  [{item.risk}]  {item.user_request[:60]}")
    return None


def _reject(store: ProposalStore, proposal: Proposal, message: str) -> int:
    store.finish(proposal, Status.CANCELLED, message)
    renderer.show_error(message)
    return EXIT_FAILED


def _show_context(proposal: Proposal, prepared: PreparedPlan) -> None:
    origin = f" de {proposal.client}" if proposal.client else ""
    renderer.show_info(f"Proposta {proposal.id}{origin}", "brand")
    renderer.show_info(f"  pedido original: {proposal.user_request}")
    if proposal.reason:
        renderer.show_info(f"  motivo da IA:    {proposal.reason}")
    if prepared.binding() != proposal.binding:
        renderer.show_info(
            "  ⚠ o repositório mudou desde a proposta; o impacto abaixo é o ATUAL.", "warn"
        )


def _approve(runtime: Runtime, store: ProposalStore, proposal: Proposal) -> int:
    executor = runtime.executor
    plan = executor.build_plan([(s["op"], s["args"]) for s in proposal.steps], proposal.reason)
    if problems := grounding_error(plan, proposal.user_request):
        return _reject(store, proposal, f"Proposta recusada pela guarda: {problems}")
    prepared = executor.prepare(plan)
    _show_context(proposal, prepared)
    if prepared.errors:
        return _reject(store, proposal, "Não pode ser executada: " + "; ".join(prepared.errors))
    result = confirm_and_run(
        runtime.assistant, TurnResult("confirm", prepared=prepared), make_reader(None)
    )
    if result is None:
        store.finish(proposal, Status.CANCELLED, "o usuário recusou a proposta")
        return EXIT_DECLINED
    renderer.show_result(result, show_output=False)
    store.finish(proposal, Status.DONE if result.ok else Status.FAILED, format_result(result))
    return 0 if result.ok else EXIT_FAILED


def run_approve(proposal_id: str | None) -> int:
    if not is_interactive():
        print(
            "grippo: a aprovação exige um terminal interativo (stdin/stdout em TTY). "
            "Isso impede que uma IA com acesso a shell aprove sozinha.",
            file=sys.stderr,
        )
        return EXIT_USAGE
    store = ProposalStore(get_paths().proposals_dir)
    proposal = _pick(store, proposal_id)
    if proposal is None:
        return EXIT_FAILED
    if not proposal.pending:
        renderer.show_error(
            f"a proposta {proposal.id} está '{proposal.status}' e não pode ser aprovada"
        )
        return EXIT_FAILED
    runtime = build_runtime(Path(proposal.repo), use_llm=False)
    try:
        return _approve(runtime, store, proposal)
    except GrippoError as error:
        store.finish(proposal, Status.FAILED, str(error))
        renderer.show_error(str(error))
        return EXIT_FAILED
    finally:
        runtime.close()


def run_proposals() -> int:
    items = ProposalStore(get_paths().proposals_dir).list()
    for item in items[-20:]:
        ops = ", ".join(step["op"] for step in item.steps)
        print(f"{item.id}  {item.status:9} [{item.risk}]  {ops}  — {item.repo}")
    if not items:
        print("nenhuma proposta.")
    return 0
