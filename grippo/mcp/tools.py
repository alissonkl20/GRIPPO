"""The tools GRIPPO exposes over MCP.

Design rule: an AI client can *look* freely and *propose* changes, but it can never
approve them. There is deliberately no "approve"/"execute" tool — approval happens in the
user's terminal (``grippo approve``), outside the AI's reach.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from grippo.engine.guard import grounding_error
from grippo.engine.operations import CATALOG, Param
from grippo.engine.plan import Plan, PreparedPlan, format_result
from grippo.errors import GrippoError
from grippo.git import conflicts
from grippo.mcp.proposals import ProposalStore
from grippo.runtime import Runtime

MAX_STEPS = 5


@dataclass(frozen=True)
class ToolReply:
    text: str
    is_error: bool = False


_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "description": "operation name from grippo_list_operations"},
        "args": {"type": "object", "description": "operation arguments"},
    },
    "required": ["op"],
}

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "grippo_repo_state",
        "description": "Current Git state: branch, HEAD, upstream, staged/unstaged/untracked "
        "files, conflicts and any merge/rebase in progress. Call this before proposing changes.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "grippo_list_operations",
        "description": "Catalog of Git operations GRIPPO can run, with parameters and whether "
        "each is read-only.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "grippo_explain_conflict",
        "description": "Facts about the current conflict (which side changed which file, in "
        "which commits, the conflicting hunks). Use it to EXPLAIN why it happened. Do not "
        "resolve it for the user unless they ask you to edit the files.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "grippo_propose",
        "description": "Run read-only operations immediately, or PROPOSE a mutating plan "
        "(commit, branch, push, rebase, revert, reset…). Every change is only a proposal and is "
        "NEVER executed by this tool, whatever its risk: the user "
        "must approve it in their terminal with `grippo approve <id>`. Include the user's "
        "own words in user_request; destructive targets (branches, commits, tags, paths) "
        "must appear in it or the proposal is refused. Never invent targets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {"type": "array", "items": _STEP_SCHEMA, "maxItems": MAX_STEPS},
                "user_request": {
                    "type": "string",
                    "description": "the user's request, quoted verbatim (required for changes)",
                },
                "reason": {"type": "string", "description": "why this plan fits the request"},
            },
            "required": ["steps"],
        },
    },
    {
        "name": "grippo_proposal_status",
        "description": "Status and result of a proposal (pending / done / failed / cancelled / "
        "expired). Without id, lists pending proposals for this repository.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}},
    },
]


class GrippoTools:
    def __init__(self, runtime: Runtime, store: ProposalStore, client: str = "") -> None:
        self.runtime = runtime
        self.executor = runtime.executor
        self.repo = str(runtime.repo.root)
        self.store = store
        self.client = client
        self._handlers = {
            "grippo_repo_state": self._repo_state,
            "grippo_list_operations": self._list_operations,
            "grippo_explain_conflict": self._explain_conflict,
            "grippo_propose": self._propose,
            "grippo_proposal_status": self._proposal_status,
        }

    def call(self, name: str, arguments: dict[str, Any]) -> ToolReply:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolReply(f"ferramenta desconhecida: {name}", True)
        try:
            return handler(arguments)
        except GrippoError as error:
            return ToolReply(f"erro: {error}", True)

    # ------------------------------------------------------------------ read tools
    def _repo_state(self, _: dict[str, Any]) -> ToolReply:
        state = self.executor.state()
        return ToolReply(f"repositório: {state.root}\n{state.summary()}")

    @staticmethod
    def _list_operations(_: dict[str, Any]) -> ToolReply:
        rows = [
            f"{op.name} [{'mutating' if op.mutating else 'read-only'}, base risk "
            f"{op.risk.label}] ({_signature(op.params)}) — {op.summary}"
            for op in CATALOG.values()
        ]
        return ToolReply("(* = required)\n" + "\n".join(rows))

    def _explain_conflict(self, _: dict[str, Any]) -> ToolReply:
        return ToolReply(conflicts.explain(self.executor.reader, self.executor.state()))

    # ------------------------------------------------------------------ propose
    def _propose(self, arguments: dict[str, Any]) -> ToolReply:
        specs = _parse_steps(arguments.get("steps"))
        if isinstance(specs, str):
            return ToolReply(specs, True)
        user_request = str(arguments.get("user_request") or "").strip()
        reason = str(arguments.get("reason") or "").strip()[:500]

        plan = self.executor.build_plan(specs, reason)
        prepared = self.executor.prepare(plan)
        if not prepared.allowed:
            return ToolReply(prepared.denial_reason or "não permitido", True)
        if prepared.errors:
            return ToolReply("não pode ser executado: " + "; ".join(prepared.errors), True)
        if not prepared.mutating:
            return self._run(prepared)
        refusal = self._refusal(plan, user_request)
        return ToolReply(refusal, True) if refusal else self._store(prepared, user_request, reason)

    @staticmethod
    def _refusal(plan: Plan, user_request: str) -> str | None:
        if not user_request:
            return "user_request é obrigatório para alterações: cite o pedido do usuário"
        problems = grounding_error(plan, user_request)
        return f"proposta recusada pela guarda anti-alucinação: {problems}" if problems else None

    def _run(self, prepared: PreparedPlan) -> ToolReply:
        result = self.executor.execute(prepared)
        return ToolReply(format_result(result), not result.ok)

    def _store(self, prepared: PreparedPlan, user_request: str, reason: str) -> ToolReply:
        proposal = self.store.create(
            repo=self.repo,
            steps=[{"op": step.op, "args": step.args} for step in prepared.plan.steps],
            user_request=user_request,
            reason=reason,
            risk=prepared.risk.label,
            binding=prepared.binding(),
            ttl_sec=self.runtime.config.security.proposal_ttl_min * 60,
            client=self.client,
        )
        return ToolReply(_render_proposal(proposal.id, prepared))

    # ------------------------------------------------------------------ status
    def _proposal_status(self, arguments: dict[str, Any]) -> ToolReply:
        proposal_id = str(arguments.get("id") or "").strip()
        if not proposal_id:
            return ToolReply(self._pending_summary())
        proposal = self.store.get(proposal_id)
        if proposal is None or proposal.repo != self.repo:
            return ToolReply("proposta não encontrada neste repositório", True)
        return ToolReply(
            f"{proposal.id}: {proposal.status}"
            + (f"\n{proposal.result}" if proposal.result else "")
        )

    def _pending_summary(self) -> str:
        pending = self.store.list(repo=self.repo, only_pending=True)
        return (
            "\n".join(
                f"{p.id} [{p.risk}] {', '.join(step['op'] for step in p.steps)}" for p in pending
            )
            or "nenhuma proposta pendente"
        )


def _signature(params: Mapping[str, Param]) -> str:
    return ", ".join(f"{name}{'*' if p.required else ''}:{p.kind}" for name, p in params.items())


def _parse_steps(steps: Any) -> list[tuple[str, dict[str, Any]]] | str:
    """Validated ``(op, args)`` pairs, or an error message for the client."""
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        return f"steps deve ser uma lista de 1 a {MAX_STEPS} itens"
    specs = []
    for item in steps:
        if not isinstance(item, dict) or not isinstance(item.get("op"), str):
            return "cada step precisa de 'op' (texto) e 'args' (objeto)"
        args = item.get("args") or {}
        if not isinstance(args, dict):
            return "'args' deve ser um objeto"
        specs.append((item["op"], args))
    return specs


def _render_proposal(proposal_id: str, prepared: PreparedPlan) -> str:
    lines = [
        f"PROPOSTA {proposal_id} criada — NADA foi executado. Risco: {prepared.risk.label}.",
        "Peça ao usuário para aprovar no terminal dele:",
        f"    grippo approve {proposal_id}",
        "",
    ]
    for preview in prepared.previews:
        lines.append(f"• {preview.step.op} {json.dumps(preview.step.args, ensure_ascii=False)}")
        lines += [f"    - {line}" for line in preview.impact.consequences]
        lines += [f"    ⚠ {line}" for line in preview.impact.warnings]
        if preview.impact.recovery:
            lines.append(f"    recuperação: {preview.impact.recovery}")
    lines += [
        "",
        "Depois da aprovação, consulte o resultado com grippo_proposal_status.",
        "Não repita a proposta nem tente executar de outra forma.",
    ]
    return "\n".join(lines)
