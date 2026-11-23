"""Deterministic intent router (pt-BR / en).

Used as (1) a fast path for unambiguous read-only requests, skipping the LLM, and
(2) the fallback when the LLM is unavailable or answers with something invalid.
It never invents risky flags: destructive variants are chosen only when the user's
words explicitly ask for them, and they still go through confirmation.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from grippo.git.conflicts import unresolved_files
from grippo.git.state import RepoState
from grippo.text import fold

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
    fast: bool = True  # a matching rule is final; set False only for loose matches
    notes: list[str] = field(default_factory=list)


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
    "chamada",
    "chamado",
    "called",
    "named",
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


_CONFLICT_QUESTION = re.compile(
    r"\b(como|porque|por que|pq|expli\w*|entend\w*|ajud\w*|motivo|causa|o que|oque|qual)\b"
)
# Commands (or reports of work already done) that merely mention "conflito".
_CONFLICT_COMMAND = re.compile(
    r"\b(abort\w*|cancel\w*|continu\w*|prossig\w*|conclu\w*|finaliz\w*|resolvi\w*|"
    r"fa[cz]\w*|comit\w*|commit\w*|push|envi\w*)\b"
)


def _rule_explain_conflict(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    """ "como resolvo o conflito?" → explain why it happened (never auto-resolve)."""
    if "conflit" not in t or not _CONFLICT_QUESTION.search(t) or _CONFLICT_COMMAND.search(t):
        return None
    return Interpretation("explain_conflict", reply="", fast=True)


def _rule_sequencer(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    action = None
    if re.search(r"\b(abort\w*|cancel\w*|desist\w*)\b", t):
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
    staging = action == "continue" and s is not None and bool(s.conflicted)
    unresolved = unresolved_files(s.root, s.conflicted) if staging and s else []
    if unresolved:
        return Interpretation(
            "clarify",
            question="Ainda há marcadores de conflito (<<<<<<<, =======, >>>>>>>) em: "
            + ", ".join(unresolved)
            + ". Resolva-os no editor (ou peça ao Claude Code) e peça de novo.",
        )
    steps: list[StepSpec] = [(op, {})]
    if staging and s:  # resolved files must be staged before --continue
        steps.insert(0, ("add", {"paths": list(s.conflicted)}))
    return Interpretation("execute", steps, reply=f"{kind} {action}.")


_READ_WORDS = re.compile(
    r"\b(mostr\w*|show|histori\w*|history|log|detalh\w*|lista\w*|qual|quais|diff|"
    r"diferenc\w*|ultim\w*|last|tag\w*)\b"
)
_CREATE_VERB = re.compile(
    r"\b(faca|faz|fazer|faze|crie|cria|criar|grave|grava|gravar|registre|registra|registrar"
    r"|envie|envia|enviar|mande|manda|mandar|salve|salva|salvar|prepar(e|a|ar)"
    r"|comm?it(e|a|ar|em))\b"
    # "git add + commit", "commit e push": the commit is part of a sequence of actions.
    r"|\badd\b.*\bcommit|\bcommit\w*\s+e\s+(push|envi|mand|sub)"
)
_ALL_CHANGES = re.compile(r"\b(tudo|todas?|todos|everything|all)\b")


def _rule_unpushed(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    """'quais commits eu ainda não enviei': commits ahead of the upstream (read-only)."""
    if not re.search(
        r"\bcommits?\b.*\b(nao|ainda nao)\s+(\w+\s+)?(envi\w*|sub\w*|push\w*|mand\w*|publi\w*)"
        r"|\b(unpushed|not pushed)\b",
        t,
    ):
        return None
    return Interpretation(
        "execute", [("log", {"rev": "@{u}..HEAD", "n": 100})], reply="Commits ainda não enviados:"
    )


def _rule_back_to_commit(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    """'volta tudo pro commit abc1234' is ambiguous (look vs move the branch): ask."""
    sha = re.search(r"\b" + _SHA + r"\b", t)
    if not sha or not re.search(r"\b(volt\w*|retorn\w*|go back|back to)\b", t):
        return None
    return Interpretation(
        "clarify",
        question=f"Voltar para {sha.group(1)} de que jeito? (1) só olhar o código desse commit "
        f'("faça checkout {sha.group(1)}", não perde nada); (2) mover a branch para ele e '
        f'manter as alterações ("reset --soft {sha.group(1)}"); (3) mover e DESCARTAR o que '
        f'veio depois ("reset --hard {sha.group(1)}", destrutivo).',
    )


def _rule_commit(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if not re.search(r"\bcommit\w*|\bcomita\w*", t) or re.search(
        r"\b(desfa\w*|revert\w*|"
        r"cherry|amend|emend)",
        t,
    ):
        return None
    # A question about commits ("mostre o último commit") is not a request to commit.
    creates = _CREATE_VERB.search(t) or re.match(r"\s*comm?it\w*\b", t)
    if not creates:
        # "commit" without a verb that creates one ("volta pro commit anterior", "mostre o
        # último commit") is a reference to a commit, not a request to make one.
        return None
    if re.search(r"\btag\b", t) or (re.search(r"\b" + _SHA + r"\b", t) and not creates):
        return None  # "tag nesse commit", "volta pro commit abc1234": not a new commit
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
    if _ALL_CHANGES.search(t) and not _ALL_CHANGES.search(fold(request)):
        request = (request + " tudo").strip()  # "salva tudo num commit": keep the "tudo"
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
    o = re.sub(r"\bem cima d[aoe]s?\b", "sobre", o, flags=re.IGNORECASE)
    base = _first_name_after(r"\b(?:com|with|sobre|onto|em|na|no|d[aoe])\b", o)
    strategy = "rebase" if "rebase" in t else ("merge" if "merge" in t else None)
    if not base:
        return Interpretation("clarify", question="Atualizar com qual branch?")
    args: dict[str, Any] = {"base": base}
    if strategy:
        args["strategy"] = strategy
    return Interpretation(
        "execute", [("sync_branch", args)], reply=f"Atualizar com {base}.", fast=True
    )


_TARGET_AT_END = re.compile(
    r"\b(?:na|no|em|into|on|para|pra|to)\s+(?:a\s+|o\s+)?(?:branch\s+)?"
    r"([A-Za-z0-9][\w./-]*)\s*[.!?]?\s*$",
    flags=re.IGNORECASE,
)


def _rule_merge(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if not re.search(r"\b(merge|mescl\w*|integr\w*|junt(e|a|ar))\b", t):
        return None
    name = _first_name_after(
        r"\b(?:merge|mescl\w*|integr\w*|junt(?:e|a|ar))(?:\s+(?:da|de|do|a|the))?"
        r"(?:\s+branch)?",
        o,
    )
    if not name:
        return Interpretation("clarify", question="Qual branch devo mesclar na atual?")
    # "faça merge da develop na main": the destination is part of the request. When it is
    # not the current branch, switch first (the plan re-checks the merge after the switch).
    steps: list[StepSpec] = []
    target = _TARGET_AT_END.search(o)
    dest = _clean_name(target.group(1)) if target else ""
    current = s.branch if s else None
    if dest and fold(dest) not in _STOPWORDS and dest not in {name, current}:
        steps.append(("switch", {"branch": dest}))
        reply = f"Mudar para {dest} e mesclar {name}."
    else:
        reply = f"Mesclar {name}."
    steps.append(("merge", {"rev": name}))
    return Interpretation("execute", steps, reply=reply, fast=True)


_PR_TERM = re.compile(r"\b(pull request\w*|merge request\w*|prs?)\b")
_PR_CREATE = re.compile(r"\b(abrir|abra|abre|abro|crie|criar|cria|crio|create|open)\b")


def _rule_pr(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    """Pull requests live on GitHub: GRIPPO reads them (via gh) but never runs `pull`."""
    if not _PR_TERM.search(t) or re.search(r"\bcommit\w*|\bpush\b|\bpublic\w*", t):
        return None
    if _PR_CREATE.search(t):
        return Interpretation(
            "answer",
            reply="O GRIPPO ainda não cria pull requests. Crie pelo GitHub ou com "
            "`gh pr create`; eu posso listar os PRs abertos com você.",
            fast=True,
        )
    target = _TARGET_AT_END.search(o)
    branch = _clean_name(target.group(1)) if target else ""
    return Interpretation(
        "execute",
        [("pr_list", {"branch": branch})],
        reply="Consultando pull requests.",
        fast=True,
    )


def _rule_publish(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if not re.search(
        r"\b(push|envie|envia|enviar|sob(e|a)|sub(a|ir|o)|mand\w* (pro|para o) remoto|publi\w*)\b",
        t,
    ):
        return None
    if re.search(r"\b(exclu\w*|delet\w*|apag\w*|remov\w*)\b", t):
        return None
    if "lease" in t:
        return Interpretation(
            "execute",
            [("push", {"force_with_lease": True})],
            reply="push --force-with-lease.",
            fast=True,
        )
    if re.search(r"--force\b|\bforce\b|forcad\w*", t):
        return Interpretation(
            "execute", [("push", {"force": True})], reply="push --force.", fast=True
        )
    return Interpretation("execute", [("publish", {})], reply="Vou enviar seus commits.", fast=True)


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
    if re.search(
        r"\b(mud(ar|e|a|o)|troc\w*|altern\w*|switch|checkout|v(a|ai|á|olt\w*) (para|pra)"
        r"|ir (para|pra)|volt\w* (para|pra|a)|retorn\w* (para|pra|a)|go to)\b",
        t,
    ):
        name = _first_name_after(
            r"\b(?:mud\w*|troc\w*|altern\w*|switch|checkout|para|pra|to|volt\w*|retorn\w*)"
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
    if re.search(
        r"\bstash\b|(guard\w*|escond\w*|reserv\w*) (minhas |as |meus |os )?"
        r"(alteracoes|mudancas|modificacoes)",
        t,
    ):
        return Interpretation(
            "execute",
            [("stash_push", {"include_untracked": "untracked" in t or "nao rastread" in t})],
            reply="Guardar alterações no stash.",
        )
    return None


_FILE = re.compile(r"(?<![\w/.])((?:[\w\-]+/)*(?:[\w\-]+\.[A-Za-z0-9][\w.]*|\.[\w\-][\w.\-]*))")


def _rule_refuse_broad(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    """'apague tudo do repositório': GRIPPO never wipes a repository or its history."""
    # "sem apagar o histórico" is a safety clause, not a request to erase anything.
    if re.search(r"\bsem\b", t):
        return None
    if re.search(
        r"\b(apag\w*|delet\w*|exclu\w*|remov\w*)\b.*\b(tudo|todo|todos|repositorio|repo"
        r"|historico|projeto|git)\b",
        t,
    ) and not (
        re.search(r"\b(branch|arquivo|file|remoto|tag|stash)\b", t)
        or _FILE.search(o)  # a named file: "remova config.json do git", ".env"
    ):
        return Interpretation(
            "answer",
            reply="O GRIPPO não apaga o repositório nem o histórico inteiro. Se quiser, "
            "descarto alterações de arquivos específicos, desfaço um commit ou excluo uma "
            "branch (nomeada).",
            fast=True,
        )
    return None


def _rule_unstage(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    """'desfaça o add do x.txt': take a file out of the index, keep its content."""
    if not re.search(
        r"\bunstage\b|\b(desfa\w*|tir\w*|remov\w*)\b.*\b(add|stage|staging|indice)\b", t
    ):
        return None
    match = _FILE.search(o) or re.search(
        r"\b(?:arquivo|file|d[aoe])\s+([\w./\-]+)\s*[.!?]?\s*$", o, flags=re.IGNORECASE
    )
    if not match:
        return Interpretation("clarify", question="Qual arquivo devo tirar do stage?")
    path = match.group(1)
    if s is not None and path not in s.staged:
        # "do README" → README.md when exactly one staged file has that name.
        named = [f for f in s.staged if f.rsplit("/", 1)[-1].lower().startswith(path.lower())]
        if len(named) == 1:
            path = named[0]
    return Interpretation(
        "execute",
        [("unstage", {"paths": [path]})],
        reply=f"Tirar {path} do stage (o conteúdo continua no disco).",
    )


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


_URL = re.compile(r"(https?://\S+|ssh://\S+|git@\S+)")
_VERSION = re.compile(r"\b(v\d+(?:\.\d+)*[\w.\-]*)")


def _rule_tags_remotes(t: str, o: str, s: RepoState | None) -> Interpretation | None:
    if (
        re.search(r"\btag\b", t)
        and re.search(r"\b(cri\w*|create|marc\w*|tag this|tag o|tag a)\b", t)
        and not re.search(r"\b(list\w*|quais|mostr\w*|show)\b", t)
    ):
        named = re.search(r"\bas\s+([\w./-]+)", o, flags=re.IGNORECASE)
        version = _VERSION.search(o)
        name = named.group(1) if named else (version.group(1) if version else "")
        name = _clean_name(name) or (
            _first_name_after(r"\btag(?:\s+(?:chamada|chamado|called|named))?", o) or ""
        )
        if not name:
            return Interpretation("clarify", question="Qual o nome da tag?")
        return Interpretation("execute", [("tag_create", {"name": name})], reply=f"Tag {name}.")
    if re.search(r"\b(cri\w*|create)\b.*\btag\b", t):
        name = _first_name_after(r"\btag", o)
        if not name:
            return Interpretation("clarify", question="Qual o nome da tag?")
        return Interpretation("execute", [("tag_create", {"name": name})], reply=f"Tag {name}.")
    if re.search(r"\btags?\b", t):
        return Interpretation("execute", [("tag_list", {})], reply="Tags:", fast=True)
    if re.search(r"\b(adicion\w*|add|configur\w*|cri\w*)\b.*\bremot\w*", t):
        url = _URL.search(o)
        name = _first_name_after(r"\bremot\w*", o)
        if name and url and url.group(1).startswith(name):
            name = None  # "adicione um remoto https://…": the URL is not a name
        if not name or not url:
            return Interpretation(
                "clarify",
                question='Informe o nome e a URL do remoto, ex.: "adicione o remoto origin '
                'https://…".',
            )
        return Interpretation(
            "execute",
            [("remote_add", {"name": name, "url": _clean_name(url.group(1))})],
            reply=f"Adicionar o remoto {name}.",
        )
    if (
        re.search(r"\bremot(o|os|e|es)\b", t)
        and re.search(r"\b(list\w*|quais|qual|mostr\w*|ver|veja|show|configurad\w*)\b", t)
        and not re.search(r"\b(envi|push|pull|sob|sub|mand)", t)
    ):
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
    if re.search(
        r"\b(ultim\w*|last|atual)\s+commit\b|\bcommit (atual|ultimo|mais recente)\b", t
    ) and re.search(r"\b(mostr\w*|show|detalh\w*|ver|veja|qual|exib\w*)\b", t):
        return Interpretation(
            "execute", [("show", {"rev": "HEAD"})], reply="Último commit:", fast=True
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
        r" (pendentes|atuais|locais)|mostr\w* (o que|as alteracoes)"
        r"|quais (arquivos|alteracoes|mudancas)|arquivos? (mudad|alterad|modificad)\w*",
        t,
    ):
        return Interpretation(
            "execute", [("status", {}), ("diff", {"stat": True})], reply="Estado atual:", fast=True
        )
    return None


_RULES: tuple[Rule, ...] = (
    _rule_not_git,
    _rule_refuse_broad,
    _rule_explain_conflict,
    _rule_sequencer,
    _rule_pr,
    _rule_undo,
    _rule_revert,
    _rule_reset,
    _rule_cherry,
    _rule_unpushed,
    _rule_back_to_commit,
    _rule_commit,
    _rule_sync,
    _rule_merge,
    _rule_unstage,
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
