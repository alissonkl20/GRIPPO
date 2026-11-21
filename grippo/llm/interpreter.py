"""Natural language → validated Interpretation (LLM first, deterministic fallback)."""

from __future__ import annotations

import logging
from typing import Any

from grippo.agent.router import Interpretation, StepSpec, route
from grippo.agent.session import Session
from grippo.engine.operations import CATALOG
from grippo.errors import LLMError
from grippo.git.state import RepoState
from grippo.llm.client import LLMMetrics, OllamaClient
from grippo.llm.prompts import INTENT_SCHEMA, VIRTUAL_OPS, system_prompt

log = logging.getLogger(__name__)

_VIRTUAL_PARAMS: dict[str, set[str]] = {
    "commit_changes": {"request", "message", "paths", "branch"},
    "publish": {"remote", "branch"},
    "sync_branch": {"base", "strategy"},
    "undo_last_commit": {"keep_changes"},
    "discard_changes": {"paths"},
}


def allowed_params(op: str) -> set[str]:
    if op in CATALOG:
        return set(CATALOG[op].params)
    return _VIRTUAL_PARAMS.get(op, set())


def sanitize_steps(raw_steps: Any) -> tuple[list[StepSpec], list[str]]:
    """Keep only known ops and known params; report what was dropped."""
    steps: list[StepSpec] = []
    notes: list[str] = []
    if not isinstance(raw_steps, list):
        return steps, ["formato de etapas inválido"]
    for item in raw_steps[:6]:
        if not isinstance(item, dict):
            continue
        op = item.get("op")
        raw_args = item.get("args")
        args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        if op not in CATALOG and op not in VIRTUAL_OPS:
            notes.append(f"operação desconhecida ignorada: {op!r}")
            continue
        allowed = allowed_params(str(op))
        dropped = sorted(set(args) - allowed)
        if dropped:
            notes.append(f"{op}: parâmetros ignorados: {', '.join(dropped)}")
        clean = {k: v for k, v in args.items() if k in allowed and v not in (None, "")}
        steps.append((str(op), clean))
    return steps, notes


class Interpreter:
    def __init__(self, llm: OllamaClient | None, *, max_prompt_chars: int = 12000) -> None:
        self.llm = llm
        self.max_prompt_chars = max_prompt_chars
        self._system = system_prompt()
        self.last_metrics: LLMMetrics | None = None
        self.last_error: str | None = None

    def _context(
        self,
        message: str,
        state: RepoState,
        session: Session,
        branches: list[str],
        remotes: list[str],
    ) -> str:
        parts = [
            "Estado do repositório:\n" + state.summary(max_files=15),
            "Branches locais: " + (", ".join(branches[:30]) or "(nenhuma)"),
            "Remotos: " + (", ".join(remotes) or "(nenhum)"),
        ]
        history = session.render(max_chars=self.max_prompt_chars // 4)
        if history:
            parts.append("Conversa recente:\n" + history)
        parts.append("Pedido: " + message)
        text = "\n".join(parts)
        return text[-self.max_prompt_chars :]

    def interpret(
        self,
        message: str,
        state: RepoState,
        session: Session,
        branches: list[str] | None = None,
        remotes: list[str] | None = None,
    ) -> Interpretation:
        self.last_metrics = None
        self.last_error = None
        routed = route(message, state)
        if routed is not None and routed.fast:
            return routed
        if self.llm is None:
            return routed or self._unknown()
        try:
            data, reply = self.llm.chat_json(
                [
                    {"role": "system", "content": self._system},
                    {
                        "role": "user",
                        "content": self._context(
                            message, state, session, branches or [], remotes or []
                        ),
                    },
                ],
                INTENT_SCHEMA,
                max_tokens=220,
            )
            self.last_metrics = reply.metrics
        except LLMError as error:
            self.last_error = str(error)
            log.warning("LLM falhou ao interpretar: %s", error)
            fallback = routed or self._unknown()
            fallback.notes.append(f"LLM indisponível ({error}); usando roteador determinístico")
            return fallback
        interpretation = self._from_llm(data)
        if interpretation is None:
            self.last_error = f"resposta inválida do modelo: {data!r}"[:300]
            fallback = routed or self._unknown()
            fallback.notes.append(
                "o modelo respondeu algo inválido; usando roteador determinístico"
            )
            return fallback
        return interpretation

    @staticmethod
    def _unknown() -> Interpretation:
        return Interpretation(
            "clarify",
            question='Não entendi qual operação Git você quer. Exemplos: "mostre o que foi '
            'alterado", "faça commit do formulário", "crie a branch fix/x".',
        )

    @staticmethod
    def _from_llm(data: dict[str, Any]) -> Interpretation | None:
        action = data.get("action")
        reply = str(data.get("reply") or "")[:500]
        question = str(data.get("question") or "")[:500]
        if action == "answer":
            return Interpretation("answer", reply=reply or "Ok.", source="llm")
        if action == "clarify":
            return Interpretation(
                "clarify", question=question or reply or "Pode detalhar?", source="llm"
            )
        if action != "execute":
            return None
        steps, notes = sanitize_steps(data.get("steps"))
        if not steps:
            return None
        return Interpretation("execute", steps, reply=reply, source="llm", notes=notes)
