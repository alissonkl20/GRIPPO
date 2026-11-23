"""Interpretation + repository state → concrete Plan (or a question / answer).

High-level ("virtual") intents are expanded here using the real repository state;
the LLM is never trusted to decide which files to stage or which flags to pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from grippo.agent.router import Interpretation, StepSpec
from grippo.agent.session import PendingChoice
from grippo.engine.executor import GitExecutor
from grippo.engine.plan import Plan
from grippo.errors import ValidationError
from grippo.git.state import RepoState
from grippo.llm.commit import CommitDraft, CommitDrafter, normalize_message, wants_all_changes


@dataclass
class PlanOutcome:
    kind: str  # plan | clarify | answer | noop
    plan: Plan | None = None
    message: str = ""
    pending: PendingChoice | None = None
    draft: CommitDraft | None = None
    notes: list[str] = field(default_factory=list)


class Planner:
    def __init__(self, executor: GitExecutor, drafter: CommitDrafter) -> None:
        self.executor = executor
        self.drafter = drafter

    @property
    def reader(self):  # type: ignore[no-untyped-def]
        return self.executor.reader

    def plan(self, interpretation: Interpretation, state: RepoState) -> PlanOutcome:
        notes = list(interpretation.notes)
        if interpretation.action == "answer":
            return PlanOutcome("answer", message=interpretation.reply, notes=notes)
        if interpretation.action == "clarify":
            return PlanOutcome("clarify", message=interpretation.question, notes=notes)

        steps: list[StepSpec] = []
        warnings: list[str] = []
        draft: CommitDraft | None = None
        for op, args in interpretation.steps:
            expander = _EXPANDERS.get(op)
            if expander is None:
                steps.append((op, self._resolve_remote_revs(args, notes)))
                continue
            outcome = expander(self, args, state, steps)
            if isinstance(outcome, PlanOutcome):
                outcome.notes = notes + outcome.notes
                return outcome
            new_steps, new_warnings, new_draft = outcome
            steps.extend(new_steps)
            warnings.extend(new_warnings)
            draft = new_draft or draft
        try:
            plan = self.executor.build_plan(steps, interpretation.reply, warnings)
        except ValidationError as error:
            return PlanOutcome(
                "clarify", message=f"Não consigo montar a operação: {error}", notes=notes
            )
        return PlanOutcome(
            "plan",
            plan=plan,
            message=interpretation.reply,
            draft=draft,
            notes=notes + (draft.notes if draft else []),
        )

    # ------------------------------------------------------------------ expanders
    def _commit(
        self, args: dict[str, Any], state: RepoState, prior: list[StepSpec]
    ) -> PlanOutcome | tuple[list[StepSpec], list[str], CommitDraft | None]:
        if state.conflicted:
            return PlanOutcome(
                "clarify",
                message="Há conflitos não resolvidos: "
                + ", ".join(state.conflicted)
                + ". Resolva-os antes do commit.",
            )
        if state.in_progress in {"merge", "cherry-pick", "revert", "rebase"}:
            op = f"{state.in_progress.replace('-', '_')}_continue"
            return PlanOutcome(
                "clarify",
                message=f"Há um {state.in_progress} em andamento: "
                f'use "continuar o {state.in_progress}" ({op}) para concluí-lo.',
            )
        guard = self._branch_guard(args.get("branch"), state, "commitar")
        if guard is not None:
            return guard
        request = str(args.get("request") or "")
        explicit_paths = args.get("paths")
        warnings: list[str] = []
        everything = wants_all_changes(request) and not explicit_paths
        if state.staged and not explicit_paths and not everything:
            candidates = state.staged
            draft = self.drafter.draft(request, state, self.reader, candidates, staged_only=True)
            steps: list[StepSpec] = []
            if state.unstaged or state.untracked:
                warnings.append(
                    "somente o que já está staged entra no commit; "
                    f"{len(state.unstaged) + len(state.untracked)} arquivo(s) "
                    'ficam de fora (diga "commit de tudo" para incluí-los)'
                )
        else:
            # Staged files belong to the pool too: a previous `git add` must not
            # make "commit de tudo" silently leave the rest behind.
            pool = state.staged + state.unstaged + list(state.untracked)
            candidates = list(dict.fromkeys(pool))
            if explicit_paths:
                wanted = explicit_paths if isinstance(explicit_paths, list) else [explicit_paths]
                candidates = [
                    c
                    for c in candidates
                    if any(c == w or c.startswith(str(w).rstrip("/") + "/") for w in wanted)
                ] or [str(w) for w in wanted]
            if not candidates:
                return PlanOutcome("noop", message="Nada para commitar: o working tree está limpo.")
            draft = self.drafter.draft(request, state, self.reader, candidates)
            if not draft.files and draft.unrelated:
                listing = ", ".join(draft.unrelated[:12])
                return PlanOutcome(
                    "clarify",
                    message=f'Não identifiquei quais alterações correspondem a "{request}". '
                    f"Arquivos alterados: {listing}. Diga quais arquivos incluir "
                    '(ex.: "commit de src/forms") ou peça "commit de tudo".',
                )
            if not draft.files:
                return PlanOutcome(
                    "noop",
                    message="Nenhum arquivo elegível para commit "
                    "(arquivos sensíveis são sempre excluídos).",
                )
            steps = [("add", {"paths": draft.files})]
        if draft.excluded_sensitive:
            warnings.append(
                "arquivos possivelmente sensíveis NÃO incluídos: "
                + ", ".join(draft.excluded_sensitive)
            )
        if draft.unrelated:
            warnings.append(
                "não incluídos (parecem não relacionados): " + ", ".join(draft.unrelated[:15])
            )
        message = draft.message
        if args.get("message"):
            explicit = normalize_message(str(args["message"]))
            if explicit:
                message = explicit
            else:
                warnings.append(
                    f"mensagem sugerida {str(args['message'])!r} não segue Conventional "
                    "Commits; usando a mensagem gerada (edite com [e])"
                )
        steps.append(("commit", {"message": message}))
        warnings.extend(self._behind_default(state))
        return steps, warnings, draft

    _REV_KEYS = ("rev", "upstream", "base", "target", "start")

    def _resolve_remote_revs(self, args: dict[str, Any], notes: list[str]) -> dict[str, Any]:
        """'merge da main' without a local main → origin/main (said explicitly)."""
        fixed = dict(args)
        for key in self._REV_KEYS:
            value = fixed.get(key)
            if not isinstance(value, str) or not value or self.reader.resolve(value):
                continue
            for remote in self.reader.remotes():
                candidate = f"{remote}/{value}"
                if self.reader.resolve(candidate):
                    fixed[key] = candidate
                    notes.append(f"'{value}' não existe localmente; usando {candidate}")
                    break
        return fixed

    def _behind_default(self, state: RepoState) -> list[str]:
        """Warn when the current branch lacks commits of main/master (stale branch)."""
        if not state.branch or not state.head:
            return []
        for default in ("main", "master"):
            if default == state.branch:
                return []
            ref = default if self.reader.branch_exists(default) else f"origin/{default}"
            if not self.reader.resolve(ref):
                continue
            missing = self.reader.commits(f"HEAD..{ref}", limit=100)
            if missing:
                count = f"{len(missing)}+" if len(missing) == 100 else str(len(missing))
                return [
                    f"{state.branch} não contém {count} commit(s) de {ref}; se ela "
                    f'deveria estar atualizada, considere "faça merge da {default}" antes'
                ]
            return []
        return []

    def _branch_guard(self, wanted: Any, state: RepoState, verb: str) -> PlanOutcome | None:
        """The user named a branch other than the current one: never act on the wrong branch."""
        if not wanted or wanted == state.branch:
            return None
        name = str(wanted)
        exists = self.reader.branch_exists(name) or any(
            r.split("/", 1)[1] == name for r in self.reader.remote_branches()
        )
        if not exists:
            return None  # not a branch name ("commit para o formulário")
        return PlanOutcome(
            "clarify",
            message=f"Você está na branch {state.branch or '(HEAD destacado)'}, mas pediu para "
            f'{verb} em {name}. Nada foi feito. Primeiro mude de branch ("mude para {name}"; '
            "as alterações locais acompanham a troca, e o Git recusa se houver conflito) "
            "e repita o pedido.",
        )

    def _publish(
        self, args: dict[str, Any], state: RepoState, prior: list[StepSpec]
    ) -> PlanOutcome | tuple[list[StepSpec], list[str], None]:
        remotes = self.reader.remotes()
        if not remotes:
            return PlanOutcome(
                "answer",
                message="Nenhum remoto configurado. Adicione um com "
                '"adicione o remoto origin <url>".',
            )
        guard = self._branch_guard(args.get("branch"), state, "enviar")
        if guard is not None:
            return guard
        branch = state.branch
        if not branch:
            return PlanOutcome("clarify", message="HEAD destacado: para qual branch devo enviar?")
        remote = args.get("remote") or (state.upstream.split("/", 1)[0] if state.upstream else None)
        if not remote:
            remote = (
                "origin" if "origin" in remotes else (remotes[0] if len(remotes) == 1 else None)
            )
        if not remote:
            return PlanOutcome("clarify", message=f"Para qual remoto? ({', '.join(remotes)})")
        committing = any(op == "commit" for op, _ in prior)
        warnings = []
        if state.upstream and not committing:
            if state.ahead == 0 and state.behind == 0:
                return PlanOutcome(
                    "noop", message=f"Nada para enviar: {branch} está igual a {state.upstream}."
                )
            if state.behind:
                return PlanOutcome(
                    "clarify",
                    message=f"{branch} está {state.behind} commit(s) atrás de {state.upstream}: "
                    'o push seria rejeitado. Faça pull primeiro ("faça pull") — '
                    "force push só se você pedir explicitamente.",
                )
        if (state.unstaged or state.untracked) and not committing:
            warnings.append("alterações não commitadas não são enviadas")
        push_args: dict[str, Any] = {"remote": remote, "branch": branch}
        if not state.upstream:
            push_args["set_upstream"] = True
        if not committing:
            warnings.extend(self._behind_default(state))
        return [("push", push_args)], warnings, None

    def _sync(
        self, args: dict[str, Any], state: RepoState, prior: list[StepSpec]
    ) -> PlanOutcome | tuple[list[StepSpec], list[str], None]:
        base = args.get("base")
        if not base:
            return PlanOutcome("clarify", message="Atualizar com qual branch?")
        if base == state.branch:
            return PlanOutcome(
                "answer",
                message=f"Você já está em {base}: atualizar uma branch com ela mesma não faz "
                f"nada (nada foi alterado). Para trazer as novidades do remoto use "
                f'"faça pull"; para atualizar outra branch, troque para ela antes.',
            )
        if state.staged or state.unstaged:
            return PlanOutcome(
                "clarify",
                message="Há alterações locais não commitadas. Faça "
                "commit ou stash antes de atualizar a branch (nada foi alterado).",
            )
        remote = state.upstream.split("/", 1)[0] if state.upstream else "origin"
        steps: list[StepSpec] = []
        warnings: list[str] = []
        target = str(base)
        remote_ref = f"{remote}/{base}"
        if remote in self.reader.remotes() and "/" not in target:
            steps.append(("fetch", {"remote": remote}))
            remote_sha = self.reader.resolve(remote_ref)
            local_sha = self.reader.resolve(target) if self.reader.branch_exists(target) else None
            if (
                local_sha
                and remote_sha
                and local_sha != remote_sha
                and (self.reader.is_ancestor(remote_sha, local_sha))
            ):
                # The local branch has commits not pushed yet: rebasing/merging onto the
                # remote copy would silently ignore them.
                warnings.append(
                    f"usando {target} local: ela está à frente de {remote_ref} "
                    "(tem commits ainda não enviados)"
                )
            elif remote_sha or not local_sha:
                target = remote_ref
        elif not self.reader.resolve(target):
            return PlanOutcome("clarify", message=f"Não encontrei a branch {base}.")
        strategy = args.get("strategy")
        if strategy not in {"merge", "rebase"}:
            published = bool(state.upstream)
            rec = "merge" if published else "rebase"
            why = (
                "sua branch já está publicada; merge preserva o histórico"
                if published
                else "sua branch é só local; rebase deixa o histórico linear"
            )
            return PlanOutcome(
                "clarify",
                message=f"Atualizar {state.branch} com {target} via merge ou rebase? "
                f'Recomendo {rec} ({why}). Responda "merge" ou "rebase".',
                pending=PendingChoice("sync_branch", dict(args), "strategy", ("merge", "rebase")),
            )
        if strategy == "merge":
            steps.append(("merge", {"rev": target}))
        else:
            steps.append(("rebase", {"upstream": target}))
        return steps, warnings, None

    def _undo(
        self, args: dict[str, Any], state: RepoState, prior: list[StepSpec]
    ) -> PlanOutcome | tuple[list[StepSpec], list[str], None]:
        if not state.head:
            return PlanOutcome("noop", message="O repositório ainda não tem commits.")
        if not self.reader.resolve("HEAD~1"):
            return PlanOutcome(
                "answer",
                message="Este é o primeiro commit do repositório; "
                "não há commit anterior para onde voltar.",
            )
        keep = args.get("keep_changes", True) not in (False, "false", 0)
        return [("reset", {"mode": "soft" if keep else "hard", "target": "HEAD~1"})], [], None

    def _discard(
        self, args: dict[str, Any], state: RepoState, prior: list[StepSpec]
    ) -> PlanOutcome | tuple[list[StepSpec], list[str], None]:
        paths = args.get("paths") or state.unstaged
        if not paths:
            return PlanOutcome("noop", message="Não há alterações não staged para descartar.")
        warnings = (
            ["arquivos não rastreados não são afetados (isso seria clean)"]
            if state.untracked
            else []
        )
        return [("restore", {"paths": paths})], warnings, None


_EXPANDERS = {
    "commit_changes": Planner._commit,
    "publish": Planner._publish,
    "sync_branch": Planner._sync,
    "undo_last_commit": Planner._undo,
    "discard_changes": Planner._discard,
}
