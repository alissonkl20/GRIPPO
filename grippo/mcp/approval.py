"""``grippo approve``: the human side of an AI proposal."""

from __future__ import annotations

import sys
from pathlib import Path

from grippo.agent.assistant import TurnResult
from grippo.cli import renderer
from grippo.cli.confirm import confirm_and_run
from grippo.cli.interface import make_reader
from grippo.engine.guard import grounding_problems
from grippo.errors import GrippoError
from grippo.mcp import proposals as states
from grippo.mcp.proposals import Proposal, ProposalStore
from grippo.mcp.tools import _format_result
from grippo.paths import get_paths
from grippo.runtime import build_runtime


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
    else:
        renderer.show_error("Há mais de uma proposta pendente; informe o id:")
        for item in pending:
            renderer.show_info(f"  {item.id}  {item.repo}  [{item.risk}]  {item.user_request[:60]}")
    return None


def run_approve(proposal_id: str | None) -> int:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(
            "grippo: a aprovação exige um terminal interativo (stdin/stdout em TTY). "
            "Isso impede que uma IA com acesso a shell aprove sozinha.",
            file=sys.stderr,
        )
        return 2
    store = ProposalStore(get_paths().proposals_dir)
    proposal = _pick(store, proposal_id)
    if proposal is None:
        return 1
    if not proposal.pending:
        renderer.show_error(
            f"a proposta {proposal.id} está '{proposal.status}' e não pode ser aprovada"
        )
        return 1
    runtime = build_runtime(Path(proposal.repo), use_llm=False)
    try:
        executor = runtime.executor
        plan = executor.build_plan([(s["op"], s["args"]) for s in proposal.steps], proposal.reason)
        problems = grounding_problems(plan, proposal.user_request)
        if problems:
            store.finish(proposal, states.CANCELLED, "recusada: " + "; ".join(problems))
            renderer.show_error("Proposta recusada pela guarda: " + "; ".join(problems))
            return 1
        prepared = executor.prepare(plan)
        renderer.show_info(
            f"Proposta {proposal.id}" + (f" de {proposal.client}" if proposal.client else ""),
            "brand",
        )
        renderer.show_info(f"  pedido original: {proposal.user_request}")
        if proposal.reason:
            renderer.show_info(f"  motivo da IA:    {proposal.reason}")
        if prepared.binding() != proposal.binding:
            renderer.show_info(
                "  ⚠ o repositório mudou desde a proposta; o impacto abaixo é o ATUAL.", "warn"
            )
        if prepared.errors:
            store.finish(
                proposal, states.CANCELLED, "inválida agora: " + "; ".join(prepared.errors)
            )
            renderer.show_error("Não pode ser executada: " + "; ".join(prepared.errors))
            return 1
        result = confirm_and_run(
            runtime.assistant, TurnResult("confirm", prepared=prepared), make_reader(None)
        )
        if result is None:
            store.finish(proposal, states.CANCELLED, "o usuário recusou a proposta")
            return 3
        renderer.show_result(result, show_output=False)
        store.finish(proposal, states.DONE if result.ok else states.FAILED, _format_result(result))
        return 0 if result.ok else 1
    except GrippoError as error:
        store.finish(proposal, states.FAILED, str(error))
        renderer.show_error(str(error))
        return 1
    finally:
        runtime.close()


def run_proposals() -> int:
    store = ProposalStore(get_paths().proposals_dir)
    items = store.list()
    if not items:
        print("nenhuma proposta.")
        return 0
    for item in items[-20:]:
        ops = ", ".join(step["op"] for step in item.steps)
        print(f"{item.id}  {item.status:9} [{item.risk}]  {ops}  — {item.repo}")
    return 0
