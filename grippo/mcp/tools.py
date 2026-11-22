"""The tools GRIPPO exposes over MCP.

Design rule: an AI client can *look* freely and *propose* changes, but it can never
approve them. There is deliberately no "approve"/"execute" tool — approval happens in the
user's terminal (``grippo approve``), outside the AI's reach.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from grippo.engine.guard import grounding_problems
from grippo.engine.operations import CATALOG
from grippo.engine.plan import PlanResult, PreparedPlan
from grippo.errors import GrippoError
from grippo.git import conflicts
from grippo.mcp.proposals import ProposalStore
from grippo.runtime import Runtime

MAX_STEPS = 5
MAX_OUTPUT_CHARS = 20_000


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
        state = self.runtime.executor.state()
        return ToolReply(f"repositório: {state.root}\n{state.summary()}")

    @staticmethod
    def _list_operations(_: dict[str, Any]) -> ToolReply:
        rows = []
        for op in CATALOG.values():
            params = ", ".join(
                f"{name}{'*' if p.required else ''}:{p.kind}" for name, p in op.params.items()
            )
            kind = "mutating" if op.mutating else "read-only"
            rows.append(f"{op.name} [{kind}, base risk {op.risk.label}] ({params}) — {op.summary}")
        return ToolReply("(* = required)\n" + "\n".join(rows))

    def _explain_conflict(self, _: dict[str, Any]) -> ToolReply:
        report = conflicts.collect(self.runtime.executor.reader, self.runtime.executor.state())
        if report is None:
            return ToolReply("não há conflito em andamento")
        return ToolReply(conflicts.render(report))

    # ------------------------------------------------------------------ propose
    def _propose(self, arguments: dict[str, Any]) -> ToolReply:
        steps = arguments.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
            return ToolReply(f"steps deve ser uma lista de 1 a {MAX_STEPS} itens", True)
        specs: list[tuple[str, dict[str, Any]]] = []
        for item in steps:
            if not isinstance(item, dict) or not isinstance(item.get("op"), str):
                return ToolReply("cada step precisa de 'op' (texto) e 'args' (objeto)", True)
            args = item.get("args") or {}
            if not isinstance(args, dict):
                return ToolReply("'args' deve ser um objeto", True)
            specs.append((item["op"], args))
        user_request = str(arguments.get("user_request") or "").strip()
        reason = str(arguments.get("reason") or "").strip()[:500]

        executor = self.runtime.executor
        plan = executor.build_plan(specs, reason)
        prepared = executor.prepare(plan)
        if not prepared.allowed:
            return ToolReply(prepared.denial_reason or "não permitido", True)
        if prepared.errors:
            return ToolReply("não pode ser executado: " + "; ".join(prepared.errors), True)
        if not prepared.mutating:
            return self._run(prepared)
        if not user_request:
            return ToolReply(
                "user_request é obrigatório para alterações: cite o pedido do usuário", True
            )
        problems = grounding_problems(plan, user_request)
        if problems:
            return ToolReply(
                "proposta recusada pela guarda anti-alucinação: " + "; ".join(problems), True
            )
        return self._store(prepared, specs, user_request, reason)

    def _run(self, prepared: PreparedPlan) -> ToolReply:
        result = self.runtime.executor.execute(prepared)
        return ToolReply(_format_result(result), not result.ok)

    def _store(
        self,
        prepared: PreparedPlan,
        specs: list[tuple[str, dict[str, Any]]],
        user_request: str,
        reason: str,
    ) -> ToolReply:
        proposal = self.store.create(
            repo=str(self.runtime.repo.root),
            steps=[{"op": step.op, "args": step.args} for step in prepared.plan.steps],
            user_request=user_request,
            reason=reason,
            risk=prepared.risk.label,
            binding=prepared.binding(),
            ttl_sec=self.runtime.config.security.proposal_ttl_min * 60,
            client=self.client,
        )
        lines = [
            f"PROPOSTA {proposal.id} criada — NADA foi executado. Risco: {prepared.risk.label}.",
            "Peça ao usuário para aprovar no terminal dele:",
            f"    grippo approve {proposal.id}",
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
        return ToolReply("\n".join(lines))

    # ------------------------------------------------------------------ status
    def _proposal_status(self, arguments: dict[str, Any]) -> ToolReply:
        proposal_id = str(arguments.get("id") or "").strip()
        if not proposal_id:
            pending = self.store.list(repo=str(self.runtime.repo.root), only_pending=True)
            if not pending:
                return ToolReply("nenhuma proposta pendente")
            return ToolReply(
                "\n".join(
                    f"{p.id} [{p.risk}] {', '.join(s['op'] for s in p.steps)}" for p in pending
                )
            )
        proposal = self.store.get(proposal_id)
        if proposal is None or proposal.repo != str(self.runtime.repo.root):
            return ToolReply("proposta não encontrada neste repositório", True)
        text = f"{proposal.id}: {proposal.status}"
        if proposal.result:
            text += f"\n{proposal.result}"
        return ToolReply(text)


def _format_result(result: PlanResult) -> str:
    parts = []
    for step in result.results:
        header = f"$ {step.command}" if step.command else f"({step.step.op}: {step.status.value})"
        body = (step.stdout or step.stderr).strip()
        parts.append(f"{header}\n{body}" if body else header)
        if step.hint:
            parts.append(f"dica: {step.hint}")
    parts.append(result.message)
    text = "\n".join(parts)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n… (saída truncada)"
    return text
