"""Anti-hallucination guard: destructive operations must be *grounded* in what the user said.

The LLM (or an external AI client talking to GRIPPO over MCP) only ever *proposes* a plan.
For operations that rewrite history or destroy data, every target they name — a branch,
a commit, a tag, a path — must appear in the user's own request, and "forcing" flags need
explicit wording. Otherwise the plan is refused *before* it reaches the confirmation
screen, so a model that invents ``branch_delete feature/old`` never gets to ask.

This is a second line of defence: the permission manager, the human confirmation and the
automatic backup still apply afterwards.
"""

from __future__ import annotations

import re
import unicodedata

from grippo.engine.plan import Plan, Step

# op -> argument names that identify *what is being affected*.
TARGET_ARGS: dict[str, tuple[str, ...]] = {
    "rebase": ("upstream", "onto"),
    "revert": ("commit",),
    "reset": ("target",),
    "branch_delete": ("name",),
    "push_delete": ("branch",),
    "tag_delete": ("name",),
    "tag_delete_remote": ("tag",),
    "cherry_pick": ("commits",),
    "clean": ("paths",),
    "restore": ("paths",),
    "rm": ("paths",),
}

# op -> {condition: words (accent-folded) of which at least one must be in the request}.
# Condition: "arg" (truthy), "arg=value", or "*" (the operation itself).
FORCE_WORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "reset": {"mode=hard": ("hard", "descart", "forc", "destru", "perder", "perda")},
    "branch_delete": {"force": ("forc", "force", "nao mesclad", "sem merge", "mesmo assim")},
    "push": {"force": ("forc", "force"), "force_with_lease": ("forc", "force", "lease")},
    "clean": {
        "*": ("limp", "clean", "apag", "remov", "delet", "exclu", "nao rastread", "untracked"),
        "ignored": ("ignorad", "gitignore", "-x", "tudo", "all", "build", ".env"),
    },
    "stash_drop": {"*": ("stash",)},
}

# Revisions that can be implied by phrases like "o último commit".
_IMPLICIT = re.compile(r"^(HEAD|@)([~^]\d*)*$")
_IMPLICIT_WORDS = ("ultimo", "ultima", "anterior", "atual", "head", "last", "previous", "current")
_COUNT = re.compile(r"HEAD~(\d+)$")


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _values(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _mentioned(value: str, folded_request: str) -> bool:
    needle = _fold(value).strip()
    if not needle:
        return True
    candidates = {needle, needle.rstrip("/")}
    if "/" in needle:  # origin/main is grounded by "main"; feature/x by "feature/x" only
        remote_part = needle.split("/", 1)
        if remote_part[0] in {"origin", "upstream"}:
            candidates.add(remote_part[1])
    for candidate in candidates:
        pattern = rf"(?<![\w./-]){re.escape(candidate)}(?![\w-])"
        if re.search(pattern, folded_request):
            return True
        if re.fullmatch(r"[0-9a-f]{7,40}", candidate):  # sha typed shorter/longer
            for token in re.findall(r"[0-9a-f]{7,40}", folded_request):
                if candidate.startswith(token) or token.startswith(candidate):
                    return True
    return False


def _implicit_ok(value: str, folded_request: str) -> bool:
    if not _IMPLICIT.fullmatch(value):
        return False
    count = _COUNT.fullmatch(value)
    if count and int(count.group(1)) > 5:
        return False
    return any(word in folded_request for word in _IMPLICIT_WORDS) or value in folded_request


def grounding_problems(plan: Plan, user_request: str) -> list[str]:
    """Human-readable reasons the plan is *not* backed by ``user_request`` (empty = OK)."""
    request = _fold(user_request)
    problems: list[str] = []
    for step in plan.steps:
        for arg in TARGET_ARGS.get(step.op, ()):
            if arg not in step.args:
                continue
            for value in _values(step.args[arg]):
                if value == "." and step.op in {"restore", "clean", "rm"}:
                    problems.append(
                        f"{step.op} em '.' (tudo) exige que o pedido diga isso explicitamente"
                    )
                    continue
                if _mentioned(value, request) or _implicit_ok(value, request):
                    continue
                problems.append(
                    f"{step.op}: '{value}' ({arg}) não aparece no seu pedido — "
                    "não vou assumir um alvo que você não citou"
                )
        for spec, words in FORCE_WORDS.get(step.op, {}).items():
            key, _, wanted = spec.partition("=")
            actual = step.args.get(key)
            active = key == "*" or ((str(actual) == wanted) if wanted else bool(actual))
            if active and not any(word in request + " " for word in words):
                problems.append(
                    f"{step.op}: o pedido não autoriza explicitamente "
                    + (
                        "esta operação"
                        if key == "*"
                        else f"a opção destrutiva {spec if wanted else key}"
                    )
                )
        problems += _stash_problems(step, request)
    return problems


def _stash_problems(step: Step, request: str) -> list[str]:
    """Dropping a stash other than the newest one requires naming its number."""
    index = step.args.get("index")
    if step.op != "stash_drop" or not index:
        return []
    if re.search(rf"(?<!\d){index}(?!\d)", request):
        return []
    return [f"stash_drop: o stash número {index} não aparece no seu pedido"]
