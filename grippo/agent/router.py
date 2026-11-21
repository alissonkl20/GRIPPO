"""Deterministic intent router (pt-BR / en).

Used as (1) a fast path for unambiguous read-only requests, skipping the LLM, and
(2) the fallback when the LLM is unavailable or answers with something invalid.
It never invents risky flags: destructive variants are chosen only when the user's
words explicitly ask for them, and they still go through confirmation.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from grippo.git.state import RepoState

StepSpec = tuple[str, dict[str, Any]]

_SHA = r"([0-9a-f]{7,40})"
_NAME = r"([A-Za-z0-9][A-Za-z0-9._/\-]*)"


@dataclass
class Interpretation:
    action: str  # execute | answer | clarify
    steps: list[StepSpec] = field(default_factory=list)
    reply: str = ""
    question: str = ""
    source: str = "router"
    fast: bool = False  # safe to skip the LLM
    notes: list[str] = field(default_factory=list)


def fold(text: str) -> str:
    """Lowercase without accents (so 'alterações' matches 'alteracoes')."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _clean_name(raw: str) -> str:
    return raw.strip().strip("\"'`.,;:!?)(")


_NOT_GIT = re.compile(
    r"\b(implement\w*|programe|codifi\w*|escrev\w* (o |um |uma )?(codigo|funcao|classe|teste)"
    r"|corrij\w* (o |um |esse )?bug|conserte|refator\w*|code review|revis\w* (o |meu )?codigo"
    r"|cri\w* (a |uma )?(tela|pagina|funcao|classe|componente|api|endpoint))\b"
)

_STOPWORDS = {
    "a",
    "o",
    "as",
    "os",
    "de",
    "do",
    "da",
    "para",
    "pra",
    "pro",
    "com",
    "em",
    "branch",
    "uma",
    "um",
    "nova",
    "novo",
    "the",
    "to",
    "new",
    "minha",
    "meu",
    "na",
    "no",
    "essa",
    "esse",
    "isso",
}


def _first_name_after(pattern: str, original: str) -> str | None:
    match = re.search(pattern + r"\s+" + _NAME, original, flags=re.IGNORECASE)
    if not match:
        return None
    for candidate in re.findall(_NAME, original[match.start(1) :]):
        name = _clean_name(candidate)
        if name and fold(name) not in _STOPWORDS:
            return name
    return None


def _in_progress_kind(state: RepoState | None, text: str) -> str | None:
    for kind in ("cherry-pick", "rebase", "merge", "revert"):
        if kind.replace("-", "") in text.replace("-", "") or kind in text:
            return kind
    return state.in_progress if state and state.in_progress != "bisect" else None


Rule = Callable[[str, str, RepoState | None], Interpretation | None]


def _rule_not_git(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if _NOT_GIT.search(t) and "commit" not in t and "branch" not in t:
        return Interpretation(
            "answer",
            reply="O GRIPPO só executa operações Git: ele não implementa nem corrige código. "
            "Quando terminar a implementação, posso criar a branch, o commit e o push.",
            fast=True,
        )
    return None


def _rule_sequencer(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    action = None
    if re.search(r"\b(abort\w*|cancel\w*)\b", t):
        action = "abort"
    elif re.search(r"\b(continu\w*|prossig\w*|--continue)\b", t):
        action = "continue"
    elif re.search(r"\b(pul(e|ar) (esse |este |o )?commit|--skip|skip)\b", t):
        action = "skip"
    if not action:
        return None
    kind = _in_progress_kind(s, t)
    if not kind:
        return None
    op = f"{kind.replace('-', '_')}_{action}"
    return Interpretation("execute", [(op, {})], reply=f"{kind} {action}.")


def _rule_commit(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if not re.search(r"\bcommit\w*|\bcomita\w*", t) or re.search(
        r"\b(desfa\w*|revert\w*|"
        r"cherry|amend|emend)",
        t,
    ):
        return None
    if "amend" in t or "emend" in t:
        return None
    match = re.search(r"\b(?:commit\w*|comita\w*)\b(.*)", o, flags=re.IGNORECASE)
    request = match.group(1).strip(" .") if match else ""
    request = re.sub(r"^(d[aoe]s?|of|the)\s+", "", request, flags=re.IGNORECASE)
    target = re.search(
        r"\b(?:na|para|pra|into|on|to)\s+(?:a\s+|o\s+)?(?:branch\s+)?([A-Za-z0-9][\w./-]*)\s*[.!]?\s*$",
        o,
        flags=re.IGNORECASE,
    )
    branch = _clean_name(target.group(1)) if target else ""
    if target:
        request = request[: max(0, len(request) - len(target.group(0)))].strip()
    request = re.split(
        r"(?:^|\s+)e\s+(?:fa[cç]a\s+)?(?:o\s+)?(?:push|envi|mand|sub)",
        request,
        flags=re.IGNORECASE,
    )[0].strip()
    commit_args: dict[str, Any] = {"request": request}
    if branch and fold(branch) not in _STOPWORDS:
        commit_args["branch"] = branch
    steps: list[StepSpec] = [("commit_changes", commit_args)]
    if re.search(r"\b(push|envi\w*|mand\w* (pro|para o) remoto|sub(a|ir))\b", t):
        steps.append(("publish", {"branch": branch} if "branch" in commit_args else {}))
    # Commits are planned deterministically (the LLM still drafts files/message).
    return Interpretation("execute", steps, reply="Vou preparar o commit.", fast=True)


def _rule_undo(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if not re.search(r"\bdesfa(c|z)\w*|\bundo\b", t) or "commit" not in t:
        return None
    if re.search(r"sem (apagar|perder|reescrever) (o )?historico|novo commit|revert", t):
        return None  # → revert rule
    discard = bool(re.search(r"descart\w*|apagando|perdendo|sem manter|jogar fora|hard", t))
    return Interpretation(
        "execute",
        [("undo_last_commit", {"keep_changes": not discard})],
        reply="Vou desfazer o último commit.",
    )


def _rule_revert(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if not re.search(r"\brevert\w*|sem (apagar|perder|reescrever) (o )?historico", t):
        return None
    sha = re.search(r"\b" + _SHA + r"\b", t)
    if sha:
        return Interpretation(
            "execute",
            [("revert", {"commit": sha.group(1)})],
            reply=f"Vou reverter {sha.group(1)} com um novo commit.",
        )
    if re.search(r"ultimo commit|last commit|head\b", t):
        return Interpretation(
            "execute",
            [("revert", {"commit": "HEAD"})],
            reply="Vou reverter o último commit com um novo commit.",
        )
    return Interpretation("clarify", question="Qual commit você quer reverter? (hash)")


def _rule_reset(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    match = re.search(r"\breset\b\s*(?:--)?(soft|mixed|hard)?\s*([^\s]+)?", o, re.IGNORECASE)
    if not match:
        return None
    mode = (match.group(1) or "mixed").lower()
    target = match.group(2) if match.group(2) and fold(match.group(2)) not in _STOPWORDS else "HEAD"
    return Interpretation(
        "execute",
        [("reset", {"mode": mode, "target": target})],
        reply=f"reset --{mode} para {target}.",
    )


def _rule_cherry(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if not re.search(r"cherry[- ]?pick", t):
        return None
    shas = re.findall(r"\b" + _SHA + r"\b", t)
    if not shas:
        return Interpretation("clarify", question="Qual commit (hash) devo aplicar?")
    return Interpretation(
        "execute", [("cherry_pick", {"commits": shas})], reply=f"Vou aplicar {', '.join(shas)}."
    )


def _rule_sync(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if not re.search(
        r"\b(atualiz\w*|sincroniz\w*|update|sync)\b.*\b(com|with|a partir d[aoe])\b", t
    ) and not re.search(r"\brebase\b", t):
        return None
    base = _first_name_after(r"\b(?:com|with|sobre|onto|em|d[aoe])\b", o)
    strategy = "rebase" if "rebase" in t else ("merge" if "merge" in t else None)
    if not base:
        return Interpretation("clarify", question="Atualizar com qual branch?")
    args: dict[str, Any] = {"base": base}
    if strategy:
        args["strategy"] = strategy
    return Interpretation("execute", [("sync_branch", args)], reply=f"Atualizar com {base}.")


def _rule_merge(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if not re.search(r"\b(merge|mescl\w*|integr\w*)\b", t):
        return None
    name = _first_name_after(
        r"\b(?:merge|mescl\w*|integr\w*)(?:\s+(?:da|de|do|a|the))?"
        r"(?:\s+branch)?",
        o,
    )
    if not name:
        return Interpretation("clarify", question="Qual branch devo mesclar na atual?")
    return Interpretation("execute", [("merge", {"rev": name})], reply=f"Mesclar {name}.")


def _rule_publish(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if not re.search(r"\b(push|envi\w*|sub(a|ir)|mand\w* (pro|para o) remoto|publi\w*)\b", t):
        return None
    if re.search(r"\b(exclu\w*|delet\w*|apag\w*|remov\w*)\b", t):
        return None
    if "lease" in t:
        return Interpretation(
            "execute", [("push", {"force_with_lease": True})], reply="push --force-with-lease."
        )
    if re.search(r"--force\b|\bforce\b|forcad\w*", t):
        return Interpretation("execute", [("push", {"force": True})], reply="push --force.")
    return Interpretation("execute", [("publish", {})], reply="Vou enviar seus commits.")


def _rule_pull_fetch(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if re.search(r"\b(pull|pux\w*|baix\w* (as )?(alteracoes|atualizacoes))\b", t):
        mode = "rebase" if "rebase" in t else ("merge" if "merge" in t else "ff-only")
        return Interpretation("execute", [("pull", {"mode": mode})], reply="pull.")
    if re.search(r"\b(fetch|busc\w* (as )?atualizacoes)\b", t):
        return Interpretation(
            "execute", [("fetch", {"prune": "prune" in t})], reply="fetch.", fast=True
        )
    return None


def _rule_branch(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if re.search(r"\b(cri\w*|nova|new|create)\b.*\bbranch\b|\bbranch\b.*\b(nova|new)\b", t):
        name = _first_name_after(r"\bbranch(?:\s+(?:chamada|called|named))?", o)
        if not name:
            return Interpretation("clarify", question="Qual nome devo dar à nova branch?")
        return Interpretation(
            "execute",
            [("branch_create", {"name": name, "switch": True})],
            reply=f"Criar a branch {name}.",
        )
    if re.search(r"\brenome\w*|\brename\b", t) and "branch" in t:
        new = _first_name_after(r"\b(?:para|to)\b", o)
        if not new:
            return Interpretation("clarify", question="Qual o novo nome da branch?")
        return Interpretation(
            "execute", [("branch_rename", {"new": new})], reply=f"Renomear para {new}."
        )
    if re.search(r"\b(exclu\w*|delet\w*|apag\w*|remov\w*)\b.*\bbranch\b", t):
        name = _first_name_after(r"\bbranch(?:\s+(?:local|remota))?", o)
        if not name:
            return Interpretation("clarify", question="Qual branch devo excluir?")
        if "remot" in t:
            return Interpretation(
                "execute",
                [("push_delete", {"branch": name})],
                reply=f"Excluir a branch remota {name}.",
            )
        force = bool(re.search(r"\bforc\w*|\bforce\b", t)) or "-D" in o
        return Interpretation(
            "execute",
            [("branch_delete", {"name": name, "force": force})],
            reply=f"Excluir a branch {name}.",
        )
    if re.search(r"\b(mud\w*|troc\w*|altern\w*|switch|checkout|v(a|ai|á) para|ir para)\b", t):
        name = _first_name_after(
            r"\b(?:mud\w*|troc\w*|altern\w*|switch|checkout|para|to)"
            r"(?:\s+(?:para|pra|to))?(?:\s+(?:a|o|the))?(?:\s+branch)?",
            o,
        )
        if not name:
            return Interpretation("clarify", question="Para qual branch?")
        if re.fullmatch(_SHA, name):
            return Interpretation(
                "execute", [("checkout", {"rev": name})], reply=f"checkout {name}."
            )
        return Interpretation(
            "execute", [("switch", {"branch": name})], reply=f"Mudar para {name}."
        )
    if (
        re.search(r"\bbranch(es)?\b", t)
        and not re.search(r"historico|\blog\b", t)
        and re.search(r"\b(list\w*|quais|mostr\w*|show|merged|mesclad\w*|atual|current)\b", t)
    ):
        if re.search(r"\b(atual|current)\b", t) and not re.search(r"\blist", t):
            return Interpretation("execute", [("status", {})], reply="Branch atual:", fast=True)
        args: dict[str, Any] = {"all": "remot" in t or "todas" in t}
        if re.search(r"nao (merged|mesclad)|not merged|no.merged", t):
            args["no_merged"] = "HEAD"
        elif re.search(r"merged|mesclad", t):
            args["merged"] = "HEAD"
        return Interpretation("execute", [("branch_list", args)], reply="Branches:", fast=True)
    return None


def _rule_stash(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if re.search(
        r"stash (pop|apply)|recuper\w* (o )?stash|restaur\w* (o )?stash|aplic\w* (o )?stash", t
    ):
        op = "stash_apply" if "apply" in t or "aplic" in t else "stash_pop"
        return Interpretation("execute", [(op, {})], reply="Restaurar o stash.")
    if re.search(r"list\w* (os )?stash", t):
        return Interpretation("execute", [("stash_list", {})], reply="Stashes:", fast=True)
    if re.search(r"\bstash\b|guard\w* (minhas |as )?alteracoes", t):
        return Interpretation(
            "execute",
            [("stash_push", {"include_untracked": "untracked" in t or "nao rastread" in t})],
            reply="Guardar alterações no stash.",
        )
    return None


def _rule_discard(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if re.search(r"\b(git )?clean\b|limp\w* (os )?arquivos (nao rastreados|untracked)", t):
        return Interpretation(
            "execute",
            [("clean", {"ignored": "-x" in t or "ignorad" in t})],
            reply="Remover arquivos não rastreados.",
        )
    if re.search(
        r"\b(descart\w*|discard|restor\w*|restaur\w*)\b.*\b(alterac\w*|changes|arquivo)", t
    ):
        match = re.search(r"(?:arquivo|file|em|de|do)\s+([\w./\-]+\.\w+)", o, flags=re.IGNORECASE)
        args = {"paths": [match.group(1)]} if match else {}
        return Interpretation(
            "execute", [("discard_changes", args)], reply="Descartar alterações locais."
        )
    return None


def _rule_tags_remotes(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if re.search(r"\b(cri\w*|create)\b.*\btag\b", t):
        name = _first_name_after(r"\btag", o)
        if not name:
            return Interpretation("clarify", question="Qual o nome da tag?")
        return Interpretation("execute", [("tag_create", {"name": name})], reply=f"Tag {name}.")
    if re.search(r"\btags?\b", t):
        return Interpretation("execute", [("tag_list", {})], reply="Tags:", fast=True)
    if re.search(r"\bremot(o|os|e|es)\b", t) and not re.search(r"\b(envi|push|pull)", t):
        return Interpretation("execute", [("remote_list", {})], reply="Remotos:", fast=True)
    return None


def _rule_compare(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if not re.search(r"\b(compar\w*|compare|diferenca entre)\b", t):
        return None
    base = _first_name_after(r"\b(?:com|with|e|and)\b", o)
    if not base:
        return Interpretation("clarify", question="Comparar com qual branch?")
    return Interpretation(
        "execute",
        [("compare", {"base": base}), ("diff", {"base": base, "stat": True})],
        reply=f"Comparação com {base}.",
        fast=True,
    )


def _rule_read(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if re.search(r"\breflog\b", t):
        return Interpretation("execute", [("reflog", {})], reply="Reflog:", fast=True)
    sha = re.search(r"\b(show|mostr\w*|detalh\w*)\b.*\bcommit\b\s*" + _SHA, t)
    if sha:
        return Interpretation(
            "execute", [("show", {"rev": sha.group(2)})], reply="Commit:", fast=True
        )
    if re.search(r"\b(log|historico|history)\b", t):
        return Interpretation("execute", [("log", {"n": 20})], reply="Histórico:", fast=True)
    if re.search(r"\bstaged\b|\bcached\b|na staging", t) and re.search(r"diff|mostr|alterac", t):
        return Interpretation(
            "execute", [("diff", {"staged": True})], reply="Diff staged:", fast=True
        )
    if re.search(r"\bdiff\b|diferenc", t):
        return Interpretation("execute", [("diff", {})], reply="Diff:", fast=True)
    if re.search(
        r"\bstatus\b|pendent\w*|o que (foi )?(mudou|alterado|modificado)|alterac\w*"
        r" (pendentes|atuais|locais)|mostr\w* (o que|as alteracoes)",
        t,
    ):
        return Interpretation(
            "execute", [("status", {}), ("diff", {"stat": True})], reply="Estado atual:", fast=True
        )
    return None


_RULES: tuple[Rule, ...] = (
    _rule_not_git,
    _rule_sequencer,
    _rule_undo,
    _rule_revert,
    _rule_reset,
    _rule_cherry,
    _rule_commit,
    _rule_sync,
    _rule_merge,
    _rule_discard,
    _rule_publish,
    _rule_pull_fetch,
    _rule_stash,
    _rule_branch,
    _rule_compare,
    _rule_tags_remotes,
    _rule_read,
)


def route(message: str, state: RepoState | None = None) -> Interpretation | None:
    text = fold(message)
    for rule in _RULES:
        result = rule(text, message, state)
        if result is not None:
            return result
    return None
