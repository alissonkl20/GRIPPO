"""Deterministic executor: the only component that issues mutation tickets.

Flow: ``build_plan`` (normalize) → ``prepare`` (state, risk, impact, policy) →
user decision → ``GitPermissionManager.grant`` → ``execute`` (lock, re-snapshot,
re-validate, verify authorization against *current* state, run, verify, audit).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any

from grippo.engine.audit import AuditLog
from grippo.engine.backup import Backup, BackupError, create_backups
from grippo.engine.operations import OpContext, Risk, get_operation, normalize_args
from grippo.engine.permissions import Authorization, Decision, GitPermissionManager
from grippo.engine.plan import (
    Plan,
    PlanResult,
    PreparedPlan,
    Step,
    StepPreview,
    StepResult,
    StepStatus,
)
from grippo.errors import AuthorizationError, StateChangedError, ValidationError
from grippo.git.lock import RepoLocks
from grippo.git.reader import GitReader
from grippo.git.repo import Repository
from grippo.git.runner import GitResult, _issue_ticket, run_git
from grippo.git.state import RepoState, snapshot
from grippo.logs import redact

log = logging.getLogger(__name__)

_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("please tell me who you are", "empty ident", "unable to auto-detect email"),
        'configure sua identidade: git config user.name "Nome" e git config user.email '
        '"voce@exemplo"',
    ),
    (("index.lock",), "outro processo Git está usando o repositório (index.lock)"),
    (("stale info",), "force-with-lease recusado: o remoto mudou desde o último fetch"),
    (
        ("non-fast-forward", "fetch first", "[rejected]"),
        "o remoto tem commits que você não tem: faça pull (ou fetch + merge/rebase) antes",
    ),
    (
        ("not possible to fast-forward", "diverging branches", "need to specify how to reconcile"),
        "os históricos divergiram: escolha pull com merge ou com rebase",
    ),
    (
        (
            "terminal prompts disabled",
            "authentication failed",
            "could not read username",
            "permission denied (publickey)",
            "batchmode",
        ),
        "falha de autenticação: configure um credential helper ou ssh-agent",
    ),
    (
        (
            "could not read from remote",
            "does not appear to be a git repository",
            "repository not found",
            "could not resolve host",
        ),
        "remoto inacessível ou inexistente",
    ),
    (
        ("couldn't find remote ref", "remote ref does not exist"),
        "a branch remota não existe (talvez tenha sido removida)",
    ),
    (
        ("has no upstream branch", "no upstream configured", "no tracking information"),
        "a branch não tem upstream: use push com set_upstream",
    ),
    (("not fully merged",), "a branch tem commits não mesclados (exclusão forçada é risco alto)"),
    (
        ("would be overwritten", "please commit your changes or stash them"),
        "alterações locais seriam sobrescritas: faça commit ou stash antes",
    ),
    (
        ("conflict", "could not apply", "after resolving the conflicts"),
        "há conflitos: resolva os arquivos, faça add e use *_continue — ou *_abort",
    ),
    (("hook", "pre-commit", "commit-msg"), "um hook do repositório recusou a operação"),
    (("nothing to commit", "no changes added to commit"), "não há nada para commitar"),
)


def explain(result: GitResult) -> str:
    if result.timed_out:
        return "tempo limite excedido: o processo git foi encerrado"
    if result.interrupted:
        return "interrompido: verifique o estado do repositório"
    text = (result.stderr + "\n" + result.stdout).lower()
    for needles, hint in _HINTS:
        if any(n in text for n in needles):
            return hint
    return ""


def _backup_note(backups: list[Backup]) -> str:
    lines = ["\nBackup de segurança criado (nada se perde):"]
    lines += [f"  • {b.what}: {b.ref}" for b in backups]
    lines.append(f"  recuperar: {backups[0].recovery()}")
    return "\n".join(lines)


class GitExecutor:
    def __init__(
        self,
        repo: Repository,
        *,
        permissions: GitPermissionManager,
        audit: AuditLog,
        locks: RepoLocks,
        git_timeout: float = 60.0,
        network_timeout: float = 300.0,
    ) -> None:
        self.repo = repo
        self.permissions = permissions
        self.audit = audit
        self.locks = locks
        self.git_timeout = git_timeout
        self.network_timeout = network_timeout
        self.reader = GitReader(repo, timeout=git_timeout)

    # ------------------------------------------------------------------ helpers
    def _snapshot(self) -> RepoState:
        return snapshot(self.repo, timeout=self.git_timeout)

    def _ctx(self, state: RepoState) -> OpContext:
        return OpContext(self.repo, state, self.reader)

    def state(self) -> RepoState:
        with self.locks.shared(self.repo):
            return self._snapshot()

    def build_plan(
        self,
        steps: Iterable[tuple[str, Mapping[str, Any]]],
        explanation: str = "",
        warnings: Iterable[str] = (),
    ) -> Plan:
        normalized = []
        for name, raw in steps:
            op = get_operation(name)
            normalized.append(Step(op.name, normalize_args(op, raw, self.repo.root)))
        if not normalized:
            raise ValidationError("plano vazio")
        return Plan(tuple(normalized), explanation, tuple(warnings))

    def _binding_extra(self, plan: Plan, ctx: OpContext) -> tuple[str, dict[str, Any]]:
        first = plan.steps[0]
        pre = get_operation(first.op).pre(first.args, ctx)
        return json.dumps(pre, sort_keys=True, default=str), pre

    def _revalidate(self, plan: Plan) -> None:
        """Reject plans whose args were not produced by ``normalize_args``."""
        for step in plan.steps:
            op = get_operation(step.op)
            if normalize_args(op, step.args, self.repo.root) != step.args:
                raise AuthorizationError(f"argumentos não normalizados em {step.op}")

    def _requirement_inputs(self, plan: Plan, ctx: OpContext) -> list[tuple[str, Risk, bool]]:
        items = []
        for step in plan.steps:
            op = get_operation(step.op)
            items.append((op.name, op.assess_risk(step.args, ctx), op.mutating))
        return items

    # ------------------------------------------------------------------ prepare
    def prepare(self, plan: Plan) -> PreparedPlan:
        self._revalidate(plan)
        with self.locks.shared(self.repo):
            state = self._snapshot()
            ctx = self._ctx(state)
            previews = []
            for index, step in enumerate(plan.steps):
                op = get_operation(step.op)
                previews.append(
                    StepPreview(
                        step=step,
                        argv=op.build(step.args, ctx),
                        risk=op.assess_risk(step.args, ctx),
                        mutating=op.mutating,
                        impact=op.impact(
                            step.args,
                            OpContext(
                                self.repo,
                                state,
                                self.reader,
                                tuple(s.op for s in plan.steps[:index]),
                            ),
                        ),
                        # Later steps depend on the outcome of earlier ones; they are
                        # re-checked right before running.
                        errors=op.check(step.args, ctx) if index == 0 else [],
                    )
                )
            extra, _ = self._binding_extra(plan, ctx)
        requirement = self.permissions.requirement(
            [(p.step.op, p.risk, p.mutating) for p in previews]
        )
        return PreparedPlan(
            plan=plan,
            repo_root=str(self.repo.root),
            state=state,
            previews=previews,
            risk=requirement.risk,
            mutating=any(p.mutating for p in previews),
            needs_confirmation=requirement.needs_confirmation,
            allowed=requirement.allowed,
            denial_reason=requirement.reason,
            binding_extra=extra,
        )

    # ------------------------------------------------------------------ execute
    def execute(
        self, prepared: PreparedPlan, authorization: Authorization | None = None
    ) -> PlanResult:
        lock = self.locks.exclusive if prepared.mutating else self.locks.shared
        with lock(self.repo):
            return self._execute_locked(prepared, authorization)

    def _audit_base(self, state: RepoState, prepared: PreparedPlan) -> dict[str, Any]:
        return {
            "repo": str(self.repo.root),
            "branch": state.branch,
            "head": state.head,
            "risk": prepared.risk.label,
            "plan": [{"op": s.op, "args": s.args} for s in prepared.plan.steps],
        }

    def _execute_locked(
        self, prepared: PreparedPlan, authorization: Authorization | None
    ) -> PlanResult:
        plan = prepared.plan
        state = self._snapshot()
        ctx = self._ctx(state)
        try:
            self._revalidate(plan)
            if str(self.repo.root) != prepared.repo_root:
                raise AuthorizationError("plano preparado para outro repositório")
            extra, first_pre = self._binding_extra(plan, ctx)
            current = PreparedPlan(
                plan=plan,
                repo_root=prepared.repo_root,
                state=state,
                previews=[],
                risk=prepared.risk,
                mutating=prepared.mutating,
                needs_confirmation=prepared.needs_confirmation,
                allowed=prepared.allowed,
                denial_reason=prepared.denial_reason,
                binding_extra=extra,
            )
            now = self.permissions.requirement(self._requirement_inputs(plan, ctx))
            if not now.allowed:
                raise AuthorizationError(now.reason or "operação não permitida")
            if (now.needs_confirmation and not prepared.needs_confirmation) or (
                now.risk > prepared.risk
            ):
                raise StateChangedError("o risco da operação mudou; prepare e confirme novamente")
            self.permissions.check(prepared, current.binding(), authorization)
        except AuthorizationError as error:
            self.audit.record("denied", reason=str(error), **self._audit_base(state, prepared))
            raise

        try:
            backups = create_backups(self.repo.root, plan, state, timeout=self.git_timeout)
        except BackupError as error:
            reason = f"backup de segurança falhou; nada foi executado ({error})"
            self.audit.record("denied", reason=reason, **self._audit_base(state, prepared))
            raise AuthorizationError(reason) from error
        if backups:
            self.audit.record(
                "backup",
                backups=[{"ref": b.ref, "sha": b.sha, "what": b.what} for b in backups],
                **self._audit_base(state, prepared),
            )

        results: list[StepResult] = []
        status = StepStatus.DONE
        try:
            for index, step in enumerate(plan.steps):
                result, state = self._run_step(
                    step, state, first_pre if index == 0 else None, prepared
                )
                results.append(result)
                if result.status is not StepStatus.DONE:
                    status = result.status
                    break
        except KeyboardInterrupt:
            status = StepStatus.INTERRUPTED
            state = self._snapshot()
        for step in plan.steps[len(results) :]:
            results.append(StepResult(step, StepStatus.SKIPPED))
        summary = self._summary(status, state)
        if backups:
            summary += _backup_note(backups)
        return PlanResult(status, results, state, summary)

    def _run_step(
        self, step: Step, state: RepoState, pre: dict[str, Any] | None, prepared: PreparedPlan
    ) -> tuple[StepResult, RepoState]:
        op = get_operation(step.op)
        ctx = self._ctx(state)
        errors = op.check(step.args, ctx)
        if errors:
            outcome = StepResult(step, StepStatus.REJECTED, hint="; ".join(errors))
            self.audit.record(
                "execute",
                op=step.op,
                args=step.args,
                status="rejected",
                error=outcome.hint,
                **self._audit_base(state, prepared),
            )
            return outcome, state
        if pre is None:
            pre = op.pre(step.args, ctx)
        argv = op.build(step.args, ctx)
        ticket = _issue_ticket(argv) if op.mutating else None
        timeout = self.network_timeout if op.network else self.git_timeout
        result = run_git(self.repo.root, argv, timeout=timeout, ticket=ticket)
        after = self._snapshot()
        verification = op.verify(step.args, pre, ctx, after, result)
        if result.interrupted:
            status = StepStatus.INTERRUPTED
        elif result.timed_out:
            status = StepStatus.TIMEOUT
        elif not result.ok and op.may_conflict and (after.conflicted or after.in_progress):
            status = StepStatus.CONFLICT
        elif result.ok and verification.ok:
            status = StepStatus.DONE
        else:
            status = StepStatus.FAILED
        outcome = StepResult(
            step=step,
            status=status,
            command=result.command,
            stdout=redact(result.stdout),
            stderr=redact(result.stderr),
            returncode=result.returncode,
            duration_sec=result.duration_sec,
            verification=verification,
            hint=explain(result),
        )
        self.audit.record(
            "execute",
            op=step.op,
            args=step.args,
            command=result.command,
            status=status.value,
            returncode=result.returncode,
            duration_sec=round(result.duration_sec, 3),
            verification=verification.message,
            error=result.error_text() if not result.ok else None,
            **self._audit_base(state, prepared),
        )
        log.info("%s → %s (%s)", result.command, status.value, verification.message)
        return outcome, after

    @staticmethod
    def _summary(status: StepStatus, state: RepoState) -> str:
        if status is StepStatus.DONE:
            return "Operação concluída e verificada."
        if status is StepStatus.CONFLICT and not state.conflicted:
            kind = state.in_progress or "operação"
            return (
                f"{kind} em andamento sem conflitos (provavelmente um commit vazio). "
                f"O repositório está em estado intermediário: use {kind.replace('-', '_')}"
                "_skip/_continue ou _abort."
            )
        if status is StepStatus.CONFLICT:
            return (
                f"Parado com conflitos ({state.in_progress or 'arquivos'}): "
                f"{', '.join(state.conflicted) or 'veja git status'}. "
                "O repositório está em estado intermediário."
            )
        if status is StepStatus.INTERRUPTED:
            extra = f" Operação em andamento: {state.in_progress}." if state.in_progress else ""
            return "Execução interrompida." + extra
        if status is StepStatus.TIMEOUT:
            return "Tempo limite excedido; o processo git foi encerrado. Verifique o estado."
        if status is StepStatus.REJECTED:
            return "Etapa recusada pela validação; nada foi executado a partir dela."
        return "A operação falhou."

    def authorize(self, prepared: PreparedPlan, decision: Decision) -> Authorization | None:
        """Record the user's decision and mint an authorization only for CONFIRM."""
        self.audit.record(
            "decision", decision=decision.value, **self._audit_base(prepared.state, prepared)
        )
        return self.permissions.grant(prepared, decision)

    # ------------------------------------------------------------------ shortcuts
    def run_read(self, op_name: str, args: Mapping[str, Any] | None = None) -> PlanResult:
        plan = self.build_plan([(op_name, args or {})])
        prepared = self.prepare(plan)
        if prepared.mutating:
            raise AuthorizationError(f"{op_name} não é uma operação de leitura")
        return self.execute(prepared)
